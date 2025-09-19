#!/bin/bash

# Two-phase deployment script
# Phase 1: Deploy registry + identity
# Phase 2: Build image + Deploy container app

set -e

echo "=== Two-Phase Deployment ==="

echo ""
echo "Phase 1: Deploying container registry and identity..."
echo "This creates the infrastructure needed for building images"

# Deploy Phase 1 (registry only)
az deployment sub create \
    --location $(azd env get-values --output json | jq -r '.AZURE_LOCATION // "eastus"') \
    --template-file infra/phase1-registry.bicep \
    --parameters environmentName=$(azd env get-values --output json | jq -r '.AZURE_ENV_NAME') \
                 location=$(azd env get-values --output json | jq -r '.AZURE_LOCATION // "eastus"') \
    --output table

echo ""
echo "Phase 1 completed! Registry is now available."

# Get registry name from the deployment output
REGISTRY_NAME=$(az deployment sub show \
    --name phase1-registry \
    --query 'properties.outputs.AZURE_CONTAINER_REGISTRY_NAME.value' \
    --output tsv)

echo "Registry name: $REGISTRY_NAME"

echo ""
echo "Phase 2: Building container image..."

# Login to ACR
az acr login --name $REGISTRY_NAME

# Build and push image
az acr build --registry $REGISTRY_NAME \
             --image voice-live-agent/app-voiceagent:latest \
             --file ./server/Dockerfile \
             ./server

echo ""
echo "Image built successfully!"

echo ""
echo "Phase 2: Deploying container app with pre-built image..."

# Now deploy the full infrastructure (which includes the container app)
azd up --no-prompt

echo ""
echo "=== Deployment Complete ==="
echo "Registry: $REGISTRY_NAME.azurecr.io"
echo "Image: voice-live-agent/app-voiceagent:latest"