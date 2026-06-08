<#
.SYNOPSIS
    Pre-deploy hook — ensures TELEPHONY_PROVIDER is set and imports base image into ACR.
#>

$telephony = azd env get-value TELEPHONY_PROVIDER 2>$null
if ($LASTEXITCODE -ne 0) { $telephony = "" }

if ([string]::IsNullOrWhiteSpace($telephony)) {
    $twilioToken = azd env get-value TWILIO_AUTH_TOKEN 2>$null
    if ($LASTEXITCODE -ne 0) { $twilioToken = "" }
    $infobipKey = azd env get-value INFOBIP_API_KEY 2>$null
    if ($LASTEXITCODE -ne 0) { $infobipKey = "" }
    $genesysKey = azd env get-value GENESYS_API_KEY 2>$null
    if ($LASTEXITCODE -ne 0) { $genesysKey = "" }

    if (-not [string]::IsNullOrWhiteSpace($twilioToken)) {
        azd env set TELEPHONY_PROVIDER twilio
        Write-Host "TELEPHONY_PROVIDER set to: twilio" -ForegroundColor Green
    }
    elseif (-not [string]::IsNullOrWhiteSpace($infobipKey)) {
        azd env set TELEPHONY_PROVIDER infobip
        Write-Host "TELEPHONY_PROVIDER set to: infobip" -ForegroundColor Green
    }
    elseif (-not [string]::IsNullOrWhiteSpace($genesysKey)) {
        azd env set TELEPHONY_PROVIDER genesys
        Write-Host "TELEPHONY_PROVIDER set to: genesys" -ForegroundColor Green
    }
    else {
        azd env set TELEPHONY_PROVIDER acs
        Write-Host "TELEPHONY_PROVIDER set to: acs (default)" -ForegroundColor Green
    }
}

