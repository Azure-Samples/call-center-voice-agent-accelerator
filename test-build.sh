#!/bin/bash

# Test script to build container image locally
# This helps verify everything works before running azd up

set -e

echo "Testing local container build..."

# Check if we're in the right directory
if [ ! -d "server" ] || [ ! -f "server/Dockerfile" ]; then
    echo "ERROR: This script should be run from the root of the repository"
    echo "Make sure you have the 'server' directory with Dockerfile"
    exit 1
fi

# Get registry name from azd environment (if already provisioned)
REGISTRY_NAME=$(azd env get-values --output json 2>/dev/null | jq -r '.AZURE_CONTAINER_REGISTRY_NAME // empty' || echo "")

if [ -z "$REGISTRY_NAME" ]; then
    echo "No registry found in azd environment yet."
    echo "You can run this script after 'azd provision' completes."
    echo ""
    echo "For now, testing local docker build..."
    
    # Test local docker build
    if command -v docker &> /dev/null; then
        echo "Building container locally..."
        docker build -t voice-live-agent:test ./server
        echo "Local container build successful!"
    else
        echo "Docker not found. Install Docker to test local builds."
    fi
else
    echo "Found registry: $REGISTRY_NAME"
    echo "Testing ACR build..."
    
    # Login to ACR
    az acr login --name $REGISTRY_NAME
    
    # Build and push
    az acr build --registry $REGISTRY_NAME \
                 --image voice-live-agent/app-voiceagent:latest \
                 --file ./server/Dockerfile \
                 ./server
    
    echo "ACR build successful!"
fi