param location string
param keyVaultName string
param tags object
@secure()
param acsConnectionString string
@secure()
param twilioAuthToken string = ''
@secure()
param infobipApiKey string = ''

var sanitizedKeyVaultName = keyVaultName

resource keyVault 'Microsoft.KeyVault/vaults@2023-02-01' = {
  name: sanitizedKeyVaultName
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

output acsConnectionStringUri string = !empty(acsConnectionString) ? 'https://${keyVault.name}${keyVaultDnsSuffix}/secrets/${acsConnectionStringSecret.name}' : ''
output twilioAuthTokenUri string = !empty(twilioAuthToken) ? 'https://${keyVault.name}${keyVaultDnsSuffix}/secrets/TWILIO-AUTH-TOKEN' : ''
output infobipApiKeyUri string = !empty(infobipApiKey) ? 'https://${keyVault.name}${keyVaultDnsSuffix}/secrets/INFOBIP-API-KEY' : ''
output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
