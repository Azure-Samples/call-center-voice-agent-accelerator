<#
.SYNOPSIS
    Pre-provision hook — validates prerequisites and configures optional telephony provider.
#>

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Voice Agent Accelerator - Setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# --- Check required tools ---
$missing = @()
if (-not (Get-Command "azd" -ErrorAction SilentlyContinue)) { $missing += "azd" }
if (-not (Get-Command "az" -ErrorAction SilentlyContinue)) { $missing += "az CLI" }

if ($missing.Count -gt 0) {
    Write-Host "ERROR: Missing required tools: $($missing -join ', ')" -ForegroundColor Red
    Write-Host "Install from: https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd"
    exit 1
}

# --- Validate Azure login ---
$account = az account show 2>$null | ConvertFrom-Json
if (-not $account) {
    Write-Host "ERROR: Not logged in to Azure. Run 'az login' first." -ForegroundColor Red
    exit 1
}
Write-Host "Subscription: $($account.name) ($($account.id))" -ForegroundColor Green

# --- Model selection ---
$modelName = azd env get-value AZURE_VOICE_LIVE_MODEL 2>$null
if ($LASTEXITCODE -ne 0) { $modelName = "" }
$selectedLocation = azd env get-value AZURE_LOCATION 2>$null
if ($LASTEXITCODE -ne 0) { $selectedLocation = "" }

if ([string]::IsNullOrWhiteSpace($modelName)) {
    Write-Host ""
    Write-Host "Model Selection" -ForegroundColor Yellow
    Write-Host "---------------"
    Write-Host "Your region: $selectedLocation"
    Write-Host ""
    Write-Host "All models are fully managed (no deployment or capacity planning needed)."
    Write-Host "Pricing is determined by the model tier. See:" -ForegroundColor DarkGray
    Write-Host "https://learn.microsoft.com/azure/ai-services/speech-service/voice-live#supported-models-and-regions" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Voice Live Pro" -ForegroundColor Magenta
    Write-Host "    [1]  gpt-realtime        Native audio I/O + Azure TTS (custom voice supported)"
    Write-Host "    [2]  gpt-4o              Azure STT + GPT-4o + Azure TTS"
    Write-Host "    [3]  gpt-4.1             Azure STT + GPT-4.1 + Azure TTS"
    Write-Host "    [4]  gpt-5               Azure STT + GPT-5 + Azure TTS"
    Write-Host "    [5]  gpt-5-chat          Azure STT + GPT-5 chat + Azure TTS"
    Write-Host ""
    Write-Host "  Voice Live Basic" -ForegroundColor Cyan
    Write-Host "    [6]  gpt-realtime-mini   Native audio I/O + Azure TTS (custom voice supported)"
    Write-Host "    [7]  gpt-4o-mini         Azure STT + GPT-4o mini + Azure TTS" -NoNewline
    Write-Host " (default)" -ForegroundColor Green
    Write-Host "    [8]  gpt-4.1-mini        Azure STT + GPT-4.1 mini + Azure TTS"
    Write-Host "    [9]  gpt-5-mini          Azure STT + GPT-5 mini + Azure TTS"
    Write-Host ""
    Write-Host "  Voice Live Lite" -ForegroundColor DarkYellow
    Write-Host "    [10] gpt-5-nano          Azure STT + GPT-5 nano + Azure TTS"
    Write-Host "    [11] phi4-mm-realtime    Native Phi4-mm audio + Azure TTS"
    Write-Host "    [12] phi4-mini           Azure STT + Phi4-mini + Azure TTS"
    Write-Host ""
    Write-Host "    [13] Custom (BYOM - bring your own model deployment)"
    Write-Host ""
    $modelChoice = Read-Host "Select model [7]"
    if ([string]::IsNullOrWhiteSpace($modelChoice)) { $modelChoice = "7" }

    $modelMap = @{
        "1"  = "gpt-realtime"
        "2"  = "gpt-4o"
        "3"  = "gpt-4.1"
        "4"  = "gpt-5"
        "5"  = "gpt-5-chat"
        "6"  = "gpt-realtime-mini"
        "7"  = "gpt-4o-mini"
        "8"  = "gpt-4.1-mini"
        "9"  = "gpt-5-mini"
        "10" = "gpt-5-nano"
        "11" = "phi4-mm-realtime"
        "12" = "phi4-mini"
    }

    if ($modelChoice -eq "13") {
        $modelName = Read-Host "Enter your model deployment name"
        if ([string]::IsNullOrWhiteSpace($modelName)) {
            Write-Host "ERROR: Model deployment name is required." -ForegroundColor Red
            exit 1
        }
    }
    elseif ($modelMap.ContainsKey($modelChoice)) {
        $modelName = $modelMap[$modelChoice]
    }
    else {
        Write-Host "Invalid selection, using gpt-4o-mini." -ForegroundColor Yellow
        $modelName = "gpt-4o-mini"
    }

    azd env set AZURE_VOICE_LIVE_MODEL $modelName
    Write-Host "Model: $modelName" -ForegroundColor Green
}
else {
    Write-Host "Model: $modelName (already configured)" -ForegroundColor Green
}

# --- Validate model-region compatibility ---
# Models not listed here are available in ALL Voice Live regions.
# See: https://learn.microsoft.com/azure/ai-services/speech-service/regions?tabs=voice-live
$regionModelSupport = @{
    "gpt-realtime"      = @("australiaeast","canadaeast","eastus2","francecentral","southeastasia","swedencentral","uksouth","westus2")
    "gpt-realtime-mini" = @("australiaeast","eastus2","francecentral","southeastasia","swedencentral","uksouth","westus2")
    "gpt-4o"            = @("australiaeast","brazilsouth","eastus","eastus2","francecentral","italynorth","japaneast","norwayeast","southafricanorth","southcentralus","swedencentral","switzerlandnorth","uksouth","westeurope","westus","westus2","westus3")
    "gpt-4o-mini"       = @("australiaeast","brazilsouth","eastus","eastus2","francecentral","italynorth","japaneast","norwayeast","southafricanorth","southcentralus","swedencentral","switzerlandnorth","uksouth","westeurope","westus","westus2","westus3")
    "phi4-mm-realtime"  = @("eastus2","japaneast","swedencentral","westus2")
    "phi4-mini"         = @("eastus2","japaneast","swedencentral","westus2")
}

if ($regionModelSupport.ContainsKey($modelName) -and -not [string]::IsNullOrWhiteSpace($selectedLocation)) {
    $supportedRegions = $regionModelSupport[$modelName]
    if ($selectedLocation -notin $supportedRegions) {
        Write-Host ""
        Write-Host "ERROR: '$modelName' is not available in '$selectedLocation' for Voice Live." -ForegroundColor Red
        Write-Host "Supported regions for ${modelName}: $($supportedRegions -join ', ')" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Options:" -ForegroundColor Cyan
        Write-Host "  1. Change region:  azd env set AZURE_LOCATION <region>"
        Write-Host "  2. Change model:   azd env set AZURE_VOICE_LIVE_MODEL <model>"
        Write-Host ""
        Write-Host "Models available in ALL Voice Live regions:" -ForegroundColor Green
        Write-Host "  gpt-4.1, gpt-4.1-mini, gpt-4.1-nano, gpt-5, gpt-5-chat, gpt-5-mini, gpt-5-nano" -ForegroundColor Green
        Write-Host ""
        Write-Host "Full matrix: https://learn.microsoft.com/azure/ai-services/speech-service/regions?tabs=voice-live" -ForegroundColor DarkGray
        exit 1
    }
}

# --- Telephony configuration ---
$twilioToken = azd env get-value TWILIO_AUTH_TOKEN 2>$null
if ($LASTEXITCODE -ne 0) { $twilioToken = "" }
$infobipKey = azd env get-value INFOBIP_API_KEY 2>$null
if ($LASTEXITCODE -ne 0) { $infobipKey = "" }
$genesysKey = azd env get-value GENESYS_API_KEY 2>$null
if ($LASTEXITCODE -ne 0) { $genesysKey = "" }

if ([string]::IsNullOrWhiteSpace($twilioToken) -and [string]::IsNullOrWhiteSpace($infobipKey) -and [string]::IsNullOrWhiteSpace($genesysKey)) {
    Write-Host ""
    Write-Host "Telephony Provider Selection" -ForegroundColor Yellow
    Write-Host "----------------------------"
    Write-Host "No telephony credentials detected. Choose a provider:"
    Write-Host ""
    Write-Host "  [1] Azure Communication Services (default - no extra credentials needed)"
    Write-Host "  [2] Twilio (requires Auth Token)"
    Write-Host "  [3] Infobip (requires API Key + Base URL)"
    Write-Host "  [4] Genesys AudioHook Audio Connector (requires API Key)"
    Write-Host ""
    $choice = Read-Host "Select provider [1]"
    if ([string]::IsNullOrWhiteSpace($choice)) { $choice = "1" }

    switch ($choice) {
        "2" {
            $sid = Read-Host "Enter Twilio Account SID"
            if ($sid -notmatch '^AC[a-f0-9]{32}$') {
                Write-Host "ERROR: Invalid Twilio Account SID format (expected AC + 32 hex characters)." -ForegroundColor Red
                exit 1
            }
            $token = Read-Host "Enter Twilio Auth Token" -AsSecureString
            $tokenPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($token))
            if ($tokenPlain.Length -ne 32 -or $tokenPlain -notmatch '^[a-f0-9]+$') {
                Write-Host "ERROR: Invalid Twilio Auth Token format (expected 32 hex characters)." -ForegroundColor Red
                exit 1
            }
            # Validate credentials against Twilio API
            Write-Host "Validating Twilio credentials..." -ForegroundColor Gray
            $authHeader = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("${sid}:${tokenPlain}"))
            try {
                $resp = Invoke-RestMethod -Uri "https://api.twilio.com/2010-04-01/Accounts/$sid.json" `
                    -Headers @{ Authorization = "Basic $authHeader" } -Method Get -ErrorAction Stop
                Write-Host "Twilio account verified: $($resp.friendly_name)" -ForegroundColor Green
            }
            catch {
                $status = $_.Exception.Response.StatusCode.value__
                if ($status -eq 401) {
                    Write-Host "ERROR: Twilio credentials are invalid (401 Unauthorized)." -ForegroundColor Red
                }
                else {
                    Write-Host "ERROR: Failed to validate Twilio credentials (HTTP $status)." -ForegroundColor Red
                }
                exit 1
            }
            azd env set TWILIO_ACCOUNT_SID $sid
            azd env set TWILIO_AUTH_TOKEN $tokenPlain
            Write-Host "Twilio configured." -ForegroundColor Green
        }
        "3" {
            $key = Read-Host "Enter Infobip API Key" -AsSecureString
            $keyPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($key))
            $baseUrl = Read-Host "Enter Infobip API Base URL (e.g. https://xxxxx.api.infobip.com)"
            $baseUrl = $baseUrl.TrimEnd('/')
            if ($baseUrl -notmatch '^https?://') { $baseUrl = "https://$baseUrl" }
            if ($baseUrl -notmatch '^https://[a-z0-9]+\.api(-[a-z0-9]+)?\.infobip\.com$') {
                Write-Host "ERROR: Invalid Infobip Base URL format." -ForegroundColor Red
                Write-Host "  Expected: https://<id>.api.infobip.com or https://<id>.api-<region>.infobip.com" -ForegroundColor Gray
                exit 1
            }
            # Validate credentials against Infobip API
            try {
                $resp = Invoke-WebRequest -Uri "$baseUrl/settings/1/accounts" `
                    -Headers @{Authorization = "App $keyPlain"} -UseBasicParsing -ErrorAction Stop
                Write-Host "Infobip credentials validated." -ForegroundColor Green
            }
            catch {
                $status = $_.Exception.Response.StatusCode.value__
                if ($status -eq 401) {
                    Write-Host "ERROR: Infobip API key is invalid (401 Unauthorized)." -ForegroundColor Red
                    exit 1
                }
                # 403 means key is recognized but lacks admin scope — still valid for calls
                Write-Host "Infobip API key verified." -ForegroundColor Green
            }
            azd env set INFOBIP_API_KEY $keyPlain
            azd env set INFOBIP_API_BASE_URL $baseUrl
            Write-Host "Infobip configured." -ForegroundColor Green
        }
        "4" {
            Write-Host ""
            Write-Host "Genesys AudioHook Audio Connector" -ForegroundColor Yellow
            Write-Host "This key authenticates Genesys Cloud when it connects to your /audiohook/ws endpoint."
            Write-Host "You define this value and configure the same key in Genesys Cloud."
            Write-Host ""
            $gKey = Read-Host "Enter API Key for AudioHook authentication"
            if ([string]::IsNullOrWhiteSpace($gKey)) {
                Write-Host "ERROR: API Key is required." -ForegroundColor Red
                exit 1
            }
            azd env set GENESYS_API_KEY $gKey
            Write-Host "Genesys AudioHook configured." -ForegroundColor Green
            Write-Host ""
            Write-Host "After deployment, the post-deploy script will show your WebSocket URL and simulator link." -ForegroundColor Cyan
        }
        default {
            Write-Host "Using Azure Communication Services (will be provisioned automatically)." -ForegroundColor Green
        }
    }
}
else {
    if (-not [string]::IsNullOrWhiteSpace($twilioToken)) {
        Write-Host "Telephony: Twilio (credentials detected)" -ForegroundColor Green
    }
    elseif (-not [string]::IsNullOrWhiteSpace($infobipKey)) {
        Write-Host "Telephony: Infobip (credentials detected)" -ForegroundColor Green
    }
    elseif (-not [string]::IsNullOrWhiteSpace($genesysKey)) {
        Write-Host "Telephony: Genesys AudioHook (credentials detected)" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Pre-provisioning checks passed. Proceeding..." -ForegroundColor Green
Write-Host ""
