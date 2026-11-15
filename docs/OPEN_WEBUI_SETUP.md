# Open WebUI Setup Guide

This guide explains how to connect Open WebUI to the Local AI Fabric gateway.

## Configuration Steps

1. Open WebUI in your browser and go to **Settings > Connections**.
2. Look for the **OpenAI API** section.
3. Click the `+` button to add a new connection.
4. Enter the following details:
   - **Base URL**: `http://<gateway-host-ip>:8080/v1` (or `http://127.0.0.1:8080/v1` if running on the same machine)
   - **API Key**: one of your `GATEWAY_API_KEYS` (required when the gateway has keys configured; sent as `Authorization: Bearer`)
5. Click **Save** or **Verify Connection**.

## Available Models

Once connected, Open WebUI will fetch the available models from the Gateway via the `/v1/models` endpoint. You will see:

- `local-fast`: Best for 7B/8B quick coding models (e.g. Qwen 2.5 Coder).
- `local-quality`: Best for 14B+ reasoning models.
- `private-docs`: Enforces strict privacy constraints (no cloud fallback allowed).
- `cloud-best`: (Only if enabled in your configuration) Routes to paid models like GPT-4.

## Privacy & Security

The Gateway enforces privacy tags natively. By default, all requests from Open WebUI will be treated as `personal` privacy level (the default set in `security.yaml`). This ensures they will try local machines first. 

> [!WARNING]
> By default, `cloud-best` is explicitly disabled in the Gateway. If you attempt to select `cloud-best` in Open WebUI without configuring a cloud fallback locally, the request will fail to ensure your prompts do not leave the local network.
