<#
.SYNOPSIS
    Post-deploy hook — automatically configures telephony webhooks after deployment.
    - ACS: Creates Event Grid subscription for incoming calls via Azure CLI.
    - Twilio: Configures phone number voice webhook via Twilio REST API.
    - Infobip: Creates/updates calls application via Infobip REST API.
#>

# --- Check if ACS is the active provider (no Twilio/Infobip configured) ---
$twilioToken = azd env get-value TWILIO_AUTH_TOKEN 2>$null
if ($LASTEXITCODE -ne 0) { $twilioToken = "" }

$infobipKey = azd env get-value INFOBIP_API_KEY 2>$null
if ($LASTEXITCODE -ne 0) { $infobipKey = "" }

# ===========================================================================
# TWILIO: Configure phone number webhook via REST API
# ===========================================================================
if (-not [string]::IsNullOrWhiteSpace($twilioToken)) {
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

    # First informative output — no Write-Host before this point on happy path
    Write-Host "Found voice-capable number: $($selectedNumber.phone_number)" -ForegroundColor White
    Write-Host "Updating voice webhook for $($selectedNumber.phone_number)..." -ForegroundColor White

    # Update the phone number's voice webhook
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
    exit 0
}

# ===========================================================================
# INFOBIP: Update webhook URL and media-stream-config via REST API
# ===========================================================================
if (-not [string]::IsNullOrWhiteSpace($infobipKey)) {
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
            # Create subscription linking profile → calls configuration → events
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
                # Ensure profile is linked (Infobip may not process it in POST)
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
    exit 0
}

# ===========================================================================
# ACS: Automatically create Event Grid subscription
# ===========================================================================

# Get required values from azd env
$resourceGroup = azd env get-value AZURE_RESOURCE_GROUP 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($resourceGroup)) {
    Write-Host "ERROR: Could not retrieve AZURE_RESOURCE_GROUP from azd env." -ForegroundColor Red
    exit 1
}

# Get the container app URL
$endpoints = azd env get-value SERVICE_API_ENDPOINTS 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($endpoints)) {
    Write-Host "ERROR: Could not retrieve SERVICE_API_ENDPOINTS from azd env." -ForegroundColor Red
    exit 1
}

# Parse the webhook endpoint (first entry in the array, e.g. "https://xxx/acs/incomingcall")
$endpointList = @($endpoints | ConvertFrom-Json)
$webhookUrl = $endpointList[0]

if ([string]::IsNullOrWhiteSpace($webhookUrl)) {
    Write-Host "ERROR: Could not determine webhook URL from SERVICE_API_ENDPOINTS." -ForegroundColor Red
    exit 1
}

# Find the ACS resource in the resource group (using az resource to avoid extension dependency)
$acsResource = az resource list --resource-group $resourceGroup --resource-type "Microsoft.Communication/communicationServices" --query "[0]" -o json 2>$null | ConvertFrom-Json
if (-not $acsResource) {
    Write-Host "ERROR: No Communication Services resource found in resource group '$resourceGroup'." -ForegroundColor Red
    exit 1
}

$acsResourceId = $acsResource.id
$subscriptionName = "incoming-call-webhook"
$containerAppUrl = $webhookUrl -replace '/acs/incomingcall$', ''

# Check if subscription already exists
$existingSub = az eventgrid event-subscription show `
    --name $subscriptionName `
    --source-resource-id $acsResourceId -o json 2>$null

if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($existingSub)) {
    # Check if the existing endpoint already matches — skip update if unchanged
    $subObj = $existingSub | ConvertFrom-Json
    $currentEndpoint = $subObj.destination.endpointBaseUrl
    if ($currentEndpoint -eq $webhookUrl) {
        Write-Host ""
        Write-Host "Event Grid subscription already configured." -ForegroundColor Green
        Write-Host "  Webhook    : $webhookUrl" -ForegroundColor Gray
        Write-Host "  Web client : $containerAppUrl" -ForegroundColor Gray
        Write-Host ""
        Write-Host "Buy a phone number at https://aka.ms/acs-phone-number to receive calls." -ForegroundColor White
        Write-Host ""
        exit 0
    } else {
        az eventgrid event-subscription update `
            --name $subscriptionName `
            --source-resource-id $acsResourceId `
            --endpoint $webhookUrl `
            --endpoint-type webhook `
            --included-event-types "Microsoft.Communication.IncomingCall" | Out-Null

        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-Host "Event Grid subscription updated!" -ForegroundColor Green
            Write-Host "  Webhook    : $webhookUrl" -ForegroundColor Gray
            Write-Host "  Web client : $containerAppUrl" -ForegroundColor Gray
            Write-Host ""
            Write-Host "Buy a phone number at https://aka.ms/acs-phone-number to receive calls." -ForegroundColor White
            Write-Host ""
        } else {
            Write-Host ""
            Write-Host "Event Grid update failed. Retry: azd hooks run postdeploy" -ForegroundColor Yellow
            Write-Host "  Or manually: ACS > Events > + Event Subscription > IncomingCall > $webhookUrl" -ForegroundColor Gray
            Write-Host ""
        }
    }
} else {
    az eventgrid event-subscription create `
        --name $subscriptionName `
        --source-resource-id $acsResourceId `
        --endpoint $webhookUrl `
        --endpoint-type webhook `
        --included-event-types "Microsoft.Communication.IncomingCall" | Out-Null

    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "Event Grid subscription configured!" -ForegroundColor Green
        Write-Host "  Webhook    : $webhookUrl" -ForegroundColor Gray
        Write-Host "  Web client : $containerAppUrl" -ForegroundColor Gray
        Write-Host ""
        Write-Host "Buy a phone number at https://aka.ms/acs-phone-number to receive calls." -ForegroundColor White
        Write-Host ""
    } else {
        Write-Host ""
        Write-Host "Event Grid setup failed (app may still be starting). Retry: azd hooks run postdeploy" -ForegroundColor Yellow
        Write-Host ""
    }
}
