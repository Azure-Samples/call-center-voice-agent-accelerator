param location string
param keyVaultName string
param tags object

var sanitizedKeyVaultName = take(toLower(replace(replace(replace(replace(keyVaultName, '--', '-'), '_', '-'), '[^a-zA-Z0-9-]', ''), '-$', '')), 24)

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
output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
