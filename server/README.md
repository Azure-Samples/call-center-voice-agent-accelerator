# Overview
The Twilio real-time voice service is managed using `pyproject.toml` and the [`uv`](https://github.com/astral-sh/uv) package manager for fast Python dependency management.

## 1. Test with Web Client

### Set Up Environment Variables
Based on .env-sample.txt, create and construct your .env file to allow your local app to access your Azure resource.

### Run the App Locally
1. Run the local server:

    ```shell
    uv run server.py
    ```

3. Once the app is running, open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser (or click the printed URL in the terminal).

4. On the page, click **Start** to begin speaking with the agent using your browser’s microphone and speaker.

### Run with Docker (Alternative)

If you prefer Docker or are running in GitHub Codespaces:

1. Build the image:

    ```
    docker build -t twilio-realtime-voice .
    ```

2. Run the image with local environment variables:

    ```
    docker run --env-file .env -p 8000:8000 -it twilio-realtime-voice
    ```
3. Open [http://127.0.0.1:8000](http://127.0.0.1:8000) and click **Start** to interact with the agent.

## 2. Test with Twilio Voice (Phone Call)

You can stream live calls from Twilio into the agent by enabling **Twilio Media Streams** and
pointing the `<Stream>` target to the app's `/twilio/stream` WebSocket endpoint.

### Expose Your Local Server (optional)

For local development, make your Quart server reachable on the public internet using a tunneling
solution such as [ngrok](https://ngrok.com/) or [Azure Dev Tunnels](https://learn.microsoft.com/azure/developer/dev-tunnels/overview).

```
ngrok http https://localhost:8000
```

Take note of the generated `wss://` URL, e.g. `wss://<random>.ngrok.app/twilio/stream`.

### Configure a Twilio Voice Application

1. In the [Twilio Console](https://console.twilio.com/), create (or edit) a **Voice** application.
2. For the call handler, provide TwiML that connects the call to your streaming endpoint. Example:

        ```xml
        <?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Connect>
                <Stream url="wss://<your-domain>/twilio/stream" />
            </Connect>
        </Response>
        ```

        Replace `<your-domain>` with your tunnel or production hostname. Twilio requires TLS, so ensure
        the URL uses `wss://`.

3. Assign the voice application to a Twilio phone number.

### Place a Test Call

1. Start the Quart server (`uv run server.py`) and make sure your tunneling session is active.
2. Call the configured Twilio number. Twilio will open a WebSocket to `/twilio/stream`, streaming
     μ-law audio in real time. The server converts it to PCM, forwards it to Azure Voice Live, and
     sends live transcripts back over the WebSocket as `message` events.
3. Check the server logs (and optionally the Twilio debugger) to confirm audio is flowing.

## Recap

- Use the **web client** for fast local testing.
- Use **Twilio Media Streams** to drive live phone audio into the transcription pipeline.
- Customize the `.env` file, prompts, and runtime behavior to fit your use case.
