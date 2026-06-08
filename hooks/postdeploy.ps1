<#
.SYNOPSIS
    Post-deploy hook — dispatches to the active provider's post-deploy script.
    Adding a new provider: create hooks/providers/<name>.postdeploy.ps1
#>

# --- Determine active telephony provider ---
$telephonyProvider = azd env get-value TELEPHONY_PROVIDER 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($telephonyProvider)) {
    $telephonyProvider = "acs"
}

# --- Dispatch to provider-specific script ---
$providerScript = "$PSScriptRoot/providers/$telephonyProvider.postdeploy.ps1"

if (Test-Path $providerScript) {
    & $providerScript
} else {
    Write-Host "No post-deploy script for provider: $telephonyProvider" -ForegroundColor Yellow
    Write-Host "  Create hooks/providers/$telephonyProvider.postdeploy.ps1 to add one." -ForegroundColor Gray
}
