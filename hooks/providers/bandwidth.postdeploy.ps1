<#
.SYNOPSIS
    Post-deploy: Bandwidth — finds/creates a Voice-V2 Application and points its
    call-initiated callback at the deployed container app via the Bandwidth
    Account Management API (XML). Also sets Basic Auth callback credentials so
    incoming webhooks are authenticated.

    Authenticates to the Bandwidth API using OAuth 2.0 Client Credentials
    (Client ID / Client Secret). The legacy API User (username/password Basic
    Auth) scheme is deprecated and cannot be provisioned on new accounts.

    Requires (set via azd env): BANDWIDTH_ACCOUNT_ID, BANDWIDTH_CLIENT_ID, BANDWIDTH_CLIENT_SECRET
#>

$accountId = azd env get-value BANDWIDTH_ACCOUNT_ID 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($accountId)) {
    Write-Host "ERROR: BANDWIDTH_ACCOUNT_ID not set." -ForegroundColor Red
    exit 0
}

$clientId = azd env get-value BANDWIDTH_CLIENT_ID 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($clientId)) {
    Write-Host "ERROR: BANDWIDTH_CLIENT_ID not set." -ForegroundColor Red
    exit 0
}

$clientSecret = azd env get-value BANDWIDTH_CLIENT_SECRET 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($clientSecret)) {
    Write-Host "ERROR: BANDWIDTH_CLIENT_SECRET not set." -ForegroundColor Red
    exit 0
}

# --- Determine the container app webhook URL ---
$endpoints = azd env get-value SERVICE_API_ENDPOINTS 2>$null
if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($endpoints)) {
    $webhookUrl = @($endpoints | ConvertFrom-Json)[0]
}
if ([string]::IsNullOrWhiteSpace($webhookUrl)) {
    Write-Host "ERROR: Could not determine webhook URL." -ForegroundColor Red
    exit 0
}

# --- Obtain an OAuth 2.0 bearer token (grant_type=client_credentials) ---
$basic = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("${clientId}:${clientSecret}"))
try {
    $tokenResp = Invoke-RestMethod -Uri "https://api.bandwidth.com/api/v1/oauth2/token" `
        -Method Post -Headers @{ Authorization = "Basic $basic" } `
        -ContentType "application/x-www-form-urlencoded" `
        -Body "grant_type=client_credentials" -ErrorAction Stop
    $accessToken = $tokenResp.access_token
}
catch {
    Write-Host "ERROR: Failed to obtain a Bandwidth OAuth token. Check BANDWIDTH_CLIENT_ID/SECRET." -ForegroundColor Red
    exit 0
}
if ([string]::IsNullOrWhiteSpace($accessToken)) {
    Write-Host "ERROR: Bandwidth OAuth token response did not contain an access_token." -ForegroundColor Red
    exit 0
}

$apiBase = "https://api.bandwidth.com/api/accounts/$accountId/applications"
$headers = @{ Authorization = "Bearer $accessToken" }
$appName = "voice-agent-accelerator"

function New-VoiceApplicationXml {
    param([string]$Name, [string]$CallbackUrl, [string]$UserId, [string]$Password)
    return @"
<Application>
    <ServiceType>Voice-V2</ServiceType>
    <AppName>$Name</AppName>
    <CallInitiatedCallbackUrl>$CallbackUrl</CallInitiatedCallbackUrl>
    <CallInitiatedMethod>POST</CallInitiatedMethod>
    <CallbackCreds>
        <UserId>$UserId</UserId>
        <Password>$Password</Password>
    </CallbackCreds>
</Application>
"@
}

# --- Step 1: List existing applications and look for ours ---
$existingApp = $null
$applicationId = azd env get-value BANDWIDTH_APPLICATION_ID 2>$null
if ($LASTEXITCODE -ne 0) { $applicationId = "" }

try {
    [xml]$listResp = Invoke-RestMethod -Uri $apiBase -Headers $headers -Method Get -ContentType "application/xml" -ErrorAction Stop
    $apps = @($listResp.ApplicationProvisioningResponse.ApplicationList.Application)
    foreach ($a in $apps) {
        if ($a.ServiceType -ne "Voice-V2") { continue }
        if ((-not [string]::IsNullOrWhiteSpace($applicationId) -and $a.ApplicationId -eq $applicationId) `
                -or $a.AppName -eq $appName `
                -or $a.CallInitiatedCallbackUrl -eq $webhookUrl) {
            $existingApp = $a
            break
        }
    }
}
catch {
    Write-Host "Failed to list Bandwidth applications. Configure the Voice application manually:" -ForegroundColor Yellow
    Write-Host "  Call-initiated callback URL : $webhookUrl (POST)" -ForegroundColor Green
    Write-Host "  Callback credentials         : UserId=<Client ID>, Password=<Client Secret>" -ForegroundColor Green
    exit 0
}

# --- Step 2: Create or update the Voice application ---
try {
    if ($null -ne $existingApp) {
        $applicationId = $existingApp.ApplicationId
        if ($existingApp.CallInitiatedCallbackUrl -eq $webhookUrl) {
            Write-Host ""
            Write-Host "Bandwidth Voice application already configured." -ForegroundColor Green
            Write-Host "  ApplicationId : $applicationId" -ForegroundColor Gray
            Write-Host "  Callback URL  : $webhookUrl" -ForegroundColor Gray
        }
        else {
            $body = New-VoiceApplicationXml -Name $appName -CallbackUrl $webhookUrl -UserId $clientId -Password $clientSecret
            Invoke-RestMethod -Uri "$apiBase/$applicationId" -Headers $headers -Method Put `
                -Body $body -ContentType "application/xml" -ErrorAction Stop | Out-Null
            Write-Host ""
            Write-Host "Bandwidth Voice application updated." -ForegroundColor Green
            Write-Host "  ApplicationId : $applicationId" -ForegroundColor Gray
            Write-Host "  Callback URL  : $webhookUrl" -ForegroundColor Gray
        }
    }
    else {
        $body = New-VoiceApplicationXml -Name $appName -CallbackUrl $webhookUrl -UserId $clientId -Password $clientSecret
        [xml]$createResp = Invoke-RestMethod -Uri $apiBase -Headers $headers -Method Post `
            -Body $body -ContentType "application/xml" -ErrorAction Stop
        $applicationId = $createResp.ApplicationProvisioningResponse.Application.ApplicationId
        Write-Host ""
        Write-Host "Bandwidth Voice application created." -ForegroundColor Green
        Write-Host "  ApplicationId : $applicationId" -ForegroundColor Gray
        Write-Host "  Callback URL  : $webhookUrl" -ForegroundColor Gray
    }

    # Persist the application ID for subsequent deploys / container config.
    if (-not [string]::IsNullOrWhiteSpace($applicationId)) {
        azd env set BANDWIDTH_APPLICATION_ID $applicationId 2>$null | Out-Null
    }
}
catch {
    Write-Host "Failed to create/update the Bandwidth application. Configure it manually:" -ForegroundColor Yellow
    Write-Host "  Call-initiated callback URL : $webhookUrl (POST)" -ForegroundColor Green
    Write-Host "  Callback credentials         : UserId=<Client ID>, Password=<Client Secret>" -ForegroundColor Green
    exit 0
}

# --- Step 3: Re-point any Bandwidth-managed "default" Voice app at our container ---
#
# Trial / self-service (app.bandwidth.com "express") accounts pre-provision a
# default Voice-V2 application (typically named "default-http-voice") whose
# callback points at a Bandwidth sample endpoint (e.g. .../sampleCallback or
# express.cx-accounts...). Purchased numbers are bound to THAT app, not ours,
# and the classic Numbers/Sip-Peer API is often not authorized on trial
# credentials (HTTP 401) so we cannot re-bind the number directly.
#
# Since a Voice app's callback URL IS writable, the reliable automated fix is to
# re-point the default app's callback at our container. That way inbound calls
# on the number reach us without touching number/Location bindings. We ONLY
# touch apps whose callback is a Bandwidth-owned sample/express host, never a
# user's own third-party app.
$redirected = @()
try {
    [xml]$reList = Invoke-RestMethod -Uri $apiBase -Headers $headers -Method Get -ContentType "application/xml" -ErrorAction Stop
    foreach ($a in @($reList.ApplicationProvisioningResponse.ApplicationList.Application)) {
        if ($a.ServiceType -ne "Voice-V2") { continue }
        if ($a.ApplicationId -eq $applicationId) { continue }          # skip our own app
        if ($a.CallInitiatedCallbackUrl -eq $webhookUrl) { continue }   # already points at us
        $cb = [string]$a.CallInitiatedCallbackUrl
        $isBandwidthDefault = ($a.AppName -eq "default-http-voice") `
            -or ($cb -match "sampleCallback") `
            -or ($cb -match "express\.cx-accounts") `
            -or ($cb -match "\.bandwidth\.com/")
        if (-not $isBandwidthDefault) { continue }

        $body = New-VoiceApplicationXml -Name $a.AppName -CallbackUrl $webhookUrl -UserId $clientId -Password $clientSecret
        try {
            Invoke-RestMethod -Uri "$apiBase/$($a.ApplicationId)" -Headers $headers -Method Put `
                -Body $body -ContentType "application/xml" -ErrorAction Stop | Out-Null
            $redirected += "$($a.AppName) ($($a.ApplicationId))"
        }
        catch {
            Write-Host "Could not re-point default app $($a.AppName) ($($a.ApplicationId))." -ForegroundColor Yellow
        }
    }
}
catch {
    # Non-fatal: fall through to manual guidance below.
}

if ($redirected.Count -gt 0) {
    Write-Host ""
    Write-Host "Re-pointed Bandwidth default Voice app(s) at your container:" -ForegroundColor Green
    foreach ($r in $redirected) { Write-Host "  $r -> $webhookUrl" -ForegroundColor Gray }
    Write-Host "  Numbers bound to these apps now reach your voice agent. Call your number to test." -ForegroundColor Gray
    Write-Host ""
}

# --- Step 4: Number association guidance (classic accounts) ---
Write-Host ""
Write-Host "If your number is NOT bound to a Bandwidth default app (classic account):" -ForegroundColor White
Write-Host "  Associate the number's Location with application $applicationId" -ForegroundColor Gray
Write-Host "  in the Bandwidth Dashboard. Then call the number to" -ForegroundColor Gray
Write-Host "  talk to your voice agent." -ForegroundColor Gray
Write-Host ""
