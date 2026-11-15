# Quality Node Setup (MacBook Pro M4)

Run:
- LM Studio Local Server
- Qwen Coder 14B Q4_K_M
- Potential 20B/27B experiments
- Private-doc model backend
- Quality model backend

Optional during dev:
- Gateway
- Dashboard

Do not make it depend on Docker unless needed.

> **Current setup:** the quality node now runs MLX servers behind the `openai_compatible` runtime — mlx.studio (Qwen3.6-27B) on `:8001` (`macbook-m4pro`) and mlx-lm (Gemma 3 12B QAT) on `:8002` (`macbook-m4pro-fast`). See `config/backends.yaml`. LM Studio (below) is still a supported runtime (`runtime: lmstudio`).

## LM Studio Guidance

1. Install LM Studio.
2. Download `Qwen Coder 14B Q4_K_M` or equivalent.
3. Open Developer / Local Server.
4. Start server on port 1234.
5. Bind to `0.0.0.0` only if the gateway runs on another machine (e.g. the Old MacBook).
6. Keep `127.0.0.1` if the gateway runs locally on this M4 MacBook Pro.

### Important Nuance
- `127.0.0.1` is safest if gateway is on the same Mac.
- `0.0.0.0` is needed only if the Old MacBook gateway must reach LM Studio over the LAN.

CORS matters mostly for browser-to-server calls. The gateway calling LM Studio server-side usually does not need CORS, so it is optional.
