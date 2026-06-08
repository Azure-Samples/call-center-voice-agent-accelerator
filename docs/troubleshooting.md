# Troubleshooting

Common deployment issues and solutions for the Voice Agent Accelerator template.

## Docker Hub Rate Limit During Deployment

If `azd up` or `azd deploy` fails with an error like:

```
toomanyrequests: You have reached your unauthenticated pull rate limit.
```

This happens because the remote Docker build (ACR Tasks) pulls the Python base image from Docker Hub, which rate-limits anonymous pulls to **100 requests per 6 hours** per IP. Azure's shared infrastructure IPs can exhaust this limit during peak usage.

**Option 1: Retry (simplest)**

Wait a few minutes and retry — the limit resets gradually:

```shell
azd deploy
```

**Option 2: Build locally with Docker (recommended if you have Docker installed)**

If Docker Desktop is installed on your machine, you can bypass ACR remote builds entirely:

1. Edit `azure.yaml` and change `remoteBuild` to `false`:
   ```yaml
   docker:
     path: Dockerfile
     remoteBuild: false
   ```

2. Re-deploy:
   ```shell
   azd deploy
   ```

This builds the image locally (using your own Docker Hub pull quota or cached layers) and pushes the built image to ACR.

**Option 3: Authenticate to Docker Hub (avoids anonymous limits)**

If you have a [Docker Hub account](https://hub.docker.com/signup) (free tier gives 200 pulls/6h), log in before deploying:

```shell
docker login
azd deploy
```

With `remoteBuild: false`, local builds authenticate with your Docker Hub credentials automatically.

> **Note:** This issue only affects the first build for a given image tag. Subsequent `azd deploy` runs reuse cached layers and rarely hit the limit.

## RequestConflict During Provisioning

If `azd up` fails with:

```
RequestConflict: Another operation is currently being performed on this resource.
```

This means a previous ARM deployment is still in progress on the same resource group. Common causes:
- Running `azd up` again immediately after a failed or cancelled deployment
- Multiple terminals deploying to the same environment simultaneously

**Fix:** Wait 1–2 minutes for the previous operation to complete, then retry:

```shell
azd up
```

To check for in-progress deployments:

```shell
az deployment group list -g <your-resource-group> --query "[?properties.provisioningState=='Running'].name" -o table
```

## FlagMustBeSetForRestore After `azd down`

If you get this error after tearing down and re-provisioning:

```
FlagMustBeSetForRestore: An existing resource with ID '...' has been soft-deleted.
```

This is already handled — the template sets `restore: true` on AI Services resources. If you still hit it (e.g., after manual portal deletions), purge the soft-deleted resource:

```shell
az cognitiveservices account purge --name <resource-name> --location <location> --resource-group <resource-group>
```

Then retry `azd up`.

## AuthorizationFailed During Provisioning

If you get:

```
AuthorizationFailed: The client does not have authorization to perform action.
```

The template creates resources at **subscription scope** (resource group creation) and assigns RBAC roles. Your account needs:

| Role | Scope | Why |
|------|-------|-----|
| **Contributor** | Subscription | Creates resource group and all resources |
| **Role Based Access Control Administrator** | Subscription | Assigns managed identity roles (Key Vault, AI Services, ACR) |

Check your current roles:

```shell
az role assignment list --assignee $(az ad signed-in-user show --query id -o tsv) --query "[].roleDefinitionName" -o table
```

If you only have resource group-level permissions, ask your subscription admin to grant Contributor + RBAC Administrator at subscription scope, or have them pre-create the resource group and grant you Owner on it.
