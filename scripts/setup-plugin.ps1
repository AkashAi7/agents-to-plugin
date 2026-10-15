<#
.SYNOPSIS
Install or remove an agents-to-plugin marketplace plugin in the local Copilot CLI.

.DESCRIPTION
Defaults are derived from the current repository's generated manifests
(.github/plugin/marketplace.json) and git origin remote, so the script always
targets the plugin this repository actually publishes. All values can be
overridden explicitly.
#>
[CmdletBinding()]
param(
    [ValidateSet("Install", "Remove")]
    [string]$Action = "Install",
    [string]$Repo,
    [string]$MarketplaceName,
    [string]$PluginName
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command copilot -ErrorAction SilentlyContinue)) {
    throw "GitHub Copilot CLI is required. Install it, sign in, and rerun this script."
}

# Derive defaults from the repository's own generated manifests.
$marketplaceManifest = Join-Path (Get-Location) ".github\plugin\marketplace.json"
if ((-not $MarketplaceName -or -not $PluginName) -and (Test-Path $marketplaceManifest)) {
    $manifest = Get-Content -Raw $marketplaceManifest | ConvertFrom-Json
    if (-not $MarketplaceName) { $MarketplaceName = $manifest.name }
    if (-not $PluginName -and $manifest.plugins) { $PluginName = $manifest.plugins[0].name }
}
if (-not $Repo) {
    $remoteUrl = git remote get-url origin 2>$null
    if ($LASTEXITCODE -eq 0 -and $remoteUrl -match "github\.com[:/]+([^/]+)/([^/]+?)(\.git)?$") {
        $Repo = "$($Matches[1])/$($Matches[2])"
    }
}
foreach ($required in @(
        @{ Name = "Repo"; Value = $Repo },
        @{ Name = "MarketplaceName"; Value = $MarketplaceName },
        @{ Name = "PluginName"; Value = $PluginName }
    )) {
    if (-not $required.Value) {
        throw "Could not derive -$($required.Name); run from a generated repository or pass it explicitly."
    }
}

$qualified = "$PluginName@$MarketplaceName"

function Invoke-Copilot {
    param([Parameter(Mandatory)][string[]]$Arguments)
    & copilot @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: copilot $($Arguments -join ' ')"
    }
}

function Test-CliEntry {
    param([string]$Output, [string]$Name)
    # Exact-token match: avoids one plugin name matching inside another.
    return $Output -match "(?m)(^|[\s•◆])$([regex]::Escape($Name))([\s(]|$)"
}

if ($Action -eq "Remove") {
    if (Test-CliEntry -Output ((& copilot plugin list 2>&1 | Out-String)) -Name $qualified) {
        Invoke-Copilot -Arguments @("plugin", "uninstall", $qualified)
    }
    if (Test-CliEntry -Output ((& copilot plugin marketplace list 2>&1 | Out-String)) -Name $MarketplaceName) {
        Invoke-Copilot -Arguments @("plugin", "marketplace", "remove", $MarketplaceName)
    }
    Write-Host "Removed $qualified and its marketplace."
    return
}

if (-not (Test-CliEntry -Output ((& copilot plugin marketplace list 2>&1 | Out-String)) -Name $MarketplaceName)) {
    Invoke-Copilot -Arguments @("plugin", "marketplace", "add", $Repo)
}
if (-not (Test-CliEntry -Output ((& copilot plugin list 2>&1 | Out-String)) -Name $qualified)) {
    Invoke-Copilot -Arguments @("plugin", "install", $qualified)
}
Invoke-Copilot -Arguments @("plugin", "enable", $qualified)

$verified = (& copilot plugin list 2>&1 | Out-String)
if (-not (Test-CliEntry -Output $verified -Name $qualified) -or
    $verified -match "(?m)^.*$([regex]::Escape($qualified)).*\[disabled\].*$") {
    throw "$qualified was not reported as enabled after setup."
}
Write-Host "$qualified is installed and enabled."
