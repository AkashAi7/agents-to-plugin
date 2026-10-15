[CmdletBinding()]
param(
    [ValidateSet("Install", "Remove")]
    [string]$Action = "Install"
)

$ErrorActionPreference = "Stop"

$marketplaceName = "copilot-kairos-marketplace"
$marketplaceSource = "copilot-kairos/.github-private"
$pluginName = "copilot-kairos-agents@$marketplaceName"

if (-not (Get-Command copilot -ErrorAction SilentlyContinue)) {
    throw "GitHub Copilot CLI is required. Install it, sign in, and rerun this script."
}

function Invoke-Copilot {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments
    )

    & copilot @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: copilot $($Arguments -join ' ')"
    }
}

if ($Action -eq "Remove") {
    $installedPlugins = (& copilot plugin list 2>&1 | Out-String)
    if ($installedPlugins -match [regex]::Escape($pluginName)) {
        Invoke-Copilot -Arguments @("plugin", "uninstall", $pluginName)
    }

    $registeredMarketplaces = (& copilot plugin marketplace list 2>&1 | Out-String)
    if ($registeredMarketplaces -match [regex]::Escape($marketplaceName)) {
        Invoke-Copilot -Arguments @("plugin", "marketplace", "remove", $marketplaceName)
    }

    Write-Host "Removed $pluginName and its marketplace."
    return
}

$registeredMarketplaces = (& copilot plugin marketplace list 2>&1 | Out-String)
if ($registeredMarketplaces -notmatch [regex]::Escape($marketplaceName)) {
    Invoke-Copilot -Arguments @("plugin", "marketplace", "add", $marketplaceSource)
}

$installedPlugins = (& copilot plugin list 2>&1 | Out-String)
if ($installedPlugins -notmatch [regex]::Escape($pluginName)) {
    Invoke-Copilot -Arguments @("plugin", "install", $pluginName)
}

Invoke-Copilot -Arguments @("plugin", "enable", $pluginName)

$verifiedPlugins = (& copilot plugin list 2>&1 | Out-String)
if (
    $verifiedPlugins -notmatch [regex]::Escape($pluginName) -or
    $verifiedPlugins -match "(?m)^.*$([regex]::Escape($pluginName)).*\[disabled\].*$"
) {
    throw "$pluginName was not reported as enabled after setup."
}

Write-Host "$pluginName is installed and enabled."
