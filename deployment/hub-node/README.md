# Hub Node Setup (Old MacBook)

This machine acts as the central UI hub for your Local AI Fabric. It does **not** run any heavy LLM inference.

## Prerequisites

Check if Docker is installed:
```bash
docker --version
docker compose version
```

If missing, install [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/) or [OrbStack](https://orbstack.dev/) (recommended for a lightweight utility node).

## Configuration

If the Gateway is running on the **same Old MacBook**, the docker container needs to point to `http://host.docker.internal:8080/v1`.
If the Gateway is running on the **M4 MacBook Pro**, the docker container needs to point to `http://Jamess-MacBook-Pro-2.local:8080/v1`.

1. Copy `.env.example` to `.env` in this folder:
   ```bash
   cp .env.example .env
   ```
2. Edit `.env` to match your Gateway's actual IP or hostname if it is not running locally.

## Start the Hub

```bash
chmod +x start_hub.sh
./start_hub.sh
```

## Stop the Hub

```bash
chmod +x stop_hub.sh
./stop_hub.sh
```

Open WebUI will be available at `http://<old-macbook-ip>:3000`.
