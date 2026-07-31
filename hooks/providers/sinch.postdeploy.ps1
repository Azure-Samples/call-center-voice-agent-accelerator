<#
.SYNOPSIS
    Post-deploy: Sinch — configures the Voice app's callback URL via REST API.

.DESCRIPTION
    Uses the Sinch Voice "Update Callbacks" API to set the app's primary callback
    URL to our /sinch/callbacks endpoint. Falls back to printing manual dashboard
    instructions if the credentials or API call are unavailable.

    Auth: Sinch "application" signed request (HMAC-SHA256). This configuration
    endpoint does not accept HTTP Basic auth. See
    https://developers.sinch.com/docs/voice/api-reference/authentication/signed-request.
#>

$sinchKey = azd env get-value SINCH_APPLICATION_KEY 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sinchKey)) {
    Write-Host "ERROR: SINCH_APPLICATION_KEY not set." -ForegroundColor Red
    exit 0
}

$sinchSecret = azd env get-value SINCH_APPLICATION_SECRET 2>$null
if ($LASTEXITCODE -ne 0) { $sinchSecret = "" }

# Get the callback URL (provider endpoint mapping resolves to /sinch/callbacks)
$callbackUrl = ""
$endpoints = azd env get-value SERVICE_API_ENDPOINTS 2>$null
if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($endpoints)) {
    $callbackUrl = @($endpoints | ConvertFrom-Json)[0]
}
if ([string]::IsNullOrWhiteSpace($callbackUrl)) {
    Write-Host "ERROR: Could not determine callback URL." -ForegroundColor Red
    exit 0
}

function Write-ManualInstructions {
    Write-Host ""
    Write-Host "Configure your Sinch Voice app manually:" -ForegroundColor Cyan
    Write-Host "  1. Open https://dashboard.sinch.com and go to Voice > Apps." -ForegroundColor Gray
    Write-Host "  2. Select the app whose Application Key you configured ($sinchKey)." -ForegroundColor Gray
    Write-Host "  3. Set the 'Primary callback URL' to the URL above (HTTPS)." -ForegroundColor Gray
    Write-Host "  4. Ensure the connectStream (audio streaming) beta feature is enabled for the app." -ForegroundColor Gray
    Write-Host "  5. Assign a voice-capable number to the app." -ForegroundColor Gray
    Write-Host ""
}

Write-Host ""
Write-Host "Sinch Voice configuration" -ForegroundColor Green
Write-Host "-------------------------"
Write-Host "  Callback URL : $callbackUrl" -ForegroundColor Green

# Without the secret we cannot call the API — print manual steps and stop.
if ([string]::IsNullOrWhiteSpace($sinchSecret)) {
    Write-Host ""
    Write-Host "SINCH_APPLICATION_SECRET not set — cannot auto-configure the callback URL." -ForegroundColor Yellow
    Write-ManualInstructions
    Write-Host "Then call your Sinch number to talk to your voice agent!" -ForegroundColor White
    Write-Host ""
    exit 0
}

# Sinch Voice REST API — global (region-agnostic) endpoint for app configuration.
# This configuration endpoint requires an "application" signed request (HMAC-SHA256),
# not HTTP Basic auth. See:
# https://developers.sinch.com/docs/voice/api-reference/authentication/signed-request
$apiPath = "/v1/configuration/callbacks/applications/$sinchKey"
$apiUrl = "https://callingapi.sinch.com$apiPath"
$contentType = "application/json"
$body = @{ url = @{ primary = $callbackUrl } } | ConvertTo-Json -Compress
$bodyBytes = [Text.Encoding]::UTF8.GetBytes($body)

# The Application Secret is base64 and must be decoded before use as the HMAC key.
try {
    $secretBytes = [Convert]::FromBase64String($sinchSecret)
}
catch {
    Write-Host ""
    Write-Host "SINCH_APPLICATION_SECRET is not valid base64 — cannot sign the API request." -ForegroundColor Yellow
    Write-ManualInstructions
    Write-Host "Then call your Sinch number to talk to your voice agent!" -ForegroundColor White
    Write-Host ""
    exit 0
}

# Build the Sinch application signed request.
$timestamp = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss'Z'")
$md5 = [Security.Cryptography.MD5]::Create()
$contentMd5 = [Convert]::ToBase64String($md5.ComputeHash($bodyBytes))
# StringToSign = verb\ncontent-MD5\ncontent-type\nx-timestamp:<ts>\n<path>
$stringToSign = "POST`n$contentMd5`n$contentType`nx-timestamp:$timestamp`n$apiPath"
$hmac = [Security.Cryptography.HMACSHA256]::new($secretBytes)
$signature = [Convert]::ToBase64String($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($stringToSign)))

$headers = @{
    Authorization = "application ${sinchKey}:$signature"
    'x-timestamp'  = $timestamp
}

Write-Host ""
Write-Host "Setting callback URL via Sinch API..." -ForegroundColor White

try {
    Invoke-RestMethod -Uri $apiUrl -Headers $headers -Method Post `
        -Body $bodyBytes -ContentType $contentType -ErrorAction Stop | Out-Null

    Write-Host ""
    Write-Host "Sinch callback URL configured successfully!" -ForegroundColor Green
    Write-Host "  App     : $sinchKey" -ForegroundColor Gray
    Write-Host "  Callback: $callbackUrl" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Remaining steps (one-time, in the Sinch dashboard):" -ForegroundColor Cyan
    Write-Host "  - Ensure the connectStream (audio streaming) beta feature is enabled for this app." -ForegroundColor Gray
    Write-Host "  - Assign a voice-capable number to the app." -ForegroundColor Gray
    Write-Host ""
    Write-Host "Then call your Sinch number to talk to your voice agent!" -ForegroundColor White
    Write-Host ""
}
catch {
    $status = $null
    if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
    Write-Host ""
    if ($status -eq 401) {
        Write-Host "Sinch API returned 401 Unauthorized — check the Application Key/Secret, and ensure this machine's clock is accurate (signed requests are time-sensitive)." -ForegroundColor Yellow
    }
    elseif ($status) {
        Write-Host "Sinch API call failed (HTTP $status)." -ForegroundColor Yellow
    }
    else {
        Write-Host "Sinch API call failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }
    Write-ManualInstructions
    Write-Host "Then call your Sinch number to talk to your voice agent!" -ForegroundColor White
    Write-Host ""
}
