<#
.SYNOPSIS
    Post-deploy: Twilio — configures phone number voice webhook via REST API.
#>

$twilioToken = azd env get-value TWILIO_AUTH_TOKEN 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($twilioToken)) {
    Write-Host "ERROR: TWILIO_AUTH_TOKEN not set." -ForegroundColor Red
    exit 0
}

# Get container app URL
$endpoints = azd env get-value SERVICE_API_ENDPOINTS 2>$null
if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($endpoints)) {
    $webhookUrl = @($endpoints | ConvertFrom-Json)[0]
}
if ([string]::IsNullOrWhiteSpace($webhookUrl)) {
    Write-Host "ERROR: Could not determine webhook URL." -ForegroundColor Red
    exit 0
}

# Get Account SID
$accountSid = azd env get-value TWILIO_ACCOUNT_SID 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($accountSid)) {
    Write-Host "TWILIO_ACCOUNT_SID not set. Set webhook manually in Twilio Console:" -ForegroundColor Yellow
    Write-Host "  URL: $webhookUrl" -ForegroundColor Green
    exit 0
}

# Twilio API: List incoming phone numbers
$twilioApiBase = "https://api.twilio.com/2010-04-01/Accounts/$accountSid"
$authHeader = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("${accountSid}:${twilioToken}"))
$headers = @{ Authorization = "Basic $authHeader" }

try {
    $numbersResp = Invoke-RestMethod -Uri "$twilioApiBase/IncomingPhoneNumbers.json" `
        -Headers $headers -Method Get -ErrorAction Stop
}
catch {
    Write-Host "Failed to list Twilio numbers. Set webhook manually in Twilio Console:" -ForegroundColor Yellow
    Write-Host "  URL: $webhookUrl" -ForegroundColor Green
    exit 0
}

$phoneNumbers = $numbersResp.incoming_phone_numbers
if (-not $phoneNumbers -or $phoneNumbers.Count -eq 0) {
    Write-Host "No Twilio numbers found. Buy a voice-capable number, then re-run: azd hooks run postdeploy" -ForegroundColor Yellow
    Write-Host "  Webhook endpoint ready: $webhookUrl" -ForegroundColor Green
    exit 0
}

# Filter to voice-capable numbers only
$voiceNumbers = @($phoneNumbers | Where-Object { $_.capabilities.voice -eq $true })
if ($voiceNumbers.Count -eq 0) {
    Write-Host "No voice-capable numbers found. Buy one at https://console.twilio.com/phone-numbers/buy" -ForegroundColor Yellow
    Write-Host "  Then re-run: azd hooks run postdeploy" -ForegroundColor DarkGray
    exit 0
}

# If multiple voice-capable numbers, let user pick; if one, use it directly
if ($voiceNumbers.Count -gt 1) {
    Write-Host "Found $($voiceNumbers.Count) voice-capable numbers:" -ForegroundColor White
    for ($i = 0; $i -lt $voiceNumbers.Count; $i++) {
        Write-Host "  [$($i+1)] $($voiceNumbers[$i].phone_number) ($($voiceNumbers[$i].friendly_name))" -ForegroundColor Gray
    }
    $pick = Read-Host "Select number to configure [1]"
    if ([string]::IsNullOrWhiteSpace($pick)) { $pick = "1" }
    $idx = [int]$pick - 1
    if ($idx -lt 0 -or $idx -ge $voiceNumbers.Count) { exit 0 }
    $selectedNumber = $voiceNumbers[$idx]
} else {
    $selectedNumber = $voiceNumbers[0]
}

# Check if webhook is already configured correctly
if ($selectedNumber.voice_url -eq $webhookUrl) {
    Write-Host ""
    Write-Host "Twilio webhook already configured." -ForegroundColor Green
    Write-Host "  Number  : $($selectedNumber.phone_number)" -ForegroundColor Gray
    Write-Host "  Webhook : $webhookUrl" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Call $($selectedNumber.phone_number) to talk to your voice agent!" -ForegroundColor White
    Write-Host ""
    exit 0
}

# Update the phone number's voice webhook
Write-Host "Found voice-capable number: $($selectedNumber.phone_number)" -ForegroundColor White
Write-Host "Updating voice webhook for $($selectedNumber.phone_number)..." -ForegroundColor White

try {
    $body = "VoiceUrl=$([Uri]::EscapeDataString($webhookUrl))&VoiceMethod=POST"
    Invoke-RestMethod -Uri "$twilioApiBase/IncomingPhoneNumbers/$($selectedNumber.sid).json" `
        -Headers $headers -Method Post -Body $body -ContentType "application/x-www-form-urlencoded" -ErrorAction Stop | Out-Null

    Write-Host ""
    Write-Host "Twilio webhook configured successfully!" -ForegroundColor Green
    Write-Host "  Number  : $($selectedNumber.phone_number)" -ForegroundColor Gray
    Write-Host "  Webhook : $webhookUrl" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Call $($selectedNumber.phone_number) to talk to your voice agent!" -ForegroundColor White
    Write-Host ""
}
catch {
    Write-Host ""
    Write-Host "Failed to set webhook. Set manually in Twilio Console:" -ForegroundColor Yellow
    Write-Host "  Phone   : $($selectedNumber.phone_number)" -ForegroundColor Gray
    Write-Host "  URL     : $webhookUrl" -ForegroundColor Green
    Write-Host ""
}
