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

---

## Headless Raspberry Pi deployment

The gateway is the always-on hub. On a headless Pi, run it as a systemd service so it starts on boot and restarts on failure.

### 1. One-time setup on the Pi

```bash
git clone <repo-url> ~/LocalLLM
cd ~/LocalLLM
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config/backends.local.yaml.example config/backends.local.yaml   # point at your LAN nodes
```

Give the Pi a **fixed IP / hostname** on your router (DHCP reservation) so the address your devices use never changes.

### 2. Install the service

```bash
sudo cp deployment/gateway-node/gateway.service /etc/systemd/system/
sudo nano /etc/systemd/system/gateway.service     # set User / WorkingDirectory / paths
sudo systemctl daemon-reload
sudo systemctl enable --now gateway
systemctl status gateway        # confirm running
journalctl -u gateway -f        # follow logs
```

The gateway binds `0.0.0.0:8080` (override with `GATEWAY_HOST` / `GATEWAY_PORT` in the unit file). Reach the chat from any device on the LAN at `http://<pi-host>:8080/chat/`.

---

## HTTPS (required for phone voice)

Browsers only allow microphone/voice input in a **secure context** (HTTPS or `localhost`). Over plain `http://<host>:8080` typing, history, file upload and memory all work — but **voice does not**. Enable HTTPS to fix that.

### Generate a certificate

```bash
./scripts/make_cert.sh                      # auto-detects hostname + LAN IP
# or pin extra names/IPs:
./scripts/make_cert.sh my-gateway.local 192.168.7.50
```

This writes `certs/server.crt` and `certs/server.key` (gitignored — never commit keys).

### Serve over HTTPS

- **Via `manage.sh`:** it auto-detects `certs/server.{crt,key}` and switches to HTTPS on the next `./manage.sh restart`.
- **Via systemd:** uncomment the two `GATEWAY_SSL_*` lines in `gateway.service`, then `sudo systemctl daemon-reload && sudo systemctl restart gateway`.

Then browse to `https://<pi-host>:8080/chat/`.

### Trusting the certificate on devices

A self-signed cert shows a one-time **"not private"** warning; tap through it (Advanced → proceed) and the origin becomes secure. iOS can be fussy about voice on self-signed certs — for a clean, warning-free experience that reliably enables mobile voice, use [`mkcert`](https://github.com/FiloSottile/mkcert) to create a locally-trusted CA and install that CA on each phone (iOS: install the profile, then Settings → General → About → Certificate Trust Settings → enable full trust).
