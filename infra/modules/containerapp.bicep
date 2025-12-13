param location string
param environmentName string
param uniqueSuffix string
param tags object
param exists bool
param identityId string
param identityClientId string
param containerRegistryName string
param aiServicesEndpoint string
param modelDeploymentName string
param logAnalyticsWorkspaceName string
@description('The name of the container image')
param imageName string = ''

// Helper to sanitize environmentName for valid container app name
var sanitizedEnvName = toLower(replace(replace(replace(replace(environmentName, ' ', '-'), '--', '-'), '[^a-zA-Z0-9-]', ''), '_', '-'))
var containerAppName = take('trv-app-${sanitizedEnvName}-${uniqueSuffix}', 32)
var containerEnvName = take('trv-env-${sanitizedEnvName}-${uniqueSuffix}', 32)

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' existing = { name: logAnalyticsWorkspaceName }


module fetchLatestImage './fetch-container-image.bicep' = {
  name: '${containerAppName}-fetch-image'
  params: {
    exists: exists
    name: containerAppName
  }
}

resource containerAppEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: containerEnvName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspace.properties.customerId
        sharedKey: logAnalyticsWorkspace.listKeys().primarySharedKey
      }
    }
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-10-02-preview' = {
  name: containerAppName
  location: location
  tags: union(tags, { 'azd-service-name': 'twilio-realtime-voice' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      registries: [
        {
          server: '${containerRegistryName}.azurecr.io'
          identity: identityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'twilio-realtime-voice'
          image: !empty(imageName) ? imageName : 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          env: [
            {
              name: 'AZURE_VOICE_LIVE_ENDPOINT'
              value: aiServicesEndpoint
            }
            {
              name: 'AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID'
              value: identityClientId
            }
            {
              name: 'VOICE_LIVE_MODEL'
              value: modelDeploymentName
            }
            {
              name: 'DEBUG_MODE'
              value: 'true'
            }
          ]
          resources: {
            cpu: json('2.0')
            memory: '4.0Gi'
          }
        }
      ]
      // TODO add memory/cpu scaling
      scale: {
        minReplicas: 1
        maxReplicas: 10
        rules: [
          {
            name: 'http-scaler'
            http: {
              metadata: {
                concurrentRequests: '100'
              }
            }
          }
        ]
      }
    }
  }
}

output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output containerAppId string = containerApp.id
