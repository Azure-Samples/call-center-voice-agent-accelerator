<#
.SYNOPSIS
    Post-deploy: ACS — automatically creates Event Grid subscription for incoming calls.
#>

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

# Parse the webhook endpoint
$endpointList = @($endpoints | ConvertFrom-Json)
$webhookUrl = $endpointList[0]

if ([string]::IsNullOrWhiteSpace($webhookUrl)) {
    Write-Host "ERROR: Could not determine webhook URL from SERVICE_API_ENDPOINTS." -ForegroundColor Red
    exit 1
}

# Find the ACS resource in the resource group
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
