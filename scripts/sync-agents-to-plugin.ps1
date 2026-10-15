<#
.SYNOPSIS
Thin PowerShell launcher for the canonical Python converter.

.DESCRIPTION
All conversion logic lives in sync-agents-to-plugin.py (single source of truth,
covered by the regression test suite). This wrapper only locates Python and
forwards arguments, guaranteeing identical behavior on every platform.
#>
[CmdletBinding()]
param(
    [string]$Root,
    [string]$MarketplaceName,
    [string]$PluginName,
    [string]$Version,
    [switch]$Install
)

$ErrorActionPreference = "Stop"

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $python) {
    throw "Python 3.9+ is required: the PowerShell entry point delegates to sync-agents-to-plugin.py."
}

$arguments = @("-B", (Join-Path $PSScriptRoot "sync-agents-to-plugin.py"))
if ($Root) { $arguments += @("--root", $Root) }
if ($MarketplaceName) { $arguments += @("--marketplace-name", $MarketplaceName) }
if ($PluginName) { $arguments += @("--plugin-name", $PluginName) }
if ($Version) { $arguments += @("--version", $Version) }
if ($Install) { $arguments += "--install" }

& $python.Source @arguments
exit $LASTEXITCODE
