<#
.SYNOPSIS
    Post-deploy: Vonage — configures the Voice application's Answer and Event
    URLs via the Vonage Application API.

.DESCRIPTION
    Uses the Vonage Application API (v2) to point a Voice application's webhooks
    at our /vonage/answer and /vonage/events endpoints. The API uses HTTP Basic
    auth (API key + secret).

    If VONAGE_APPLICATION_ID is set, the existing application is fetched and its
    voice webhooks are updated with PUT (preserving unrelated settings). If it
    is NOT set, a new Voice application is CREATED with POST and its ID is saved
    back to the azd environment as VONAGE_APPLICATION_ID.

    After the webhooks are configured, the script can link one voice-capable
    number to the application. Set VONAGE_PHONE_NUMBER to select a number. If
    it is not set, the script uses the only available number or prompts for a
    selection when multiple numbers are available. Automated runs do not
    select from multiple numbers.

    Falls back to printing manual dashboard instructions if the credentials or
    API calls are unavailable. The deployment stays green (exit 0) either way —
    only inbound calls are affected until the webhooks are set.

    References:
      - https://developer.vonage.com/en/api/application.v2
      - https://developer.vonage.com/en/api/numbers
#>

$vonageKey = azd env get-value VONAGE_API_KEY 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($vonageKey)) {
    Write-Host "ACTION REQUIRED: VONAGE_API_KEY is not set, so the Vonage webhooks were not configured." -ForegroundColor Yellow
    Write-Host "  The deployment is ready, but inbound calls will be rejected until the Answer/Event URLs are set." -ForegroundColor Yellow
    Write-Host "  Set the key (azd env set VONAGE_API_KEY <key>) and re-run: azd hooks run postdeploy" -ForegroundColor Yellow
    exit 0
}

$vonageSecret = azd env get-value VONAGE_API_SECRET 2>$null
if ($LASTEXITCODE -ne 0) { $vonageSecret = "" }

$vonageAppId = azd env get-value VONAGE_APPLICATION_ID 2>$null
if ($LASTEXITCODE -ne 0) { $vonageAppId = "" }

# Get the answer URL (provider endpoint mapping resolves to /vonage/answer)
$answerUrl = ""
$endpoints = azd env get-value SERVICE_API_ENDPOINTS 2>$null
if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($endpoints)) {
    $answerUrl = @($endpoints | ConvertFrom-Json)[0]
}
if ([string]::IsNullOrWhiteSpace($answerUrl)) {
    Write-Host "ACTION REQUIRED: Could not determine the Answer URL from SERVICE_API_ENDPOINTS, so it was not configured on Vonage." -ForegroundColor Yellow
    Write-Host "  The deployment is ready, but inbound calls will be rejected until the Answer/Event URLs are set." -ForegroundColor Yellow
    Write-Host "  Re-run 'azd provision' then re-run: azd hooks run postdeploy" -ForegroundColor Yellow
    exit 0
}

# Event URL shares the same host as the Answer URL.
$eventUrl = $answerUrl -replace '/vonage/answer$', '/vonage/events'

function Write-ManualInstructions {
    Write-Host ""
    Write-Host "Configure your Vonage Voice application manually:" -ForegroundColor Cyan
    Write-Host "  1. Open https://dashboard.vonage.com and go to Applications." -ForegroundColor Gray
    Write-Host "  2. Select (or create) your Voice application and enable the Voice capability." -ForegroundColor Gray
    Write-Host "  3. Set 'Answer URL' (GET) to:  $answerUrl" -ForegroundColor Gray
    Write-Host "  4. Set 'Event URL'  (POST) to: $eventUrl" -ForegroundColor Gray
    Write-Host "  5. Link a voice-capable number to the application." -ForegroundColor Gray
    Write-Host ""
}

# The deployment (infrastructure) is ready; only the Vonage webhook auto-config
# step failed. We do NOT fail the deployment for this — instead we emit a loud,
# explicit warning with two remediation paths. Exit 0 keeps the deployment green.
function Warn-ConfigIncomplete {
    param([string]$Reason)
    Write-Host ""
    Write-Host "ACTION REQUIRED: Vonage webhooks were NOT auto-configured — inbound calls will be rejected until this is fixed." -ForegroundColor Yellow
    if ($Reason) { Write-Host "  Reason: $Reason" -ForegroundColor Yellow }
    Write-Host "  The deployment itself is ready; only the Vonage webhook configuration is incomplete." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Fix it either way:" -ForegroundColor Cyan
    Write-Host "  A) Set the Answer/Event URLs manually in the Vonage dashboard (steps below), or" -ForegroundColor Cyan
    Write-Host "  B) Provide a valid VONAGE_API_SECRET, then re-run: azd hooks run postdeploy" -ForegroundColor Cyan
    Write-ManualInstructions
    exit 0
}

function Write-NumberLinkInstructions {
    param(
        [Parameter(Mandatory)][array]$Numbers,
        [Parameter(Mandatory)][string]$AppId
    )

    Write-Host "  Voice-capable numbers on this account:" -ForegroundColor White
    foreach ($number in $Numbers) {
        if ([string]::IsNullOrWhiteSpace($number.app_id)) {
            $status = "unassigned"
        }
        elseif ($number.app_id -eq $AppId) {
            $status = "linked to this application"
        }
        else {
            $status = "linked to application $($number.app_id)"
        }
        Write-Host "    - $($number.msisdn) ($status)" -ForegroundColor Gray
    }

    Write-Host "  To select or reassign a number:" -ForegroundColor Cyan
    Write-Host "    azd env set VONAGE_PHONE_NUMBER <number>" -ForegroundColor Gray
    Write-Host "    azd hooks run postdeploy" -ForegroundColor Gray
    Write-Host "  Or link it in the Vonage Dashboard: Applications > select the application > Link numbers." -ForegroundColor Gray
}

function Link-VonageVoiceNumber {
    param(
        [Parameter(Mandatory)][string]$AppId,
        [Parameter(Mandatory)][hashtable]$AuthHeaders
    )

    Write-Host ""
    Write-Host "Linking voice-capable numbers to the application..." -ForegroundColor White

    try {
        # The Numbers API (rest.nexmo.com) accepts the same HTTP Basic auth.
        $list = Invoke-RestMethod -Uri "https://rest.nexmo.com/account/numbers?size=100" `
            -Headers $AuthHeaders -Method Get -ErrorAction Stop
    }
    catch {
        Write-Host "  NOTE: Could not list Vonage numbers automatically ($($_.Exception.Message))." -ForegroundColor Yellow
        Write-Host "        Link a voice-capable number manually: Applications > voice-agent-accelerator > Link numbers." -ForegroundColor Yellow
        return
    }

    $voiceNumbers = @()
    if ($list.numbers) {
        $voiceNumbers = @($list.numbers | Where-Object { $_.features -contains "VOICE" })
    }

    if ($voiceNumbers.Count -eq 0) {
        Write-Host "  NOTE: No voice-capable numbers found on this Vonage account." -ForegroundColor Yellow
        Write-Host "        Rent one (Dashboard > Numbers > Buy numbers), then re-run: azd hooks run postdeploy." -ForegroundColor Yellow
        return
    }

    $alreadyLinked = @($voiceNumbers | Where-Object { $_.app_id -eq $AppId })
    if ($alreadyLinked.Count -gt 0) {
        Write-Host "  - $($alreadyLinked[0].msisdn) is already linked to this application." -ForegroundColor Gray
        return
    }

    $configuredNumber = azd env get-value VONAGE_PHONE_NUMBER 2>$null
    if ($LASTEXITCODE -ne 0) { $configuredNumber = "" }

    $selectedNumber = $null
    if (-not [string]::IsNullOrWhiteSpace($configuredNumber)) {
        $normalizedNumber = $configuredNumber.Trim().TrimStart('+')
        $selectedNumber = $voiceNumbers | Where-Object { "$($_.msisdn)".TrimStart('+') -eq $normalizedNumber } | Select-Object -First 1
        if (-not $selectedNumber) {
            Write-Host "  NOTE: VONAGE_PHONE_NUMBER '$configuredNumber' was not found among the account's voice-capable numbers." -ForegroundColor Yellow
            Write-NumberLinkInstructions -Numbers $voiceNumbers -AppId $AppId
            return
        }
        if (-not [string]::IsNullOrWhiteSpace($selectedNumber.app_id)) {
            Write-Host "  - Reassigning $configuredNumber from application $($selectedNumber.app_id)." -ForegroundColor Yellow
        }
    }
    else {
        $availableNumbers = @($voiceNumbers | Where-Object { [string]::IsNullOrWhiteSpace($_.app_id) })
        if ($availableNumbers.Count -eq 0) {
            Write-Host "  NOTE: No unassigned voice-capable numbers are available." -ForegroundColor Yellow
            Write-NumberLinkInstructions -Numbers $voiceNumbers -AppId $AppId
            return
        }
        if ($availableNumbers.Count -eq 1) {
            $selectedNumber = $availableNumbers[0]
        }
        elseif ($env:CI) {
            Write-Host "  NOTE: Multiple unassigned numbers were found; no number was changed during this automated run." -ForegroundColor Yellow
            Write-NumberLinkInstructions -Numbers $voiceNumbers -AppId $AppId
            return
        }
        else {
            Write-Host "  Found $($availableNumbers.Count) unassigned voice-capable numbers:" -ForegroundColor White
            for ($i = 0; $i -lt $availableNumbers.Count; $i++) {
                Write-Host "    [$($i + 1)] $($availableNumbers[$i].msisdn)" -ForegroundColor Gray
            }
            $pick = Read-Host "Select number to link [1]"
            if ([string]::IsNullOrWhiteSpace($pick)) { $pick = "1" }
            $index = 0
            if (-not [int]::TryParse($pick, [ref]$index) -or $index -lt 1 -or $index -gt $availableNumbers.Count) {
                Write-Host "  No number linked." -ForegroundColor Yellow
                return
            }
            $selectedNumber = $availableNumbers[$index - 1]
        }
    }

    try {
        $updateBody = @{
            country = $selectedNumber.country
            msisdn  = $selectedNumber.msisdn
            app_id  = $AppId
        }
        Invoke-RestMethod -Uri "https://rest.nexmo.com/number/update" -Headers $AuthHeaders `
            -Method Post -Body $updateBody -ContentType "application/x-www-form-urlencoded" -ErrorAction Stop | Out-Null
        Write-Host "  - Linked $($selectedNumber.msisdn) to the application." -ForegroundColor Green
    }
    catch {
        Write-Host "  - Could not link $($selectedNumber.msisdn) automatically ($($_.Exception.Message)); link it manually in the dashboard." -ForegroundColor Yellow
        Write-NumberLinkInstructions -Numbers $voiceNumbers -AppId $AppId
    }
}

Write-Host ""
Write-Host "Vonage Voice configuration" -ForegroundColor Green
Write-Host "--------------------------"
Write-Host "  Answer URL : $answerUrl" -ForegroundColor Green
Write-Host "  Event URL  : $eventUrl" -ForegroundColor Green

# Without the secret we cannot call the API — warn with manual steps.
if ([string]::IsNullOrWhiteSpace($vonageSecret)) {
    Warn-ConfigIncomplete -Reason "VONAGE_API_SECRET is not set, so the Application API cannot authenticate."
}

# Vonage Application API (v2) — HTTP Basic auth with API key + secret.
$basic = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${vonageKey}:$vonageSecret"))
$headers = @{ Authorization = "Basic $basic" }

# The voice capability webhooks we want on the application, either way.
$voiceCapability = [pscustomobject]@{
    webhooks = [pscustomobject]@{
        answer_url = [pscustomobject]@{ address = $answerUrl; http_method = "GET" }
        event_url  = [pscustomobject]@{ address = $eventUrl;  http_method = "POST" }
    }
}

# ------------------------------------------------------------------
# No application ID yet → create a new Voice application for the user.
# ------------------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($vonageAppId)) {
    Write-Host ""
    Write-Host "No VONAGE_APPLICATION_ID set — creating a new Vonage Voice application..." -ForegroundColor White

    $createBody = [pscustomobject]@{
        name         = "voice-agent-accelerator"
        capabilities = [pscustomobject]@{ voice = $voiceCapability }
    } | ConvertTo-Json -Depth 12 -Compress

    try {
        $created = Invoke-RestMethod -Uri "https://api.nexmo.com/v2/applications" -Headers $headers `
            -Method Post -Body $createBody -ContentType "application/json" -ErrorAction Stop
    }
    catch {
        $status = $null
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        if ($status -eq 401) {
            Warn-ConfigIncomplete -Reason "Vonage API returned 401 Unauthorized while creating the application — check VONAGE_API_KEY / VONAGE_API_SECRET."
        }
        elseif ($status) {
            Warn-ConfigIncomplete -Reason "Vonage API failed to create the application (HTTP $status)."
        }
        else {
            Warn-ConfigIncomplete -Reason "Vonage API failed to create the application: $($_.Exception.Message)"
        }
    }

    $newAppId = $created.id
    if ([string]::IsNullOrWhiteSpace($newAppId)) {
        Warn-ConfigIncomplete -Reason "Vonage create-application response did not contain an application id."
    }

    # Persist the new ID so future deploys update (not recreate) this application.
    azd env set VONAGE_APPLICATION_ID $newAppId | Out-Null

    Write-Host ""
    Write-Host "Vonage Voice application created and configured!" -ForegroundColor Green
    Write-Host "  Application : $newAppId  (saved to azd env as VONAGE_APPLICATION_ID)" -ForegroundColor Gray
    Write-Host "  Answer URL  : $answerUrl" -ForegroundColor Gray
    Write-Host "  Event URL   : $eventUrl" -ForegroundColor Gray

    # Attempt to link the account's voice-capable number(s) automatically.
    Link-VonageVoiceNumber -AppId $newAppId -AuthHeaders $headers

    Write-Host ""
    Write-Host "Then call your Vonage number to talk to your voice agent!" -ForegroundColor White
    Write-Host ""
    exit 0
}

# ------------------------------------------------------------------
# Application ID present → update the existing application's webhooks.
# ------------------------------------------------------------------
$appUrl = "https://api.nexmo.com/v2/applications/$vonageAppId"

Write-Host ""
Write-Host "Fetching Vonage application..." -ForegroundColor White

try {
    $app = Invoke-RestMethod -Uri $appUrl -Headers $headers -Method Get -ErrorAction Stop
}
catch {
    $status = $null
    if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
    if ($status -eq 401) {
        Warn-ConfigIncomplete -Reason "Vonage API returned 401 Unauthorized — check VONAGE_API_KEY / VONAGE_API_SECRET."
    }
    elseif ($status -eq 404) {
        Warn-ConfigIncomplete -Reason "Vonage API returned 404 — application '$vonageAppId' was not found for this account."
    }
    elseif ($status) {
        Warn-ConfigIncomplete -Reason "Vonage API GET failed (HTTP $status)."
    }
    else {
        Warn-ConfigIncomplete -Reason "Vonage API GET failed: $($_.Exception.Message)"
    }
}

# Update the voice capability webhooks without disturbing other capabilities.
if (-not $app.capabilities) {
    $app | Add-Member -NotePropertyName capabilities -NotePropertyValue ([pscustomobject]@{}) -Force
}
$app.capabilities | Add-Member -NotePropertyName voice -NotePropertyValue $voiceCapability -Force

# The Application API PUT rejects read-only fields; keep only writable ones.
$putBody = [pscustomobject]@{
    name         = $app.name
    capabilities = $app.capabilities
}
if ($app.privacy) { $putBody | Add-Member -NotePropertyName privacy -NotePropertyValue $app.privacy -Force }

$json = $putBody | ConvertTo-Json -Depth 12 -Compress

Write-Host "Updating Answer/Event URLs via Vonage Application API..." -ForegroundColor White

try {
    Invoke-RestMethod -Uri $appUrl -Headers $headers -Method Put `
        -Body $json -ContentType "application/json" -ErrorAction Stop | Out-Null

    Write-Host ""
    Write-Host "Vonage webhooks configured successfully!" -ForegroundColor Green
    Write-Host "  Application : $vonageAppId" -ForegroundColor Gray
    Write-Host "  Answer URL  : $answerUrl" -ForegroundColor Gray
    Write-Host "  Event URL   : $eventUrl" -ForegroundColor Gray

    # Attempt to link the account's voice-capable number(s) automatically.
    Link-VonageVoiceNumber -AppId $vonageAppId -AuthHeaders $headers

    Write-Host ""
    Write-Host "Then call your Vonage number to talk to your voice agent!" -ForegroundColor White
    Write-Host ""
    exit 0
}
catch {
    $status = $null
    if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
    if ($status -eq 401) {
        Warn-ConfigIncomplete -Reason "Vonage API returned 401 Unauthorized — check VONAGE_API_KEY / VONAGE_API_SECRET."
    }
    elseif ($status) {
        Warn-ConfigIncomplete -Reason "Vonage API PUT failed (HTTP $status)."
    }
    else {
        Warn-ConfigIncomplete -Reason "Vonage API PUT failed: $($_.Exception.Message)"
    }
}
