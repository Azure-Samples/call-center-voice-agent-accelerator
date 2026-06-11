<#
.SYNOPSIS
    Post-deploy: Infobip — updates webhook URL and media-stream-config via REST API.
#>

$infobipKey = azd env get-value INFOBIP_API_KEY 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($infobipKey)) {
    Write-Host "ERROR: INFOBIP_API_KEY not set." -ForegroundColor Red
    exit 0
}

$infobipBaseUrl = azd env get-value INFOBIP_API_BASE_URL 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($infobipBaseUrl)) {
    Write-Host "--- Post-deploy: ERROR - INFOBIP_API_BASE_URL not set." -ForegroundColor Red
    exit 0
}

# Get container app URL
$endpoints = azd env get-value SERVICE_API_ENDPOINTS 2>$null
if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($endpoints)) {
    $containerAppUrl = @($endpoints | ConvertFrom-Json)[0] -replace '/infobip/incoming$', ''
}
if ([string]::IsNullOrWhiteSpace($containerAppUrl)) {
    Write-Host "--- Post-deploy: ERROR - Could not determine container app URL." -ForegroundColor Red
    exit 0
}

$webhookUrl = "$containerAppUrl/infobip/incoming"
$wsUrl = "wss://$($containerAppUrl -replace 'https://', '')/infobip/ws"
$infobipHeaders = @{
    Authorization  = "App $infobipKey"
    "Content-Type" = "application/json"
}

$profileUpdated = $false
$mediaConfigUpdated = $false
$profileName = "voice-agent-accelerator"

# --- Step 1: Update or create notification profile (webhook URL) ---
try {
    $profilesResp = Invoke-RestMethod -Uri "$infobipBaseUrl/subscriptions/1/profiles" `
        -Headers $infobipHeaders -Method Get -ErrorAction Stop
    $profileResults = @($profilesResp.results)
    if ($profileResults.Count -gt 0) {
        $profile = $profileResults[0]
        $currentNotifyUrl = $profile.webhook.notifyUrl
        if ($currentNotifyUrl -ne $webhookUrl) {
            $profileBody = @{ webhook = @{ notifyUrl = $webhookUrl } } | ConvertTo-Json -Depth 3
            Invoke-RestMethod -Uri "$infobipBaseUrl/subscriptions/1/profiles/$($profile.profileId)" `
                -Headers $infobipHeaders -Method Put -Body $profileBody -ErrorAction Stop | Out-Null
            $profileUpdated = $true
        }
        $profileName = $profile.profileId
    }
    else {
        # Create new profile
        $profileBody = @{ profileId = $profileName; webhook = @{ notifyUrl = $webhookUrl } } | ConvertTo-Json -Depth 3
        Invoke-RestMethod -Uri "$infobipBaseUrl/subscriptions/1/profiles" `
            -Headers $infobipHeaders -Method Post -Body $profileBody -ErrorAction Stop | Out-Null
        $profileUpdated = $true
    }
}
catch { }

# --- Step 2: Update or create media-stream-config (WebSocket URL) ---
try {
    $mediaResp = Invoke-RestMethod -Uri "$infobipBaseUrl/calls/1/media-stream-configs" `
        -Headers $infobipHeaders -Method Get -ErrorAction Stop
    $mediaResults = @($mediaResp.results)
    if ($mediaResults.Count -gt 0) {
        $mediaConfig = $mediaResults[0]
        if ($mediaConfig.url -ne $wsUrl) {
            $mediaBody = @{
                name       = $mediaConfig.name
                type       = $mediaConfig.type
                url        = $wsUrl
                sampleRate = $mediaConfig.sampleRate
            } | ConvertTo-Json -Depth 3
            Invoke-RestMethod -Uri "$infobipBaseUrl/calls/1/media-stream-configs/$($mediaConfig.id)" `
                -Headers $infobipHeaders -Method Put -Body $mediaBody -ErrorAction Stop | Out-Null
            $mediaConfigUpdated = $true
        }
    }
    else {
        # Create new media-stream-config
        $mediaBody = @{
            name       = "voice-agent-media-stream"
            type       = "WEBSOCKET_ENDPOINT"
            url        = $wsUrl
            sampleRate = "24000"
        } | ConvertTo-Json -Depth 3
        Invoke-RestMethod -Uri "$infobipBaseUrl/calls/1/media-stream-configs" `
            -Headers $infobipHeaders -Method Post -Body $mediaBody -ErrorAction Stop | Out-Null
        $mediaConfigUpdated = $true
    }
}
catch { }

# --- Step 3: Ensure calls configuration exists ---
$callsConfigId = $null
try {
    $configsResp = Invoke-RestMethod -Uri "$infobipBaseUrl/calls/1/configurations" `
        -Headers $infobipHeaders -Method Get -ErrorAction Stop
    $configResults = @($configsResp.results)
    if ($configResults.Count -gt 0) {
        $callsConfigId = $configResults[0].id
    }
    else {
        $configBody = @{ name = "voice-agent-config" } | ConvertTo-Json
        $newConfig = Invoke-RestMethod -Uri "$infobipBaseUrl/calls/1/configurations" `
            -Headers $infobipHeaders -Method Post -Body $configBody -ErrorAction Stop
        $callsConfigId = $newConfig.id
    }
}
catch { }

# --- Step 4: Ensure VOICE_VIDEO subscription exists ---
if ($callsConfigId) {
    $needsSubscription = $false
    try {
        $subsResp = Invoke-RestMethod -Uri "$infobipBaseUrl/subscriptions/1/subscription/VOICE_VIDEO" `
            -Headers $infobipHeaders -Method Get -ErrorAction Stop
        $subResults = @($subsResp.results)
        if ($subResults.Count -eq 0) {
            $needsSubscription = $true
        }
    }
    catch {
        $needsSubscription = $true
    }
    if ($needsSubscription) {
        $newSubId = [guid]::NewGuid().ToString()
        $subBody = @{
            subscriptionId = $newSubId
            name    = "voice-agent-subscription"
            profile = @{ profileId = $profileName }
            events  = @(
                "CALL_RECEIVED", "CALL_ESTABLISHED", "CALL_FINISHED", "CALL_FAILED",
                "MEDIA_STREAM_STARTED", "MEDIA_STREAM_FAILED", "MEDIA_STREAM_FINISHED",
                "DIALOG_CREATED", "DIALOG_ESTABLISHED", "DIALOG_FAILED", "DIALOG_FINISHED"
            )
            criteria = @(@{ callsConfigurationId = $callsConfigId })
        } | ConvertTo-Json -Depth 3
        try {
            Invoke-RestMethod -Uri "$infobipBaseUrl/subscriptions/1/subscription/VOICE_VIDEO" `
                -Headers $infobipHeaders -Method Post -Body $subBody -ErrorAction Stop | Out-Null
            $putBody = @{
                profile = @{ profileId = $profileName }
                events  = @(
                    "CALL_RECEIVED", "CALL_ESTABLISHED", "CALL_FINISHED", "CALL_FAILED",
                    "MEDIA_STREAM_STARTED", "MEDIA_STREAM_FAILED", "MEDIA_STREAM_FINISHED",
                    "DIALOG_CREATED", "DIALOG_ESTABLISHED", "DIALOG_FAILED", "DIALOG_FINISHED"
                )
                criteria = @(@{ callsConfigurationId = $callsConfigId })
            } | ConvertTo-Json -Depth 3
            Invoke-RestMethod -Uri "$infobipBaseUrl/subscriptions/1/subscription/VOICE_VIDEO/$newSubId" `
                -Headers $infobipHeaders -Method Put -Body $putBody -ErrorAction SilentlyContinue | Out-Null
        }
        catch { }
    }
}

# --- Output results ---
if (-not $profileUpdated -and -not $mediaConfigUpdated) {
    Write-Host ""
    Write-Host "Infobip webhook already configured." -ForegroundColor Green
    Write-Host "  Webhook : $webhookUrl" -ForegroundColor Gray
    Write-Host "  WS URL  : $wsUrl" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Call your Infobip number to talk to the voice agent!" -ForegroundColor White
    Write-Host ""
}
else {
    Write-Host ""
    Write-Host "Infobip webhook configured successfully!" -ForegroundColor Green
    if ($profileUpdated) { Write-Host "  Webhook : $webhookUrl (updated)" -ForegroundColor Gray }
    if ($mediaConfigUpdated) { Write-Host "  WS URL  : $wsUrl (updated)" -ForegroundColor Gray }
    Write-Host ""
    Write-Host "Call your Infobip number to talk to the voice agent!" -ForegroundColor White
    Write-Host ""
}
