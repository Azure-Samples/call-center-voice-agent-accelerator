param location string
param containerRegistryName string
param repositoryName string = 'voice-live-agent/app-voiceagent'
param imageTag string = 'latest'
param githubRepo string = 'https://github.com/team-rrr/call-center-voice-agent-accelerator-hackathon.git'
param branch string = 'bicep-power'
param identityId string
param tags object = {}

// Get reference to existing container registry
resource containerRegistry 'Microsoft.ContainerRegistry/registries@2022-02-01-preview' existing = {
  name: containerRegistryName
}

// Get reference to the user assigned identity
resource userAssignedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(identityId, '/'))
}

// Grant the identity ACR Push permission
resource acrPushRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: containerRegistry
  name: guid(subscription().id, resourceGroup().id, userAssignedIdentity.id, 'acrPushRole')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8311e382-0749-4cb8-b61a-304f252e45ec') // AcrPush role
    principalType: 'ServicePrincipal'
    principalId: userAssignedIdentity.properties.principalId
  }
}

// Grant the identity ACR Build permission (needed for az acr build command)
resource acrBuildRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: containerRegistry
  name: guid(subscription().id, resourceGroup().id, userAssignedIdentity.id, 'acrBuildRole')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c') // Contributor role (needed for builds)
    principalType: 'ServicePrincipal' 
    principalId: userAssignedIdentity.properties.principalId
  }
}

// Use deployment script to build and push the container image
resource containerBuild 'Microsoft.Resources/deploymentScripts@2023-08-01' = {
  name: 'build-container-image'
  location: location
  tags: tags
  kind: 'AzureCLI'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    azCliVersion: '2.63.0'
    timeout: 'PT30M'
    retentionInterval: 'PT1H'
    environmentVariables: [
      {
        name: 'REGISTRY_NAME'
        value: containerRegistryName
      }
      {
        name: 'REPOSITORY_NAME'
        value: repositoryName
      }
      {
        name: 'IMAGE_TAG'
        value: imageTag
      }
      {
        name: 'GITHUB_REPO'
        value: githubRepo
      }
      {
        name: 'BRANCH'
        value: branch
      }
    ]
    scriptContent: '''
      echo "Starting container build process..."
      echo "Registry: ${REGISTRY_NAME}"
      echo "GitHub Repo: ${GITHUB_REPO}"
      echo "Branch: ${BRANCH}"
      
      # Install git if not available
      which git || (apt-get update && apt-get install -y git)
      version=$(git --version)
      echo "Git version: $version"
      # Clone the repository
      echo "Cloning repository..."
      git clone https://github.com/team-rrr/call-center-voice-agent-accelerator-hackathon.git
      cd call-center-voice-agent-accelerator-hackathon

      
      # Verify we have the server directory and Dockerfile
      ls 
      
      
      # Login to Azure Container Registry
      echo "Logging into ACR..."
      az acr login --name $REGISTRY_NAME
      
      # Build and push the container image from the server directory
      echo "Building container image..."
      az acr build -r testalon4vfp2h.azurecr.io -t voice-live-agent/app-voiceagent:latest ./server
      
      echo "Container image built and pushed successfully"
      echo "Image: $REGISTRY_NAME.azurecr.io/$REPOSITORY_NAME:$IMAGE_TAG"
    '''
  }
  dependsOn: [acrPushRole, acrBuildRole]
}

output imageName string = '${containerRegistryName}.azurecr.io/${repositoryName}:${imageTag}'
output buildId string = containerBuild.id
