# Gateway Node Setup

The Gateway is the brain of the Local AI Fabric. It exposes a single OpenAI-compatible `/v1/` endpoint that routes requests to the correct hardware based on the model requested.

## Location

The gateway can run on:
- **MacBook Pro** during development/testing
- **Old MacBook** as the always-on hub

## Gateway Configuration

The most important step is ensuring the gateway knows where the `fast-coder` and `quality` nodes are located. 

Copy the example configuration to the project root's config folder:
```bash
cp config/backends.local.example.yaml ../../config/backends.local.yaml
```

Edit `../../config/backends.local.yaml`.
- Use `127.0.0.1` for backends running on the **same machine** as this gateway.
- Use `.local` hostnames or IPs for backends running on **different machines** on the LAN.

## Start the Gateway

```bash
chmod +x start_gateway.sh
./start_gateway.sh
```

## Stop the Gateway

```bash
chmod +x stop_gateway.sh
./stop_gateway.sh
```
