<#
.SYNOPSIS
    Post-deploy: Genesys — displays AudioHook configuration instructions.
#>

# Get container app URL
$endpoints = azd env get-value SERVICE_API_ENDPOINTS 2>$null
if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($endpoints)) {
    $audiohookUrl = @($endpoints | ConvertFrom-Json)[0]
}
if ([string]::IsNullOrWhiteSpace($audiohookUrl)) {
    Write-Host "ERROR: Could not determine AudioHook WebSocket URL." -ForegroundColor Red
    exit 0
}

$containerAppUrl = $audiohookUrl -replace '^wss://', 'https://' -replace '/audiohook/ws$', ''

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Genesys AudioHook - Deployment Complete" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "AudioHook endpoint ready:" -ForegroundColor Green
Write-Host "  WebSocket : $audiohookUrl" -ForegroundColor White
Write-Host "  Simulator : $containerAppUrl/genesys" -ForegroundColor White
Write-Host ""
Write-Host "To connect Genesys Cloud:" -ForegroundColor Yellow
Write-Host "  1. Add an AudioHook (Audio Connector) integration in Genesys Cloud Admin"
Write-Host "  2. Connection URI  : $audiohookUrl"
Write-Host "  3. API Key         : (the key you configured during setup)"
Write-Host "  4. Assign to a call flow or queue"
Write-Host ""
Write-Host "To test without Genesys Cloud:" -ForegroundColor Yellow
Write-Host "  Open the simulator in your browser: $containerAppUrl/genesys"
Write-Host ""
