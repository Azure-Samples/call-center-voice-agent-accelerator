# Production Readiness

This accelerator is intended to help you start quickly. Before using it for production traffic, review the design areas below and decide which changes are required for your expected call volume and compliance needs.

## Core Design Areas

The template already provisions Key Vault, managed identity, Application Insights, Log Analytics, and Container Apps. The guidance below covers what to review or add for your specific production workload.

### State and Session Storage

The template keeps short-lived call state in memory. This is a reasonable default for pilots, demos, and simple production deployments that run one active replica.

You only need a shared store when you plan to run multiple replicas or need stronger recovery after restarts. In that case, use a managed cache with automatic expiration, such as Azure Cache for Redis, for short-lived records like:
- one-time WebSocket authentication tokens
- pending call setup state (e.g. Infobip Dialog creation, waiting for provider callbacks)
- provider call IDs used to look up logs or customer records after a call

For the first production deployment, a practical path is:
1. Start with one active replica and the built-in in-memory state.
2. Add dashboards and alerts so you understand real call volume and failure patterns.
3. Move provider lifecycle state to a TTL cache before enabling multi-replica scale-out.

### WebSocket Affinity and Scaling

Telephony media streams are long-running WebSocket connections. Validate your scale-out behavior before production:
- Set realistic minimum and maximum replica counts.
- Load test concurrent calls, not only HTTP requests.
- Treat active Voice Live media sessions as non-resumable. If a replica restarts or a media WebSocket breaks, end the call cleanly and rely on the provider's normal call flow for any new call attempt.
- Confirm webhook retry behavior for provider lifecycle callbacks, which are separate from live media streams.

**Redeployment drops active calls.** When you redeploy the Container App, all in-progress calls on that replica are terminated. Plan deployments during low-traffic windows or use blue-green deployment strategies to drain calls before switching.

### Concurrency Limits

Two limits gate how many calls you can handle:

1. **Azure AI Services (Voice Live) quota** — your Speech resource has a maximum number of concurrent Voice Live sessions. Check your quota in the Azure Portal under your Speech resource → Quotas before expecting high call volume.
2. **Per-replica capacity** — the template defaults to 50 concurrent calls per replica (configurable in `CallManager`). The actual ceiling depends on your Container App workload profile (vCPU and memory allocation), since each call holds a WebSocket connection and may perform audio transcoding.

Load test to find the point where latency degrades, then set your `CallManager` limit below that.

### Network Requirements

The application requires:
- **Inbound** — HTTPS/WSS from your telephony provider (webhooks and media streams). Your provider's IP ranges must be allowed if you use network restrictions.
- **Outbound** — HTTPS to Azure AI Services (Voice Live), Azure Key Vault, and your provider's management APIs.

### Authentication and Secrets

The template already stores provider secrets in Azure Key Vault and uses managed identity for Azure-hosted access.

Before production, verify:
- provider secrets are set in Key Vault, not in source code or local files
- the Container App identity has only the Azure permissions it needs
- logs do not include tokens, request signatures, or full webhook payloads that may contain personal data

As operations mature:
- rotate provider secrets on a defined schedule
- document who can rotate secrets and how to update the telephony provider configuration
- review RBAC assignments periodically

### Observability

The template provisions Application Insights and Log Analytics, and every log line includes a `cid` for tracing calls (see [Debugging Calls](../README.md#debugging-calls) in the README).

For production, add dashboards and alerts for:
- rejected calls (look for "Too Many Connections" or 4429 close codes)
- idle disconnects (look for "Call expired" or "receive timeout")
- Voice Live connection failures
- provider webhook failures (HTTP 4xx/5xx responses)
- Container App restarts, replica count, CPU, and memory

### Resilience and Retry Policy

The template already handles several failure cases:
- per-event error handling in callback batches (one bad event does not abort the rest)
- timeouts on WebSocket authentication handshakes
- concurrency limits that reject new calls when at capacity
- clean call teardown when the media WebSocket or Voice Live session breaks

Before production, add:
- bounded retries with exponential backoff for outbound HTTP calls to your provider's management API
- alerts on repeated failures so you know when a provider endpoint is down
- testing with provider callback retries and duplicate events to confirm idempotent handling

Do not retry real-time audio sends. If the media WebSocket is broken, end the call cleanly instead of buffering audio.

### Privacy, Safety, and Compliance

The template streams live audio to Azure Voice Live and writes operational logs. It does not intentionally store call recordings or transcripts. Before production, review your own configuration and any custom changes for:
- whether your use case requires caller notice or consent
- whether logs could contain personal data from provider webhook payloads or custom code
- how long operational logs should be retained
- prompt behavior, fallback messages, and escalation paths for your scenario
- any regional data residency requirements for your organization

## Provider-Specific Notes

Most production concerns (alerts, scaling, secret rotation, post-call ID storage) apply equally to all providers and are covered in the Core Design Areas above. The notes below call out behavior unique to a specific provider.

**Infobip** — The template keeps `_answered_calls`, `_pending_media_streams`, and one-time WebSocket tokens in memory. Move these to a shared TTL store before enabling multi-replica scale-out. Tune token and pending-call expiration to match your expected call setup timing.

**Web Browser Client** — The browser client is a development and testing tool, not intended for production use.

## Suggested Implementation Roadmap

1. Deploy with one replica and run test calls to validate end-to-end behavior.
2. Add dashboards and alerts on top of the provisioned Application Insights and Log Analytics.
3. Load test concurrent WebSocket calls to find your per-replica capacity.
4. Add retry and timeout policies for outbound provider management API calls.
5. Review privacy, retention, and responsible AI requirements.
6. If you need multiple replicas: add shared TTL state, then validate how active calls behave when a replica restarts before enabling autoscale.

## References

- [Azure Well-Architected Framework](https://learn.microsoft.com/azure/well-architected/)
- [Azure Container Apps reliability](https://learn.microsoft.com/azure/container-apps/)
- [Azure Cache for Redis overview](https://learn.microsoft.com/azure/azure-cache-for-redis/cache-overview)
- [Application Insights overview](https://learn.microsoft.com/azure/azure-monitor/app/app-insights-overview)
- [Azure Key Vault best practices](https://learn.microsoft.com/azure/key-vault/general/best-practices)
- [Voice Live API transparency note](https://learn.microsoft.com/azure/ai-foundry/responsible-ai/speech-service/voice-live/transparency-note)