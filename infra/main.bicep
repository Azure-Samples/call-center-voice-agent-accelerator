targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the the environment which is used to generate a short unique hash used in all resources.')
param environmentName string

@minLength(1)
@description('Primary location for all resources (filtered on available regions for Azure Open AI Service).')
@allowed([
  'eastus2'
  'swedencentral'
])
param location string

@description('Id of the user or app to assign application roles')
param principalId string = ''

var uniqueSuffix = substring(uniqueString(subscription().id, environmentName), 0, 5)
var appExists = !empty(principalId)
var tags = {'azd-env-name': environmentName }
var rgName = '${environmentName}-rg'
var modelName = 'gpt-4o-mini'

// Reference existing resource group (created by phase1)
resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' existing = {
  name: rgName
}

// Reference existing identity (created by phase1)
var sanitizedEnvName = toLower(replace(replace(replace(replace(environmentName, ' ', ''), '--', ''), '[^a-zA-Z0-9-]', ''), '_', ''))
var userIdentityName = take('${sanitizedEnvName}-${uniqueSuffix}-id', 32)
resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  scope: rg
  name: userIdentityName
}

// Reference existing registry (created by phase1)
var sanitizedEnvName2 = toLower(replace(replace(replace(replace(environmentName, ' ', '-'), '--', '-'), '[^a-zA-Z0-9-]', ''), '_', '-'))
var containerRegistryName = take('${sanitizedEnvName}${uniqueSuffix}', 32)
resource registry 'Microsoft.ContainerRegistry/registries@2022-02-01-preview' existing = {
  scope: rg
  name: containerRegistryName
}

// Create monitoring, AI services, and other infrastructure (not included in phase1)
var logAnalyticsName = take('log-${sanitizedEnvName2}-${uniqueSuffix}', 63)
var appInsightsName = take('insights-${sanitizedEnvName2}-${uniqueSuffix}', 63)
module monitoring 'modules/monitoring/monitor.bicep' = {
  name: 'monitor'
  scope: rg
  params: {
    logAnalyticsName: logAnalyticsName
    appInsightsName: appInsightsName
    tags: tags
  }
}

module aiServices 'modules/aiservices.bicep' = {
  name: 'ai-foundry-deployment'
  scope: rg
  params: {
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
    identityId: appIdentity.id
    tags: tags
  }
}

module acs 'modules/acs.bicep' = {
  name: 'acs-deployment'
  scope: rg
  params: {
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
    tags: tags
  }
}

var keyVaultName = toLower(replace('kv-${environmentName}-${uniqueSuffix}', '_', '-'))
var sanitizedKeyVaultName = take(toLower(replace(replace(replace(replace(keyVaultName, '--', '-'), '_', '-'), '[^a-zA-Z0-9-]', ''), '-$', '')), 24)
module keyvault 'modules/keyvault.bicep' = {
  name: 'keyvault-deployment'
  scope: rg
  params: {
    location: location
    keyVaultName: sanitizedKeyVaultName
    tags: tags
    aiServicesKey: aiServices.outputs.aiServicesKey
    acsConnectionString: acs.outputs.acsConnectionString
  }
}

// Add role assignments 
module RoleAssignments 'modules/roleassignments.bicep' = {
  scope: rg
  name: 'role-assignments'
  params: {
    identityPrincipalId: appIdentity.properties.principalId
    aiServicesId: aiServices.outputs.aiServicesId
    keyVaultName: sanitizedKeyVaultName
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
    identityId: appIdentity.id
    containerRegistryName: registry.name
    containerImageName: '${registry.name}.azurecr.io/voice-live-agent/app-voiceagent:latest'
    aiServicesEndpoint: aiServices.outputs.aiServicesEndpoint
    modelDeploymentName: modelName
    aiServicesKeySecretUri: keyvault.outputs.aiServicesKeySecretUri
    acsConnectionStringSecretUri: keyvault.outputs.acsConnectionStringUri
    logAnalyticsWorkspaceName: logAnalyticsName
  }
}


// OUTPUTS will be saved in azd env for later use
output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_USER_ASSIGNED_IDENTITY_ID string = appIdentity.id
output AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID string = appIdentity.properties.clientId

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.properties.loginServer
output AZURE_CONTAINER_REGISTRY_NAME string = registry.name

output SERVICE_API_ENDPOINTS array = ['${containerapp.outputs.containerAppFqdn}/acs/incomingcall']
