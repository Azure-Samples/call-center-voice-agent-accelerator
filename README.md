# Call Center Voice Agent Accelerator with Azure Voice Live API
| [![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/Azure-Samples/call-center-voice-agent-accelerator) | [![Open in Dev Containers](https://img.shields.io/static/v1?style=for-the-badge&label=Dev%20Containers&message=Open&color=blue&logo=visualstudiocode)](https://vscode.dev/redirect?url=vscode://ms-vscode-remote.remote-containers/cloneInVolume?url=https://github.com/Azure-Samples/call-center-voice-agent-accelerator)
|---|---|

Welcome to the *Call Center Real-time Voice Agent* solution accelerator — a lightweight template for building speech-to-speech voice agents powered by **Azure Voice Live API**. It supports multiple telephony providers out of the box, including **Azure Communication Services (ACS)**, **Twilio**, **Infobip**, and **Genesys Cloud (AudioHook)**, plus a **web browser** client for quick testing. Bring your own telephony provider or use the built-in options. Start locally, deploy to Azure Container Apps.

The Azure voice live API is a solution enabling low-latency, high-quality speech to speech interactions for voice agents. The API is designed for developers seeking scalable and efficient voice-driven experiences as it eliminates the need to manually orchestrate multiple components. By integrating speech recognition, generative AI, and text to speech functionalities into a single, unified interface, it provides an end-to-end solution for creating seamless experiences. Learn more about [Azure Voice Live API](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live).

The Azure Communication Services Calls Automation APIs provide telephony integration and real-time event triggers to perform actions based on custom business logic specific to their domain. Within the call automation APIs developers can use simple AI powered APIs, which can be used to play personalized greeting messages, recognize conversational voice inputs to gather information on contextual questions to drive a more self-service model with customers, use sentiment analysis to improve customer service overall. Learn more about [Azure Communication Services (Call Automation)](https://learn.microsoft.com/azure/communication-services/concepts/call-automation/call-automation).

Alternatively, telephony integration is supported through third-party providers, including [Twilio](https://www.twilio.com/docs/voice/media-streams) and [Infobip](https://www.infobip.com/docs/voice-and-video/calls).


<div align="center">
  
[**Features**](#features) \| [**Getting Started**](#getting-started) \| [**Testing the Agent**](#testing-the-agent) \| [**Local Development**](#local-development) \| [**Debugging Calls**](#debugging-calls) \| [**Production Readiness**](#production-readiness) \| [**Resources**](#resources)

</div>

<br/>

**Note:** With any AI solutions you create using these templates, you are responsible for assessing all associated risks, and for complying with all applicable laws and safety standards. Learn more in the transparency documents for [Voice Live API](https://learn.microsoft.com/azure/ai-foundry/responsible-ai/speech-service/voice-live/transparency-note) and [Azure Communication Services](https://learn.microsoft.com/azure/communication-services/concepts/privacy).

<br/>

## Features
This sample demonstrates how to build a real-time voice agent using the [Azure Speech Voice Live API](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live).

The solution includes:
- A backend service that connects to the **Voice Live API** for real-time ASR, LLM and TTS
- **Multiple client options:** The web browser client is always available. For telephony, choose **one** provider:
  - **Web browser** — microphone/speaker via WebSocket (always available, great for testing)
  - **Azure Communication Services (ACS)** — enterprise PSTN with Call Automation (default)
  - **Twilio** — PSTN via Twilio Media Streams with webhook signature validation
  - **Infobip** — PSTN via Infobip Calls API with WebSocket audio streaming
  - **Genesys Cloud** — AudioHook (Audio Connector) for real-time call audio streaming

  > **Telephony selection:** Only one telephony provider can be active at a time. The service automatically selects the provider based on the configured credentials. If no credentials are provided, Azure Communication Services is used by default.
- **Ambient Scenes** (optional): Add realistic background audio (office, call center) or use custom audio files to simulate real-world environments
- Flexible configuration to customize prompts, ASR, TTS, and behavior
- Easy extension to other client types

> You can also try the Voice Live API via [Azure AI Foundry](https://ai.azure.com/foundry) for quick experimentation before deploying this template to your own Azure subscription.

### Architecture diagram
|![Architecture Diagram](./docs/images/architecture_v0.0.4.png)|
|---|

<br/>

## Getting Started


| [![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/Azure-Samples/call-center-voice-agent-accelerator) | [![Open in Dev Containers](https://img.shields.io/static/v1?style=for-the-badge&label=Dev%20Containers&message=Open&color=blue&logo=visualstudiocode)](https://vscode.dev/redirect?url=vscode://ms-vscode-remote.remote-containers/cloneInVolume?url=https://github.com/Azure-Samples/call-center-voice-agent-accelerator)
|---|---|

### Prerequisites and Costs
To deploy this solution accelerator, ensure you have access to an [Azure subscription](https://azure.microsoft.com/free/) with the necessary permissions to create **resource groups and resources**. Follow the steps in [Azure Account Set Up](./docs/AzureAccountSetUp.md).

Check the [Azure Products by Region](https://azure.microsoft.com/explore/global-infrastructure/products-by-region/table) page and select a **region** where the following services are available: Azure AI Foundry Speech, Azure Communication Services, Azure Container Apps, and Container Registry.

See [Voice Live supported regions](https://learn.microsoft.com/azure/ai-services/speech-service/regions?tabs=voice-live) for a full list. Common choices include East US 2, Sweden Central, West US 2, and Southeast Asia.
Pricing varies per region and usage, so it isn't possible to predict exact costs for your usage. The majority of the Azure resources used in this infrastructure are on usage-based pricing tiers. However, Azure Container Registry has a fixed cost per registry per day.

Use the [Azure pricing calculator](https://azure.microsoft.com/en-us/pricing/calculator) to calculate the cost of this solution in your subscription.

| Product | Description | Cost |
|---|---|---|
| [Azure Speech Voice Live ](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live/) | Low-latency and high-quality speech to speech interactions | [Pricing](https://azure.microsoft.com/pricing/details/cognitive-services/speech-services/) |
| [Azure Communication Services](https://learn.microsoft.com/azure/communication-services/overview) | Server-based intelligent call workflows | [Pricing](https://azure.microsoft.com/pricing/details/communication-services/) |
| [Azure Container Apps](https://learn.microsoft.com/azure/container-apps/) | Hosts the web application frontend | [Pricing](https://azure.microsoft.com/pricing/details/container-apps/) |
| [Azure Container Registry](https://learn.microsoft.com/azure/container-registry/) | Stores container images for deployment | [Pricing](https://azure.microsoft.com/pricing/details/container-registry/) |


Here are some developers tools to set up as prerequisites:
- [Azure CLI](https://learn.microsoft.com/cli/azure/what-is-azure-cli): `az`
- [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/overview): `azd`
- [Python](https://www.python.org/about/gettingstarted/): `python`
- [UV](https://docs.astral.sh/uv/getting-started/installation/): `uv`
- Optionally [Docker](https://www.docker.com/get-started/): `docker`


### Deployment Options
Pick from the options below to see step-by-step instructions for: GitHub Codespaces, VS Code Dev Containers, Local Environments, and Bicep deployments.

<details>
  <summary><b>Deploy in GitHub Codespaces</b></summary>
  
### GitHub Codespaces

You can run this solution using GitHub Codespaces. The button will open a web-based VS Code instance in your browser:

1. Open the solution accelerator (this may take several minutes):

    [![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/Azure-Samples/call-center-voice-agent-accelerator)

2. Accept the default values on the create Codespaces page.
3. Open a terminal window if it is not already open.
4. Follow the instructions in the helper script to populate deployment variables.
5. Continue with the [deploying steps](#deploying).

</details>

<details>
  <summary><b>Deploy in VS Code Dev Containers </b></summary>

 ### VS Code Dev Containers

You can run this solution in VS Code Dev Containers, which will open the project in your local VS Code using the [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers):

1. Start Docker Desktop (install it, if not already installed)
2. Open the project:

    [![Open in Dev Containers](https://img.shields.io/static/v1?style=for-the-badge&label=Dev%20Containers&message=Open&color=blue&logo=visualstudiocode)](https://vscode.dev/redirect?url=vscode://ms-vscode-remote.remote-containers/cloneInVolume?url=https://vscode.dev/redirect?url=vscode://ms-vscode-remote.remote-containers/cloneInVolume?url=https://github.com/Azure-Samples/call-center-voice-agent-accelerator)


3. In the VS Code window that opens, once the project files show up (this may take several minutes), open a terminal window.
4. Follow the instructions in the helper script to populate deployment variables.
5. Continue with the [deploying steps](#deploying).

</details>

<details>
  <summary><b>Deploy in your local environment</b></summary>

 ### Local Environment

If you're not using one of the above options for opening the project, then you'll need to:

1. Make sure the following tools are installed:

    * `bash`
    * [Azure Developer CLI (azd)](https://aka.ms/install-azd)

2. Download the project code:

    ```shell
    azd init -t Azure-Samples/call-center-voice-agent-accelerator/
    ```
    **Note:** the above command should be run in a new folder of your choosing. You do not need to run `git clone` to download the project source code. `azd init` handles this for you.

3. Open the project folder in your terminal or editor.
4. Continue with the [deploying steps](#deploying).

</details>
 
### Deploying

Once you've opened the project in [Codespaces](#github-codespaces) or in [Dev Containers](#vs-code-dev-containers) or [locally](#local-environment), you can deploy it to Azure following the following steps. 

To change the `azd` parameters from the default values, follow the steps [here](./docs/customizing_azd_parameters.md). 

1. Login to Azure:

    ```shell
    azd auth login
    ```

2. Provision and deploy all the resources:

    ```shell
    azd up
    ```
    It will prompt you to provide an `azd` environment name (like "voice-agent-dev"), select a subscription from your Azure account, and select a [Voice Live region](https://learn.microsoft.com/azure/ai-services/speech-service/regions?tabs=voice-live).

    The setup wizard will then guide you through:
    - **Model selection** — choose from 12 fully managed models across Pro, Basic, and Lite tiers (or bring your own)
    - **Telephony provider selection** — choose ACS (default), Twilio, Infobip, or Genesys
    - **Credential entry** — securely prompts for tokens/keys only if you picked Twilio, Infobip, or Genesys

    After provisioning completes, you'll see a deployment summary with your webhook endpoint(s) and next steps.

3. When `azd` has finished deploying, open the **Application URL** shown in the output to test in your browser. 🎉

4. When you've made any changes to the app code, you can just run:

    ```shell
    azd deploy
    ```

5. To switch models after deployment (no redeploy needed):

    ```shell
    az containerapp update -n <app-name> -g <resource-group> --set-env-vars "AZURE_VOICE_LIVE_MODEL=gpt-4.1-mini"
    ```

    The model is a runtime-only setting — changing it does not require `azd up` or any infrastructure changes. Update `azd env` too to keep future deploys consistent:
    ```shell
    azd env set AZURE_VOICE_LIVE_MODEL gpt-4.1-mini
    ```

6. To view live logs:

    ```shell
    azd monitor --logs
    ```

7. When done, clean up all resources:

    ```shell
    azd down
    ```



>[!NOTE]
>- All [supported models](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live#supported-models-and-regions) are fully managed — no deployment or capacity planning needed.
>- Pricing is tiered (Pro, Basic, Lite) based on the model you choose. Default is `gpt-4o-mini` (Basic tier).
>- **Not all models are available in every region.** The setup wizard validates your selection and will block incompatible model/region combinations. Models available in all regions include: `gpt-4.1`, `gpt-4.1-mini`, `gpt-4.1-nano`, `gpt-5`, `gpt-5-chat`, `gpt-5-mini`, `gpt-5-nano`.
>- See [Voice Live supported regions and models](https://learn.microsoft.com/azure/ai-services/speech-service/regions?tabs=voice-live) for the full compatibility matrix.
>- Post-Deployment: Webhook configuration is handled automatically by the post-deploy script. For ACS telephony, you'll still need to acquire a PSTN phone number (see [Testing the Agent](#testing-the-agent) below).



## Testing the Agent

After deployment, you can verify that your Voice Agent is running correctly using the **Web Client** (quick browser test) or a **telephony client** for a real-world call center scenario.

### 🌐 Web Client (Test Mode)

Use this browser-based client to confirm your Container App is up and responding.

1. Go to the [Azure Portal](https://portal.azure.com) and navigate to the **Resource Group** created by your deployment.
2. Find and open the **Container App** resource.
3. On the **Overview** page, copy the **Application URL**.
4. Open the URL in your browser — a demo webpage should load.
5. Click **Start Talking to Agent** to begin a voice session using your browser’s microphone and speaker.
6. Click **Stop Conversation** to end the session.

> ⚠️ This web client is intended for testing purposes only. Use the ACS client below for production-like call flow testing.



### 📞 Telephony with ACS Client (Call Center Scenario)

This simulates a real inbound phone call to your voice agent using **Azure Communication Services (ACS)**.

#### 1. Webhook (Automatic)

The `IncomingCall` Event Grid subscription is **created automatically** by the post-deploy script during `azd up`. No manual portal configuration is needed.

<details>
<summary>Manual setup (if needed)</summary>

1. In the same resource group, find and open the **Communication Services** resource.
2. In the left-hand menu, click **Events**.
3. Click **+ Event Subscription** and fill in the following:
   - **Event Type**: `IncomingCall`
   - **Endpoint Type**: `Web Hook`
   - **Endpoint Address**:
     ```
     https://<your-container-app-url>/acs/incomingcall
     ```

📸 Refer to the screenshot below for guidance:

![Event Subscription screenshot](./docs/images/acs_eventsubscription_v0.0.1.png)

</details>

#### 2. Get a Phone Number

If you haven't already, obtain a phone number for your ACS resource:

👉 [How to get a phone number (Microsoft Docs)](https://learn.microsoft.com/azure/communication-services/quickstarts/telephony/get-phone-number?tabs=windows&pivots=platform-azp-new)


#### 3. Call the Agent

Once the phone number is active:

- Dial the ACS number.
- Your call will connect to the real-time voice agent powered by Azure Voice Live.

### 📞 Telephony with Twilio Client (Call Center Scenario)

Inbound calls are handled via [Twilio Media Streams](https://www.twilio.com/docs/voice/media-streams) — the server validates the request, connects the caller's audio to the AI agent via a real-time WebSocket, and bridges it to Azure Voice Live.

#### 1. Prerequisites

- A [Twilio account](https://www.twilio.com/try-twilio)
- A phone number purchased in the [Twilio Console](https://www.twilio.com/console)

> During `azd up`, the setup wizard prompts for Twilio credentials and stores the token securely in Azure Key Vault.

| Variable | Description | Where to find it |
|----------|-------------|------------------|
| `TWILIO_ACCOUNT_SID` | Your Twilio Account SID | [Twilio Console](https://www.twilio.com/console) → Account Info |
| `TWILIO_AUTH_TOKEN` | Your Twilio Auth Token | [Twilio Console](https://www.twilio.com/console) → Account Info |

#### 2. Webhook (Automatic)

The Twilio webhook is **configured automatically** by the post-deploy script during `azd up`. It sets your phone number's voice URL to `https://<your-container-app-url>/voice`.

<details>
<summary>Manual setup (if needed)</summary>

1. In the [Twilio Console](https://console.twilio.com), go to your phone number's configuration.
2. Under **PhoneNumber → A Call Comes In**, set:
   - **Webhook URL:** `https://<your-container-app-url>/voice`
   - **HTTP Method:** `POST`
3. Save changes.

</details>

#### 3. Call the Agent

Dial your Twilio phone number. The call connects to the real-time voice agent powered by Azure Voice Live.

**How it works:**
1. Twilio sends a request to `/voice` — the server validates it and returns TwiML to start a media stream
2. Twilio opens a WebSocket to `/twilio/ws` — the server verifies the embedded token, then bridges audio to Azure Voice Live
3. The AI agent hears the caller, generates a response, and audio is streamed back through the same connection

### 📞 Telephony with Infobip Client (Call Center Scenario)

Inbound calls are handled via the [Infobip Calls API](https://www.infobip.com/docs/api/channels/voice/calls) — the server answers the call, then bridges the caller's audio to Azure Voice Live via a WebSocket connection.

#### 1. Prerequisites

- An [Infobip account](https://www.infobip.com/signup) with Voice capabilities enabled
- A phone number purchased in the [Infobip Portal](https://portal.infobip.com)

> During `azd up`, the setup wizard prompts for your Infobip API key and base URL, and stores the key securely in Azure Key Vault.

| Variable | Description | Where to find it |
|----------|-------------|------------------|
| `INFOBIP_API_KEY` | Your Infobip API key | [Infobip Portal](https://portal.infobip.com) → Homepage → API Key |
| `INFOBIP_API_BASE_URL` | Your account's API base URL (e.g. `https://xxxxx.api.infobip.com`) | [Infobip Portal](https://portal.infobip.com) → Homepage → Base URL |

#### 2. Webhook (Automatic)

All Infobip configuration is **set up automatically** by the post-deploy script during `azd up`:
- Notification profile with webhook URL
- Media stream configuration with WebSocket URL
- Calls configuration
- Event subscription for call lifecycle events

<details>
<summary>Manual setup (if needed)</summary>

1. In the [Infobip Portal](https://portal.infobip.com), go to **Channels and Numbers → VOICE AND WEBRTC**.
2. Under **Notification Profile**, create or update a profile with:
   - **Notify URL:** `https://<your-container-app-url>/infobip/incoming`
3. Under **Calls API → Media streaming**, create a new configuration with:
   - **URL:** `wss://<your-container-app-url>/infobip/ws`
   - **Audio format:** `audio/l16;rate=24000` (PCM 16-bit, 24kHz)
4. Under **Calls API → Calls Configuration**, create a configuration linked to your notification profile and media stream config.
5. Assign your Infobip phone number to this Calls Configuration.
6. Under **Event Subscription** (via API: `POST /subscriptions/1/subscription/VOICE_VIDEO`), create a subscription with events:
   - `CALL_RECEIVED`, `CALL_ESTABLISHED`, `CALL_FINISHED`, `CALL_FAILED`, `CALL_STARTED`, `CALL_DISCONNECTED`
   - `MEDIA_STREAM_STARTED`, `MEDIA_STREAM_FAILED`, `MEDIA_STREAM_FINISHED`
   - `DIALOG_CREATED`, `DIALOG_ESTABLISHED`, `DIALOG_FAILED`, `DIALOG_FINISHED`
   - `DTMF_CAPTURED`, `CALL_RINGING`, `CALL_PRE_ESTABLISHED`

</details>

#### 3. Call the Agent

Dial your Infobip phone number. The call connects to the real-time voice agent powered by Azure Voice Live.

**How it works:**
1. Infobip sends a `CALL_RECEIVED` webhook to `/infobip/incoming` — the server answers the call
2. Once established, the server creates a Dialog that bridges the caller to the WebSocket endpoint
3. Infobip connects to `/infobip/ws` — audio flows bidirectionally between the caller and Azure Voice Live

### 🎧 Genesys Cloud AudioHook (Audio Connector)

[Genesys AudioHook](https://developer.genesys.cloud/devapps/audiohook) (Audio Connector) streams real-time call audio from Genesys Cloud to your deployed Container App via WebSocket. Unlike the other telephony options, Genesys does not route phone calls through this template — it forwards audio from calls already handled within Genesys Cloud to your AudioHook endpoint for AI processing.

```
Caller → PSTN → Genesys Cloud → AudioHook WebSocket → Container App → Voice Live AI
```

#### 1. Prerequisites

- A [Genesys Cloud](https://www.genesys.com/genesys-cloud) organization with the Audio Connector feature enabled

> During `azd up`, the setup wizard prompts for a Genesys API key and stores it securely in Azure Key Vault.

| Variable | Description | Where to find it |
|----------|-------------|------------------|
| `GENESYS_API_KEY` | A shared secret for authenticating AudioHook connections | You define this value — use the same key in both your deployment and Genesys Cloud integration settings |

#### 2. Test with the Browser Simulator (No Genesys Cloud Required)

After deployment, open the simulator page to test without a Genesys Cloud account:

```
https://<your-container-app-url>/genesys
```

The simulator mimics a Genesys AudioHook client in the browser — it sends your microphone audio as PCMU 8kHz and plays back the AI response. Enter the same API key you configured during setup.

#### 3. Connect to Genesys Cloud (Production)

1. Add an AudioHook (Audio Connector) integration in your Genesys Cloud Admin console
2. Set the **Connection URI** to `wss://<your-container-app-url>/audiohook/ws` and the **API Key** to the same value you configured in `GENESYS_API_KEY`
3. Assign the integration to a call flow or queue so that matching calls stream audio to your endpoint

For protocol details, see the [Genesys AudioHook developer documentation](https://developer.genesys.cloud/devapps/audiohook).

**How it works:**
1. Genesys Cloud opens a WebSocket to `/audiohook/ws` and authenticates with the API key
2. The caller's audio streams to the server, which bridges it to Azure Voice Live
3. The AI response audio is streamed back to Genesys Cloud for the caller to hear

## Local Development

Once the environment has been deployed with `azd up` you can also run the application locally.

Please follow the instructions in [the server README](./server/README.md).

<br/>

## Use Voice Live with Foundry Agents

The Voice Live API supports connecting to an existing **Azure AI Foundry Agent**, allowing you to leverage pre-built capabilities, knowledge bases, and orchestration features alongside real-time voice interactions.

In the `session.update` configuration, you can set different properties such as the model, voice settings, turn detection, and agent connection. For detailed configuration options and step-by-step instructions, refer to the official documentation:

👉 [Get started with Voice Live and Azure AI Foundry Agent Service](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live-agents-quickstart?tabs=windows%2Ckeyless&pivots=ai-foundry-portal)

After updating your configuration, deploy the changes to your Container App:

```bash
azd deploy
```

<br/>

## Optional Features

### 🎧 Ambient Scenes

Add realistic background audio to your voice agent to simulate real-world call center environments. This feature works for both web browser and phone (ACS) clients.

**Available Presets:**

| Preset | Description |
|--------|-------------|
| `none` | Disabled (default) - clean audio with no background |
| `office` | Quiet office ambient (keyboard typing, soft murmurs) |
| `call_center` | Busy call center background (phones, conversations) |
| *custom* | Add your own audio files (see below) |

**How to Enable:**

1. Set the `AMBIENT_PRESET` environment variable in your `.env` file:
   ```
   AMBIENT_PRESET=call_center
   ```

2. For Azure deployment, set it before running `azd up`:
   ```bash
   azd env set AMBIENT_PRESET call_center
   azd up
   ```

**Adjusting Volume:**

The ambient volume is controlled by `_ambient_gain` in `server/app/handler/ambient_mixer.py`:

```python
self._ambient_gain = 0.08  # Default: subtle background
```

| Value | Effect |
|-------|--------|
| `0.05` | Very quiet (barely audible) |
| `0.08` | Subtle (default) |
| `0.12` | Moderate |
| `0.20` | Noticeable |

**Using Custom Audio Files:**

You can add your own ambient audio files:

1. Prepare your audio file with these requirements:
   - **Format:** WAV (uncompressed PCM)
   - **Sample Rate:** 24000 Hz
   - **Bit Depth:** 16-bit signed
   - **Channels:** Mono
   - **Duration:** 30-60 seconds (will loop seamlessly)

2. Place the file in `server/app/audio/`

3. Register the preset in `server/app/handler/ambient_mixer.py`:
   ```python
   PRESETS = {
       "none": {"file": None},
       "office": {"file": "office.wav"},
       "call_center": {"file": "callcenter.wav"},
       "my_custom": {"file": "my_audio.wav"},  # Add your preset
   }
   ```

4. Set `AMBIENT_PRESET=my_custom` in your `.env` file

<br/>

## Troubleshooting

See the [Troubleshooting Guide](./docs/troubleshooting.md) for common deployment issues and solutions, including:
- Docker Hub rate limits during remote builds
- RequestConflict errors from concurrent deployments
- Soft-deleted resource recovery
- Required RBAC permissions

<br/>

## Debugging Calls

Every log line includes a `cid` — a unique ID generated when a call arrives. Filter by `cid` to see the full lifecycle of one call:

```
2026-06-09 12:00:01 INFO [app.server] [cid=a1b2c3d4e5f6] Incoming Twilio Media Stream WebSocket connection
2026-06-09 12:00:01 INFO [app.twilio] [cid=a1b2c3d4e5f6] Stream started: sid=MZ123, call=CA456
2026-06-09 12:00:02 INFO [app.voicelive] [cid=a1b2c3d4e5f6] Voice Live session connected
2026-06-09 12:00:30 INFO [app.server] [cid=a1b2c3d4e5f6] Call ended normally
```

**Local:** logs print to the terminal where you run the server.

**Deployed:** Azure Portal → Container Apps → your app → Log stream (live), or query `ContainerAppConsoleLogs_CL` in Log Analytics for historical search.

Provider-native IDs (Twilio call SID, ACS call connection ID, Genesys conversation ID, Infobip call ID) appear in the log messages. Use them to cross-reference with your provider's dashboard.

<br/>

## Production Readiness

Before using this accelerator for production traffic, review the [Production Readiness Guide](./docs/production-readiness.md). It covers design changes to consider for scaling, shared state, WebSocket lifecycle, provider-specific reliability, observability, security, privacy, and multi-replica deployments.

<br/>

## Resources
- [📖 Docs: Voice live overview](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live)
- [📖 Blog: Upgrade your voice agent with Azure AI Voice Live API](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/upgrade-your-voice-agent-with-azure-ai-voice-live-api/4458247)
- [📖 Docs: Azure Speech](https://learn.microsoft.com/azure/ai-services/speech-service/)
- [📖 Docs: Azure Communication Services (Call Automation)](https://learn.microsoft.com/azure/communication-services/concepts/call-automation/call-automation)

<br/>  


## Security Considerations

ACS currently does not support Managed Identity. The ACS connection string is stored securely in Key Vault and injected into the container app via its secret URL.


## Additional Disclaimers
To the extent that the Software includes components or code used in or derived from Microsoft products or services, including without limitation Microsoft Azure Services (collectively, “Microsoft Products and Services”), you must also comply with the Product Terms applicable to such Microsoft Products and Services. You acknowledge and agree that the license governing the Software does not grant you a license or other right to use Microsoft Products and Services. Nothing in the license or this ReadMe file will serve to supersede, amend, terminate or modify any terms in the Product Terms for any Microsoft Products and Services. 

You must also comply with all domestic and international export laws and regulations that apply to the Software, which include restrictions on destinations, end users, and end use. For further information on export restrictions, visit https://aka.ms/exporting. 

You acknowledge that the Software and Microsoft Products and Services (1) are not designed, intended or made available as a medical device(s), and (2) are not designed or intended to be a substitute for professional medical advice, diagnosis, treatment, or judgment and should not be used to replace or as a substitute for professional medical advice, diagnosis, treatment, or judgment. Customer is solely responsible for displaying and/or obtaining appropriate consents, warnings, disclaimers, and acknowledgements to end users of Customer’s implementation of the Online Services. 

You acknowledge the Software is not subject to SOC 1 and SOC 2 compliance audits. No Microsoft technology, nor any of its component technologies, including the Software, is intended or made available as a substitute for the professional advice, opinion, or judgement of a certified financial services professional. Do not use the Software to replace, substitute, or provide professional financial advice or judgment.  

BY ACCESSING OR USING THE SOFTWARE, YOU ACKNOWLEDGE THAT THE SOFTWARE IS NOT DESIGNED OR INTENDED TO SUPPORT ANY USE IN WHICH A SERVICE INTERRUPTION, DEFECT, ERROR, OR OTHER FAILURE OF THE SOFTWARE COULD RESULT IN THE DEATH OR SERIOUS BODILY INJURY OF ANY PERSON OR IN PHYSICAL OR ENVIRONMENTAL DAMAGE (COLLECTIVELY, “HIGH-RISK USE”), AND THAT YOU WILL ENSURE THAT, IN THE EVENT OF ANY INTERRUPTION, DEFECT, ERROR, OR OTHER FAILURE OF THE SOFTWARE, THE SAFETY OF PEOPLE, PROPERTY, AND THE ENVIRONMENT ARE NOT REDUCED BELOW A LEVEL THAT IS REASONABLY, APPROPRIATE, AND LEGAL, WHETHER IN GENERAL OR IN A SPECIFIC INDUSTRY. BY ACCESSING THE SOFTWARE, YOU FURTHER ACKNOWLEDGE THAT YOUR HIGH-RISK USE OF THE SOFTWARE IS AT YOUR OWN RISK.  

##  Trademarks: 
This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft’s Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos are subject to those third-party’s policies.

## Data Collection:
The software may collect information about you and your use of the software and send it to Microsoft. Microsoft may use this information to provide services and improve our products and services. You may turn off the telemetry as described in the repository. There are also some features in the software that may enable you and Microsoft to collect data from users of your applications. If you use these features, you must comply with applicable law, including providing appropriate notices to users of your applications together with a copy of Microsoft’s privacy statement. Our privacy statement is located at [here](https://go.microsoft.com/fwlink/?LinkID=824704). You can learn more about data collection and use in the help documentation and our privacy statement. Your use of the software operates as your consent to these practices.

**Note**: 
- No telemetry or data collection is directly added in this accelerator project. Please review individual telemetry information from the included Azure services regarding their APIs.
