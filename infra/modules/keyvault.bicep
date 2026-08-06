param location string
param keyVaultName string
param tags object
@secure()
param acsConnectionString string
@secure()
param twilioAuthToken string = ''
@secure()
param infobipApiKey string = ''
@secure()
param genesysApiKey string = ''
@secure()
param sinchApplicationKey string = ''
@secure()
param sinchApplicationSecret string = ''
@secure()
param bandwidthClientId string = ''
@secure()
param bandwidthClientSecret string = ''

resource keyVault 'Microsoft.KeyVault/vaults@2023-02-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    accessPolicies: []
    enableRbacAuthorization: true
    enableSoftDelete: true
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
  }
}


resource acsConnectionStringSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(acsConnectionString)) {
  parent: keyVault
  name: 'ACS-CONNECTION-STRING'
  properties: {
    value: acsConnectionString
  }
}

var keyVaultDnsSuffix = environment().suffixes.keyvaultDns

resource twilioAuthTokenSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(twilioAuthToken)) {
  parent: keyVault
  name: 'TWILIO-AUTH-TOKEN'
  properties: {
    value: twilioAuthToken
  }
}

resource infobipApiKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(infobipApiKey)) {
  parent: keyVault
  name: 'INFOBIP-API-KEY'
  properties: {
    value: infobipApiKey
  }
}

resource genesysApiKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(genesysApiKey)) {
  parent: keyVault
  name: 'GENESYS-API-KEY'
  properties: {
    value: genesysApiKey
  }
}

resource sinchApplicationKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(sinchApplicationKey)) {
  parent: keyVault
  name: 'SINCH-APPLICATION-KEY'
  properties: {
    value: sinchApplicationKey
  }
}

resource sinchApplicationSecretSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(sinchApplicationSecret)) {
  parent: keyVault
  name: 'SINCH-APPLICATION-SECRET'
  properties: {
    value: sinchApplicationSecret
  }
}

resource bandwidthClientIdSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(bandwidthClientId)) {
  parent: keyVault
  name: 'BANDWIDTH-CLIENT-ID'
  properties: {
    value: bandwidthClientId
  }
}

resource bandwidthClientSecretSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(bandwidthClientSecret)) {
  parent: keyVault
  name: 'BANDWIDTH-CLIENT-SECRET'
  properties: {
    value: bandwidthClientSecret
  }
}

output acsConnectionStringUri string = !empty(acsConnectionString) ? 'https://${keyVault.name}${keyVaultDnsSuffix}/secrets/${acsConnectionStringSecret.name}' : ''
output twilioAuthTokenUri string = !empty(twilioAuthToken) ? 'https://${keyVault.name}${keyVaultDnsSuffix}/secrets/TWILIO-AUTH-TOKEN' : ''
output infobipApiKeyUri string = !empty(infobipApiKey) ? 'https://${keyVault.name}${keyVaultDnsSuffix}/secrets/INFOBIP-API-KEY' : ''
output genesysApiKeyUri string = !empty(genesysApiKey) ? 'https://${keyVault.name}${keyVaultDnsSuffix}/secrets/GENESYS-API-KEY' : ''
output sinchApplicationKeyUri string = !empty(sinchApplicationKey) ? 'https://${keyVault.name}${keyVaultDnsSuffix}/secrets/SINCH-APPLICATION-KEY' : ''
output sinchApplicationSecretUri string = !empty(sinchApplicationSecret) ? 'https://${keyVault.name}${keyVaultDnsSuffix}/secrets/SINCH-APPLICATION-SECRET' : ''
output bandwidthClientIdUri string = !empty(bandwidthClientId) ? 'https://${keyVault.name}${keyVaultDnsSuffix}/secrets/BANDWIDTH-CLIENT-ID' : ''
output bandwidthClientSecretUri string = !empty(bandwidthClientSecret) ? 'https://${keyVault.name}${keyVaultDnsSuffix}/secrets/BANDWIDTH-CLIENT-SECRET' : ''
output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
