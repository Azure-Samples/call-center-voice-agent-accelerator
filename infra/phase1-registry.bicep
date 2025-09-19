targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the the environment which is used to generate a short unique hash used in all resources.')
param environmentName string

@minLength(1)
@description('Primary location for all resources')
param location string

// Generate a unique suffix for resources (must match main.bicep)
var uniqueSuffix = substring(uniqueString(subscription().id, environmentName), 0, 5)

// Create resource group
resource rg 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: '${environmentName}-rg'
  location: location
}

// Create managed identity
module appIdentity 'modules/identity.bicep' = {
  name: 'app-identity'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
  }
}

// Create container registry (Phase 1)
module registry 'modules/containerregistry.bicep' = {
  name: 'registry'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
    identityName: appIdentity.outputs.name
    tags: {}
  }
}

// OUTPUTS for Phase 1 - only registry information
output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_USER_ASSIGNED_IDENTITY_ID string = appIdentity.outputs.identityId
output AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID string = appIdentity.outputs.clientId
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.outputs.loginServer
output AZURE_CONTAINER_REGISTRY_NAME string = registry.outputs.name
