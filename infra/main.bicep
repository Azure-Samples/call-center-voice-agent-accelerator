targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the the environment which is used to generate a short unique hash used in all resources.')
param environmentName string

@minLength(1)
@description('Primary location for all resources. Regions with pre-deployed models (gpt-4o-mini): eastus2, japaneast, southeastasia, swedencentral, westus2. Other regions require BYOM. See https://learn.microsoft.com/azure/ai-services/speech-service/regions?tabs=voice-live')
@allowed([
  'australiaeast'
  'brazilsouth'
  'canadaeast'
  'eastus'
  'eastus2'
  'francecentral'
  'germanywestcentral'
  'italynorth'
  'japaneast'
  'norwayeast'
  'southafricanorth'
  'southcentralus'
  'southeastasia'
  'swedencentral'
  'switzerlandnorth'
  'uksouth'
  'westeurope'
  'westus'
  'westus2'
  'westus3'
])
param location string

param appExists bool
@description('The OpenAI model name')
param modelName string = 'gpt-4o-mini'
@description('The selected telephony provider')
@allowed(['acs', 'twilio', 'infobip', 'genesys'])
param telephonyProvider string = 'acs'
@secure()
@description('Twilio Auth Token for webhook signature validation')
param twilioAuthToken string = ''
@secure()
@description('Infobip API Key for voice call handling')
param infobipApiKey string = ''
@description('Infobip API Base URL (e.g. https://xxxxx.api.infobip.com)')
param infobipApiBaseUrl string = ''
@secure()
@description('Genesys AudioHook API Key for Audio Connector authentication')
param genesysApiKey string = ''
@description('Enable debug mode for verbose logging in the container app')
param debugMode bool = false

var uniqueSuffix = substring(uniqueString(subscription().id, environmentName), 0, 5)
var tags = {'azd-env-name': environmentName }
var rgName = 'rg-${environmentName}-${uniqueSuffix}'

resource rg 'Microsoft.Resources/resourceGroups@2024-11-01' = {
  name: rgName
  location: location
  tags: tags
}

// [ User Assigned Identity for App to avoid circular dependency ]
module appIdentity './modules/identity.bicep' = {
  name: 'uami'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
  }
}

var sanitizedEnvName = toLower(replace(replace(replace(environmentName, ' ', '-'), '--', '-'), '_', '-'))
var logAnalyticsName = take('log-${sanitizedEnvName}-${uniqueSuffix}', 63)
var appInsightsName = take('insights-${sanitizedEnvName}-${uniqueSuffix}', 63)
module monitoring 'modules/monitoring/monitor.bicep' = {
  name: 'monitor'
  scope: rg
  params: {
    logAnalyticsName: logAnalyticsName
    appInsightsName: appInsightsName
    tags: tags
  }
}

module registry 'modules/containerregistry.bicep' = {
  name: 'registry'
  scope: rg
  params: {
    location: location
    uniqueSuffix: uniqueSuffix
    identityName: appIdentity.outputs.name
    tags: tags
  }
}


module aiServices 'modules/aiservices.bicep' = {
  name: 'ai-foundry-deployment'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
    identityId: appIdentity.outputs.identityId
    tags: tags
  }
}

module acs 'modules/acs.bicep' = if (telephonyProvider == 'acs') {
  name: 'acs-deployment'
  scope: rg
  params: {
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
    tags: tags
  }
}

var rawKvName = take(toLower(replace(replace(replace(replace('kv-${environmentName}-${uniqueSuffix}', ' ', ''), '.', ''), '--', '-'), '_', '')), 24)
var keyVaultName = endsWith(rawKvName, '-') ? take(rawKvName, length(rawKvName) - 1) : rawKvName
module keyvault 'modules/keyvault.bicep' = {
  name: 'keyvault-deployment'
  scope: rg
  params: {
    location: location
    keyVaultName: keyVaultName
    tags: tags
    #disable-next-line BCP327
    acsConnectionString: (telephonyProvider == 'acs') ? acs.outputs.acsConnectionString : ''
    twilioAuthToken: twilioAuthToken
    infobipApiKey: infobipApiKey
    genesysApiKey: genesysApiKey
  }
}

// Add role assignments 
module RoleAssignments 'modules/roleassignments.bicep' = {
  scope: rg
  name: 'role-assignments'
  params: {
    identityPrincipalId: appIdentity.outputs.principalId
    aiServicesId: aiServices.outputs.aiServicesId
    keyVaultName: keyVaultName
  }
  dependsOn: [ keyvault ]
}

module containerapp 'modules/containerapp.bicep' = {
  name: 'containerapp-deployment'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
    tags: tags
    exists: appExists
    identityId: appIdentity.outputs.identityId
    identityClientId: appIdentity.outputs.clientId
    containerRegistryName: registry.outputs.name
    aiServicesEndpoint: aiServices.outputs.aiServicesEndpoint
    modelDeploymentName: modelName
    acsConnectionStringSecretUri: keyvault.outputs.acsConnectionStringUri
    twilioAuthTokenSecretUri: keyvault.outputs.twilioAuthTokenUri
    infobipApiKeySecretUri: keyvault.outputs.infobipApiKeyUri
    infobipApiBaseUrl: infobipApiBaseUrl
    genesysApiKeySecretUri: keyvault.outputs.genesysApiKeyUri
    logAnalyticsWorkspaceName: logAnalyticsName
    debugMode: debugMode
    imageName: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
  }
  dependsOn: [RoleAssignments]
}


// OUTPUTS will be saved in azd env for later use
output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_USER_ASSIGNED_IDENTITY_ID string = appIdentity.outputs.identityId
output AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID string = appIdentity.outputs.clientId

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.outputs.loginServer

// Provider endpoint mapping — add new providers here
var providerEndpoints = {
  acs: 'https://${containerapp.outputs.containerAppFqdn}/acs/incomingcall'
  twilio: 'https://${containerapp.outputs.containerAppFqdn}/voice'
  infobip: 'https://${containerapp.outputs.containerAppFqdn}/infobip/incoming'
  genesys: 'wss://${containerapp.outputs.containerAppFqdn}/audiohook/ws'
}
output SERVICE_API_ENDPOINTS array = [providerEndpoints[telephonyProvider]]
output AZURE_VOICE_LIVE_ENDPOINT string = aiServices.outputs.aiServicesEndpoint
output AZURE_VOICE_LIVE_MODEL string = modelName
