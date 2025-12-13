param environmentName string
param uniqueSuffix string
param identityId string
param tags object
param disableLocalAuth bool = true

// Voice live api only supported on two regions now 
var location string = 'swedencentral'
var aiServicesName string = take('trv-ai-${environmentName}-${uniqueSuffix}', 63)

@allowed([
  'S0'
])
param sku string = 'S0'

resource aiServices 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: aiServicesName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  sku: {
    name: sku
  }
  kind: 'AIServices'
  tags: tags
  properties: {
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
    disableLocalAuth: disableLocalAuth
    customSubDomainName: take('trv-${environmentName}-${uniqueSuffix}', 63)
  }
}

@secure()
output aiServicesEndpoint string = aiServices.properties.endpoint
output aiServicesId string = aiServices.id
output aiServicesName string = aiServices.name
