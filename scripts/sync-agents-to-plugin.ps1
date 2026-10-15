<#
.SYNOPSIS
Universal agents-to-plugin converter.

.DESCRIPTION
Scans the agents/ directory for agent definitions and (re)generates the plugin
marketplace setup from them:
  1. Creates .github/plugin/marketplace.json if it does not exist, otherwise updates it.
  2. Creates plugin.json if it does not exist, otherwise updates it.
  3. Mirrors agent files into com.github.copilot/agents/ (the Agent Plugins namespace path).

The script is idempotent and scale-ready: add, amend, or delete agent files under
agents/ and re-run it to repopulate the whole plugin setup.

.PARAMETER Install
Also run scripts/setup-plugin.ps1 afterwards to register/enable the plugin locally.
#>
[CmdletBinding()]
param(
    [string]$MarketplaceName,
    [string]$PluginName,
    [switch]$Install
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$agentsDir = Join-Path $repoRoot "agents"
$namespaceAgentsDir = Join-Path $repoRoot "com.github.copilot\agents"
$marketplacePath = Join-Path $repoRoot ".github\plugin\marketplace.json"
$pluginManifestPath = Join-Path $repoRoot "plugin.json"

if (-not (Test-Path $agentsDir)) {
    throw "No agents directory found at $agentsDir. Nothing to convert."
}

# --- Resolve repository slug (owner/repo) from git remote ---
$repoSlug = $null
Push-Location $repoRoot
try {
    $remoteUrl = git remote get-url origin 2>$null
    if ($LASTEXITCODE -eq 0 -and $remoteUrl -match "github\.com[:/]+([^/]+)/([^/]+?)(\.git)?$") {
        $repoSlug = "$($Matches[1])/$($Matches[2])"
    }
}
finally {
    Pop-Location
}
if (-not $repoSlug) {
    throw "Could not determine the GitHub repository from the 'origin' remote."
}
$repoOwner = ($repoSlug -split "/")[0]
$repoUrl = "https://github.com/$repoSlug"

if (-not $MarketplaceName) { $MarketplaceName = "$repoOwner-marketplace" }
if (-not $PluginName) { $PluginName = "$repoOwner-agents" }

# --- Discover agents: Markdown files in agents/ with YAML frontmatter ---
function Get-AgentMetadata {
    param([System.IO.FileInfo]$File)

    $lines = Get-Content -Path $File.FullName
    $firstContent = ($lines | Where-Object { $_.Trim() } | Select-Object -First 1)
    if ($firstContent -ne "---") {
        return $null  # commented-out templates and plain docs are skipped
    }

    $meta = [ordered]@{ File = $File; Name = $File.BaseName -replace "\.agent$", ""; Description = "" }
    $inFrontmatter = $false
    foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if ($trimmed -eq "---") {
            if ($inFrontmatter) { break }
            $inFrontmatter = $true
            continue
        }
        if ($inFrontmatter -and $trimmed -match '^(name|description):\s*(.+)$') {
            $meta[(Get-Culture).TextInfo.ToTitleCase($Matches[1])] = $Matches[2].Trim('"', "'")
        }
    }
    return [pscustomobject]$meta
}

$agents = @(
    Get-ChildItem -Path $agentsDir -Filter "*.md" -File |
        Sort-Object Name |
        ForEach-Object { Get-AgentMetadata -File $_ } |
        Where-Object { $_ }
)

if ($agents.Count -eq 0) {
    throw "No agent definitions with YAML frontmatter found in $agentsDir."
}

$sourcePaths = @($agents | ForEach-Object { "./agents/$($_.File.Name)" })
$namespacePaths = @($agents | ForEach-Object { "./com.github.copilot/agents/$($_.File.Name)" })
$agentSummary = ($agents | ForEach-Object { $_.Name }) -join ", "
$pluginDescription = "Custom agents for $repoOwner, auto-generated from the agents/ directory. Includes: $agentSummary."

# --- 1. Mirror agent files into the com.github.copilot namespace directory ---
New-Item -ItemType Directory -Path $namespaceAgentsDir -Force | Out-Null
foreach ($agent in $agents) {
    Copy-Item -Path $agent.File.FullName -Destination (Join-Path $namespaceAgentsDir $agent.File.Name) -Force
}
# Remove mirrored copies whose source agent no longer exists (managed files only).
$sourceNames = @($agents | ForEach-Object { $_.File.Name })
Get-ChildItem -Path $namespaceAgentsDir -Filter "*.md" -File | ForEach-Object {
    $mirrorName = $_.Name
    $sourceFile = Join-Path $agentsDir $mirrorName
    if ($sourceNames -notcontains $mirrorName -and -not (Test-Path $sourceFile)) {
        Remove-Item $_.FullName
        Write-Host "Removed stale mirrored agent: $mirrorName"
    }
}

# --- 2. Create or update plugin.json ---
if (Test-Path $pluginManifestPath) {
    $manifest = Get-Content -Raw $pluginManifestPath | ConvertFrom-Json
    $PluginName = $manifest.name
} else {
    $manifest = [pscustomobject][ordered]@{
        '$schema'   = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
        name        = $PluginName
        version     = "1.0.0"
        description = $pluginDescription
        repository  = $repoUrl
        homepage    = "$repoUrl#readme"
        license     = "MIT"
        keywords    = @("copilot", "custom-agents", $repoOwner)
        extensions  = [pscustomobject]@{}
    }
}
if (-not $manifest.PSObject.Properties["extensions"]) {
    $manifest | Add-Member -NotePropertyName extensions -NotePropertyValue ([pscustomobject]@{})
}
foreach ($pair in @(
        @{ Namespace = "com.github.copilot"; Paths = $namespacePaths },
        @{ Namespace = "com.github.awesome-copilot"; Paths = $sourcePaths }
    )) {
    $existing = $manifest.extensions.PSObject.Properties[$pair.Namespace]
    if ($existing) {
        $existing.Value | Add-Member -NotePropertyName agents -NotePropertyValue $pair.Paths -Force
    } else {
        $manifest.extensions | Add-Member -NotePropertyName $pair.Namespace -NotePropertyValue ([pscustomobject]@{ agents = $pair.Paths })
    }
}
$manifest | ConvertTo-Json -Depth 10 | Set-Content -Path $pluginManifestPath -Encoding utf8

# --- 3. Create or update .github/plugin/marketplace.json ---
if (Test-Path $marketplacePath) {
    $marketplace = Get-Content -Raw $marketplacePath | ConvertFrom-Json
    $MarketplaceName = $marketplace.name
} else {
    New-Item -ItemType Directory -Path (Split-Path -Parent $marketplacePath) -Force | Out-Null
    $marketplace = [pscustomobject][ordered]@{
        name     = $MarketplaceName
        owner    = [pscustomobject]@{ name = $repoOwner }
        metadata = [pscustomobject][ordered]@{
            description = "Agent plugin marketplace for $repoOwner, auto-generated from the agents/ directory."
            version     = "1.0.0"
        }
        plugins  = @()
    }
}
$pluginEntry = @($marketplace.plugins) | Where-Object { $_.name -eq $PluginName } | Select-Object -First 1
if (-not $pluginEntry) {
    $pluginEntry = [pscustomobject][ordered]@{
        name        = $PluginName
        description = $pluginDescription
        version     = "1.0.0"
        repository  = $repoUrl
        source      = "./"
        agents      = @()
    }
    $marketplace.plugins = @($marketplace.plugins) + $pluginEntry
}
$pluginEntry | Add-Member -NotePropertyName agents -NotePropertyValue $sourcePaths -Force
$marketplace | ConvertTo-Json -Depth 10 | Set-Content -Path $marketplacePath -Encoding utf8

# --- 3b. Populate enterprise distribution (copilot/managed-settings.json) ---
$settingsPath = Join-Path $repoRoot "copilot\managed-settings.json"
if (Test-Path $settingsPath) {
    $settings = Get-Content -Raw $settingsPath | ConvertFrom-Json
} else {
    New-Item -ItemType Directory -Path (Split-Path -Parent $settingsPath) -Force | Out-Null
    $settings = [pscustomobject]@{}
}
foreach ($section in @("extraKnownMarketplaces", "enabledPlugins")) {
    if (-not $settings.PSObject.Properties[$section]) {
        $settings | Add-Member -NotePropertyName $section -NotePropertyValue ([pscustomobject]@{})
    }
}
$settings.extraKnownMarketplaces | Add-Member -NotePropertyName $MarketplaceName -NotePropertyValue ([pscustomobject]@{
    source = [pscustomobject]@{ source = "github"; repo = $repoSlug }
    autoUpdate = $true
}) -Force
$settings.enabledPlugins | Add-Member -NotePropertyName "$PluginName@$MarketplaceName" -NotePropertyValue $true -Force
$settings | ConvertTo-Json -Depth 10 | Set-Content -Path $settingsPath -Encoding utf8

# --- 4. Validate generated files and referenced agent paths ---
foreach ($file in @($marketplacePath, $pluginManifestPath)) {
    Get-Content -Raw $file | ConvertFrom-Json | Out-Null
}
foreach ($relativePath in ($sourcePaths + $namespacePaths)) {
    $fullPath = Join-Path $repoRoot ($relativePath -replace "^\./", "" -replace "/", "\")
    if (-not (Test-Path $fullPath)) {
        throw "Generated manifest references a missing file: $relativePath"
    }
}

Write-Host "Marketplace : $MarketplaceName ($marketplacePath)"
Write-Host "Plugin      : $PluginName ($pluginManifestPath)"
Write-Host "Agents ($($agents.Count))  : $agentSummary"
Write-Host "Managed settings: $settingsPath"

if ($Install) {
    & (Join-Path $PSScriptRoot "setup-plugin.ps1")
}
