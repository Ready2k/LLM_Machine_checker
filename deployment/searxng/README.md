# SearXNG node — web grounding for the gateway

The gateway's **web grounding** feature (the `web` model alias / 🌐 toggle in
`/chat/`) turns a question into real, fetched web sources so local models cite
instead of hallucinate. Search runs through this self-hosted SearXNG instance so
the **query stays on your LAN** — SearXNG proxies to upstream engines
server-side. (Page fetches still leave the LAN; that's the gateway, gated by
`security.yaml → web_search.allowed_privacy`.)

## Run it

On any always-on node (the Pi/hub is ideal):

```bash
cd deployment/searxng
# set a real secret first:
sed -i '' "s/CHANGE_ME_openssl_rand_hex_32/$(openssl rand -hex 32)/" settings.yml
docker compose up -d
```

Sanity-check JSON output is on (this is the #1 gotcha):

```bash
curl 'http://localhost:8080/search?q=test&format=json' | head -c 200
# Should be JSON, not a 403.
```

## Point the gateway at it

Either edit `config/tools.yaml`:

```yaml
tools:
  web_search:
    enabled: true
    searxng_url: "http://<this-host>:8080"
```

…or set the env var (overrides the config), e.g. in the gateway's systemd unit
or shell:

```bash
export SEARXNG_URL="http://<this-host>:8080"
```

Until the gateway can reach SearXNG, `web` requests degrade gracefully to an
ungrounded answer and flag it via the `x-ai-grounding` response header
(`unavailable` / `no_results` / `error` / `blocked_by_privacy` / `grounded`).
