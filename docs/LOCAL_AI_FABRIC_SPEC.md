# Local AI Fabric Specification

Status: Draft v0.1  
Owner: James Cregeen  
Repository: `Ready2k/LLM_Machine_checker`  
Created: 2026-06-14

## 1. Purpose

This project will create a local and hybrid LLM execution fabric that lets James route coding, architecture, research, summarisation, and general LLM requests across local machines before falling back to paid hosted models.

The goal is to reduce day-to-day spend on external coding assistants and hosted model tools such as Claude Code or Google-hosted coding workflows, while retaining ChatGPT as the primary paid assistant and keeping sensitive local work local wherever practical.

The project should not try to build a true distributed inference cluster where one model is split across random home devices. That path is fascinating, painful, and mostly a way to turn old hardware into slow warm furniture. The preferred design is federated routing: each machine runs one or more model runtimes independently, and a central gateway selects the best available backend per task.

## 2. Goals

1. Provide a single OpenAI-compatible API endpoint for local and hybrid LLM use.
2. Route requests to the best available backend based on task type, privacy level, model capability, latency, and hardware health.
3. Use existing hardware first:
   - MacBook Pro M4 Pro 24GB as quality local node.
   - RTX 2070 Windows PC as fast local CUDA node.
   - Old MacBook as utility, GUI, gateway, or light inference node after benchmarking.
   - Raspberry Pis as monitoring, DNS, Tailscale, wake-on-LAN, or lightweight infra nodes.
4. Support coding workflows through OpenAI-compatible clients, IDE tools, and command-line tools.
5. Support a GUI for chat, model selection, benchmarking, routing visibility, and system health.
6. Include security controls, auditability, privacy tagging, tests, and evals from the start.
7. Support controlled model upgrades and benchmark-driven model replacement.

## 3. Non-goals

1. Do not build a Kubernetes cluster unless a later decision proves it is genuinely needed.
2. Do not attempt true multi-node tensor or pipeline parallelism across mismatched home machines.
3. Do not expose local model endpoints directly to the public internet.
4. Do not assume cloud fallback is allowed for private or sensitive prompts.
5. Do not depend on one runtime only. Ollama, LM Studio, llama.cpp, and hosted APIs should be swappable behind the gateway.
6. Do not rebuild a full chat GUI if Open WebUI or a similar mature project already covers the need.

## 4. Current hardware inventory

| System | Current known profile | Measured/expected role | Notes |
|---|---|---|---|
| MacBook Pro | Apple M4 Pro, 24GB unified memory, macOS Darwin 25.5.0, Docker installed | Quality local LLM workstation | Measured around 13.1 tok/s on Qwen Coder 14B Q4_K_M equivalent benchmark. Best for 7B-14B quantized, stretch 20B-27B Q4. |
| Windows PC | RTX 2070 8GB, CUDA available via PyTorch 2.5.1+cu121 | Fast CUDA node | Measured around 33.8 tok/s on Qwen Coder 7B Q4_K_M equivalent benchmark. Best for 7B/8B quantized local workloads. |
| Old MacBook | Pending benchmark | Utility node or light inference node | Candidate for Open WebUI, gateway, RAG ingestion, light local models, or background workers. |
| Raspberry Pi devices | Pending inventory | Infra nodes | Candidate for monitoring, DNS, Tailscale subnet routing, wake-on-LAN, health probes, dashboards. Not recommended for serious LLM inference. |
| Cloud providers | Optional | Premium fallback | Used only when policy allows. ChatGPT remains retained separately as the primary paid assistant. |

## 5. Target architecture

```text
                 Users / Tools / IDEs / Apps
                           |
                           v
                OpenAI-compatible endpoint
                  http://ai.local/v1
                           |
                           v
                    AI Gateway / Router
                           |
       +-------------------+-------------------+-------------------+
       |                   |                   |                   |
       v                   v                   v                   v
 MacBook M4 Pro       RTX 2070 PC          Old MacBook          Cloud APIs
 Quality node         Fast CUDA node       GUI/RAG/utility      Explicit fallback
 LM Studio/Ollama     Ollama/LM Studio     Open WebUI/Qdrant    GPT/Claude/Gemini
```

The core idea is that clients do not need to know where models run. They call the gateway. The gateway performs policy checks, chooses a backend, forwards the request, streams the response, and records metrics.

## 6. Primary use cases

### 6.1 Local coding assistant

As James, I want to point coding tools at a local OpenAI-compatible endpoint so I can use local code models for routine coding, refactoring, explanation, test generation, and small architecture tasks without paying for a hosted coding model every time.

Candidate routing:

- Small coding tasks: RTX 2070 PC using Qwen Coder 7B Q4.
- Larger coding explanation: MacBook M4 Pro using Qwen Coder 14B Q4.
- High-complexity reasoning: cloud fallback only if privacy policy allows.

### 6.2 General local chat

As James, I want a GUI where I can ask general LLM questions and choose local-fast, local-quality, or cloud-best without remembering hostnames, ports, or model names.

Candidate GUI: Open WebUI first. Custom GUI later only if Open WebUI cannot expose the required routing, policy, and benchmark views.

### 6.3 Private document analysis

As James, I want to summarise or interrogate private documents locally without sending them to external providers.

Candidate routing:

- Embeddings: local embedding model on MacBook or PC.
- Vector DB: Qdrant or Chroma on old MacBook or PC.
- Generation: MacBook quality node or PC fast node depending on task.
- Cloud fallback: disabled by default for private document mode.

### 6.4 Architecture and demo building

As James, I want to use local models for architecture drafts, Amazon Connect demo code, prompt iterations, and technical notes, with optional hosted fallback only when quality requires it.

## 7. System responsibilities

### 7.1 MacBook Pro M4 Pro node

Recommended role: `local_quality`.

Install:

1. LM Studio.
2. Ollama, optional but useful for runtime consistency.
3. Docker Desktop already present.
4. Project repo checkout.
5. Python 3.12 or 3.13 for tooling and eval scripts.
6. Node.js if running GUI or gateway locally.

Candidate models:

- `qwen2.5-coder:14b` or equivalent GGUF quant via LM Studio.
- Qwen Coder 7B for faster fallback.
- Llama 3.1/3.2 8B class models for general chat.
- Gemma-class 12B or 27B Q4 only if benchmarks prove usable.

Responsibilities:

- Higher-quality local inference.
- Private architecture/document work.
- Benchmark runner.
- Development environment for gateway code.

### 7.2 RTX 2070 Windows PC node

Recommended role: `local_fast`.

Install:

1. NVIDIA drivers.
2. Ollama for Windows.
3. LM Studio optional.
4. Python 3.12 virtual environment for profiler and CUDA benchmark.
5. PyTorch CUDA wheel only for synthetic benchmark and hardware validation, not as primary inference runtime.
6. Git for pulling repo.
7. Optional: Tailscale for secure LAN/VPN access.

Validated state:

```text
PyTorch: 2.5.1+cu121
CUDA available: True
GPU: NVIDIA GeForce RTX 2070
Measured benchmark: 33.8 tok/s on Qwen Coder 7B Q4_K_M equivalent
```

Candidate models:

- Qwen Coder 7B Q4/Q5.
- Llama 3.1 8B Q4.
- Mistral/Nemo 7B/12B class models if memory allows.
- Embedding model if GPU headroom exists.

Responsibilities:

- Fast local coding and chat inference.
- CUDA experiments.
- Benchmark baseline for local-fast route.
- Optional always-on inference node if power/noise is acceptable.

### 7.3 Old MacBook node

Recommended role: pending benchmark.

Possible installs:

1. Open WebUI.
2. Qdrant or Chroma.
3. Ollama if Apple Silicon or sufficiently strong Intel hardware.
4. Tailscale.
5. Node exporter or lightweight metrics agent.

Candidate responsibilities:

- GUI host.
- RAG ingestion and indexing.
- Gateway host if stable and always available.
- Light inference node only if benchmark proves acceptable.

Decision rule:

- If 7B model >= 8 tok/s: allow as `local_utility_inference`.
- If below 8 tok/s: use for GUI, RAG, gateway, and monitoring only.

### 7.4 Raspberry Pi nodes

Recommended role: infrastructure only.

Install candidates:

1. Tailscale.
2. Prometheus node exporter.
3. Lightweight DNS, if useful.
4. Wake-on-LAN controller for PC.
5. Health-check scheduler.

Responsibilities:

- Monitor machine availability.
- Wake sleeping machines.
- Provide stable internal names such as `ai.local`, `pc-ai.local`, `mac-ai.local`.
- Run dashboards or status probes if performance allows.

Do not use Raspberry Pis for serious LLM generation unless the goal is educational suffering.

## 8. Runtime strategy

### 8.1 Preferred runtimes

| Runtime | Best use | Notes |
|---|---|---|
| Ollama | Simple model hosting and API compatibility | Good default for PC and simple node management. |
| LM Studio | Mac-first local model management and testing | Good GUI, model discovery, and OpenAI-compatible local server. |
| llama.cpp | Advanced benchmarking and low-level control | Useful later for `llama-bench` and GGUF testing. |
| Open WebUI | Chat GUI | Preferred initial GUI rather than building one from scratch. |

### 8.2 API compatibility

The gateway must expose at least:

```text
GET  /health
GET  /v1/models
POST /v1/chat/completions
POST /v1/embeddings
```

The `/v1/chat/completions` endpoint should be compatible enough for common OpenAI-compatible clients, IDE tools, and Open WebUI.

Streaming support is required.

## 9. Routing design

### 9.1 Route names

Initial logical routes:

| Route | Purpose | Default backend |
|---|---|---|
| `local-fast` | Fast local coding/chat | RTX 2070 PC |
| `local-quality` | Higher-quality local answers | MacBook M4 Pro |
| `private-docs` | Local-only document Q&A | MacBook or PC, no cloud fallback |
| `cloud-best` | Best available hosted model | Disabled unless configured |
| `auto` | Gateway chooses route | Policy-based |

### 9.2 Routing inputs

The router should consider:

1. Requested model alias.
2. Task type: coding, chat, summarisation, RAG, architecture, eval.
3. Privacy level: public, personal, confidential, restricted.
4. Prompt size and expected output size.
5. Backend health.
6. Measured tokens/sec.
7. Current memory pressure.
8. Queue depth.
9. Cost policy.
10. User override.

### 9.3 Example routing policy

```yaml
routes:
  local-fast:
    primary: pc-rtx2070-ollama
    fallback: macbook-m4pro-lmstudio
    cloud_fallback: false
    privacy_allowed: [public, personal, confidential]

  local-quality:
    primary: macbook-m4pro-lmstudio
    fallback: pc-rtx2070-ollama
    cloud_fallback: false
    privacy_allowed: [public, personal, confidential]

  private-docs:
    primary: macbook-m4pro-lmstudio
    fallback: pc-rtx2070-ollama
    cloud_fallback: false
    privacy_allowed: [personal, confidential, restricted]

  cloud-best:
    primary: chatgpt_or_other_configured_provider
    fallback: none
    cloud_fallback: true
    privacy_allowed: [public]
```

### 9.4 Fallback rules

1. Never fall back from local to cloud unless the request policy explicitly permits it.
2. Never send `restricted` data to hosted providers.
3. Prefer slower local output over accidental cloud leakage.
4. If all allowed backends are unavailable, return a clear error explaining which policy blocked fallback.

## 10. Configuration model

Use versioned YAML or JSON.

Suggested files:

```text
config/
  machines.yaml
  models.yaml
  routes.yaml
  security.yaml
  evals.yaml
```

### 10.1 Machine config

```yaml
machines:
  macbook-m4pro:
    hostname: Jamess-MacBook-Pro-2.local
    role: local_quality
    endpoint: http://Jamess-MacBook-Pro-2.local:1234/v1
    runtime: lmstudio
    measured_tps:
      qwen-coder-14b-q4: 13.1
    enabled: true

  pc-rtx2070:
    hostname: pc-ai.local
    role: local_fast
    endpoint: http://pc-ai.local:11434/v1
    runtime: ollama
    measured_tps:
      qwen-coder-7b-q4: 33.8
    enabled: true
```

### 10.2 Model registry

```yaml
models:
  qwen-coder-7b-q4:
    provider_model: qwen2.5-coder:7b
    family: qwen
    type: coding
    size_class: 7b
    quantization: q4
    preferred_route: local-fast
    min_ram_gb: 12
    min_vram_gb: 6
    eval_status: approved

  qwen-coder-14b-q4:
    provider_model: qwen2.5-coder:14b
    family: qwen
    type: coding
    size_class: 14b
    quantization: q4
    preferred_route: local-quality
    min_ram_gb: 20
    min_vram_gb: null
    eval_status: approved
```

## 11. GUI strategy

### 11.1 Start with existing tools

Use Open WebUI as the first GUI unless it fails a required capability.

Open WebUI should connect to the AI Gateway rather than individual backends.

Benefits:

1. Faster delivery.
2. Existing chat UX.
3. Multi-user support if needed later.
4. Model selection already exists.
5. Avoids building chat plumbing before the routing architecture exists.

### 11.2 Required GUI capabilities

The GUI should allow:

1. Chat with local-fast, local-quality, private-docs, and cloud-best routes.
2. Selection of model alias rather than raw backend host.
3. Visibility of current backend used for each response.
4. Display of latency, tokens/sec, and fallback events.
5. Privacy mode selection.
6. Uploading documents for local-only RAG when implemented.
7. Admin status page for node health and model availability.

### 11.3 Custom GUI later

A custom Next.js GUI may be justified later if Open WebUI cannot provide:

1. Good route transparency.
2. Policy warnings.
3. Benchmark dashboards.
4. Model lifecycle controls.
5. Per-request privacy classification.

## 12. Development architecture

Suggested repository structure:

```text
LLM_Machine_checker/
  README.md
  docs/
    LOCAL_AI_FABRIC_SPEC.md
    SECURITY_MODEL.md
    EVAL_STRATEGY.md
    MODEL_LIFECYCLE.md
  profiler/
    profile_machine.py
    benchmark_ollama.py
    benchmark_lmstudio.py
  gateway/
    app/
      main.py
      routes/
      providers/
      policy/
      telemetry/
    tests/
      unit/
      integration/
  config/
    machines.example.yaml
    models.example.yaml
    routes.example.yaml
    security.example.yaml
  evals/
    datasets/
    prompts/
    runners/
    reports/
  docker/
    docker-compose.openwebui.yaml
    docker-compose.gateway.yaml
  scripts/
    install_pc_windows.ps1
    install_mac.sh
    run_healthcheck.py
    update_models.py
```

## 13. Gateway implementation requirements

### 13.1 Technology choice

Preferred initial implementation:

- Python 3.12.
- FastAPI.
- HTTPX for backend forwarding.
- Pydantic for config validation.
- Rich or Typer for CLI utilities.
- pytest for tests.

Reason: Python is the least painful option for quick API routing, eval scripts, model benchmarking, and system probes. This is a rare moment where the obvious answer is not terrible.

### 13.2 Provider abstraction

Implement provider adapters:

```text
providers/
  base.py
  ollama.py
  lmstudio.py
  openai_compatible.py
  cloud_openai.py
  cloud_anthropic.py   # optional later
  cloud_google.py      # optional later
```

The gateway should not care if the backend is Ollama, LM Studio, OpenAI, or another OpenAI-compatible API.

### 13.3 Required gateway behaviours

1. Validate request.
2. Resolve model alias.
3. Apply privacy policy.
4. Select backend.
5. Forward request.
6. Support streaming.
7. Log metrics.
8. Return backend metadata in response headers where possible.
9. Handle backend failure cleanly.
10. Enforce fallback policy.

Example response headers:

```text
x-ai-route: local-fast
x-ai-backend: pc-rtx2070-ollama
x-ai-model: qwen-coder-7b-q4
x-ai-fallback-used: false
x-ai-policy: local-only
```

## 14. Security requirements

### 14.1 Network security

1. Bind local runtimes to LAN-only interfaces where possible.
2. Do not expose Ollama, LM Studio, Open WebUI, or the gateway directly to the internet.
3. Use Tailscale or equivalent if remote access is required.
4. Use firewall rules to restrict model backends to gateway access only where practical.
5. Prefer `ai.local` DNS or mDNS aliases over hard-coded IPs.

### 14.2 Authentication and access control

Minimum viable security:

1. Gateway requires an API key for non-localhost access.
2. Admin endpoints require a separate admin token.
3. Open WebUI should require login if available on the LAN.
4. Secrets must be loaded from environment variables or local secret files excluded from Git.

Future:

1. OIDC login.
2. Per-user quotas.
3. Per-route authorization.
4. Device certificates if exposing across networks.

### 14.3 Data protection

1. Default privacy mode should be `local-only`.
2. Cloud fallback must be explicit, never automatic for private data.
3. Prompts and completions should not be logged by default.
4. Metrics can log metadata: route, model, latency, token counts, errors.
5. Full prompt logging must be opt-in and clearly marked unsafe for sensitive data.
6. RAG document stores must remain local unless explicitly configured otherwise.

### 14.4 Secret handling

Never commit:

1. API keys.
2. Tailscale auth keys.
3. Cloud provider keys.
4. SSH keys.
5. Private documents.
6. Raw benchmark outputs containing sensitive prompts.

Add `.env`, `.env.local`, `secrets/`, and `data/private/` to `.gitignore`.

## 15. Testing strategy

### 15.1 Unit tests

Required unit test areas:

1. Config parsing and validation.
2. Model alias resolution.
3. Route selection.
4. Privacy policy enforcement.
5. Fallback policy.
6. Provider request mapping.
7. Provider response mapping.
8. Error handling.
9. Header generation.
10. Health score calculation.

Example tests:

```text
test_restricted_prompt_never_routes_to_cloud
test_local_fast_prefers_pc_when_healthy
test_local_quality_falls_back_to_pc_when_mac_down
test_cloud_best_requires_explicit_cloud_policy
test_backend_timeout_returns_structured_error
test_streaming_response_preserves_chunks
```

### 15.2 Integration tests

Use mock OpenAI-compatible backends first.

Required integration tests:

1. Gateway forwards chat completion request to mock backend.
2. Gateway streams response from mock backend.
3. Gateway falls back when primary backend fails.
4. Gateway blocks fallback when policy prohibits it.
5. Gateway lists configured models via `/v1/models`.
6. Gateway handles backend health check failures.

Optional live integration tests:

1. Ollama local endpoint.
2. LM Studio local endpoint.
3. Open WebUI connected to gateway.

Live tests should be opt-in because not every machine will have the same models installed.

### 15.3 End-to-end tests

End-to-end tests should cover:

1. Open WebUI -> Gateway -> Ollama.
2. Open WebUI -> Gateway -> LM Studio.
3. IDE client -> Gateway -> local coding model.
4. RAG question -> local embeddings -> vector DB -> local model.

### 15.4 Security tests

Required:

1. API key missing returns 401.
2. Invalid API key returns 401.
3. Admin endpoint blocked for non-admin token.
4. Cloud fallback blocked for confidential/restricted prompts.
5. Prompt logging disabled by default.
6. `.env` and secret paths excluded from Git.
7. Gateway rejects unknown model aliases unless explicitly configured to pass through.

### 15.5 Performance tests

Required:

1. Benchmark time-to-first-token.
2. Benchmark tokens/sec.
3. Measure request latency through gateway overhead.
4. Measure concurrent request behaviour.
5. Measure memory pressure and queue depth.

Success target for gateway overhead:

```text
Non-streaming gateway overhead: < 100 ms excluding backend inference
Streaming first-chunk overhead: < 250 ms excluding backend inference
```

## 16. Evaluation strategy

The eval system should compare models against James's real use cases, not generic leaderboard vanity scores.

### 16.1 Eval categories

1. Coding assistance.
2. Refactoring.
3. Test generation.
4. Architecture explanation.
5. Amazon Connect / AWS reasoning.
6. Summarisation.
7. Private document Q&A.
8. Safety and refusal behaviour.
9. Hallucination resistance.
10. Tool-use readiness.

### 16.2 Coding evals

Example prompts:

1. Generate FastAPI endpoint with tests.
2. Refactor TypeScript function with strict types.
3. Explain a Next.js build error.
4. Create AWS SDK wrapper with error handling.
5. Write Playwright tests for a simple UI flow.

Scoring:

| Score | Meaning |
|---|---|
| 0 | Fails or unsafe |
| 1 | Mostly wrong |
| 2 | Partially useful |
| 3 | Useful with edits |
| 4 | Good |
| 5 | Excellent |

### 16.3 Architecture evals

Models should be tested on:

1. Creating a small reference architecture.
2. Explaining trade-offs.
3. Identifying security risks.
4. Producing implementation plans.
5. Avoiding confident nonsense when information is missing.

### 16.4 Regression evals

Every model upgrade should run a standard eval pack.

Minimum acceptance:

1. New model must not reduce average score by more than 10% versus current approved model for same route.
2. New model must pass all restricted-data routing tests.
3. New model must meet minimum latency threshold for its route.
4. New model must be manually approved before becoming default.

## 17. Model lifecycle and updates

### 17.1 Model states

Use model states:

```text
discovered -> downloaded -> benchmarked -> evaluated -> approved -> default -> deprecated -> removed
```

### 17.2 Model update process

1. Discover candidate model.
2. Download on target node.
3. Run hardware benchmark.
4. Run task eval suite.
5. Compare against current default.
6. Update `models.yaml` with results.
7. Mark as approved or rejected.
8. Promote to default only after passing evals.
9. Keep rollback path.

### 17.3 Model metadata

Track:

1. Model name.
2. Source.
3. Quantization.
4. Context window.
5. Disk size.
6. Required RAM/VRAM.
7. Runtime compatibility.
8. Measured tokens/sec per machine.
9. Eval scores.
10. Approval status.
11. Date added.
12. Date last evaluated.

### 17.4 Update cadence

Recommended cadence:

1. Weekly check for new models only if actively experimenting.
2. Monthly model review for stable use.
3. Immediate review only for a clearly relevant coding or reasoning model release.

This prevents spending every evening downloading models because the AI internet found a new shiny object. Humanity has enough problems.

## 18. Observability

Metrics to collect:

1. Backend health.
2. Request count.
3. Error count.
4. Latency.
5. Time to first token.
6. Tokens/sec.
7. Prompt token estimate.
8. Completion token estimate.
9. Fallback count.
10. Policy block count.
11. Queue depth.
12. CPU/RAM/GPU utilisation where available.

Initial storage:

- Local JSONL logs for simplicity.
- Optional SQLite for structured history.
- Prometheus later if useful.

Example event:

```json
{
  "timestamp": "2026-06-14T18:00:00Z",
  "route": "local-fast",
  "backend": "pc-rtx2070-ollama",
  "model": "qwen-coder-7b-q4",
  "privacy": "personal",
  "ttft_ms": 420,
  "tokens_per_second": 33.8,
  "fallback_used": false,
  "status": "success"
}
```

## 19. Installation plan by system

### 19.1 Windows PC

Install:

```text
Git
Python 3.12
Ollama for Windows
NVIDIA driver
Optional LM Studio
Optional Tailscale
```

Validate:

```text
nvidia-smi
python -c "import torch; print(torch.cuda.is_available())"
ollama --version
ollama run qwen2.5-coder:7b
```

### 19.2 MacBook Pro M4

Install:

```text
LM Studio
Ollama optional
Python 3.12 or 3.13
Node.js
Docker Desktop already present
Optional Open WebUI if not hosted elsewhere
```

Validate:

```text
system_profiler SPHardwareDataType
python3 profiler/profile_machine.py
LM Studio local server responds on configured port
```

### 19.3 Old MacBook

Install after benchmark decision:

```text
Open WebUI or gateway runtime
Python 3.12 if supported
Docker if supported and stable
Tailscale
Qdrant or Chroma if used for RAG
```

### 19.4 Raspberry Pi

Install:

```text
Tailscale
Node exporter or lightweight health probe
Wake-on-LAN tools
Optional DNS/mDNS helper
```

## 20. Milestones

### Milestone 1: Hardware inventory and benchmark

Deliverables:

1. `profile_machine.py` cleaned and committed.
2. Benchmarks captured for MacBook M4 and RTX 2070 PC.
3. Old MacBook benchmark added.
4. Machine registry generated.

Acceptance:

- Each machine has role recommendation.
- Each machine has measured or explicitly estimated tokens/sec.
- Results are written to `inventory/machines.json`.

### Milestone 2: Runtime setup

Deliverables:

1. Ollama running on PC.
2. LM Studio running on MacBook.
3. At least one model loaded per node.
4. Health checks working.

Acceptance:

- Gateway can call both runtimes manually.
- `/health` returns status per backend.

### Milestone 3: Gateway MVP

Deliverables:

1. FastAPI gateway.
2. `/v1/models` endpoint.
3. `/v1/chat/completions` endpoint.
4. Basic route selection.
5. Streaming support.
6. JSONL metrics.

Acceptance:

- OpenAI-compatible client can call the gateway.
- Route `local-fast` reaches PC.
- Route `local-quality` reaches MacBook.

### Milestone 4: Security and policy

Deliverables:

1. API key auth.
2. Privacy policy enforcement.
3. Cloud fallback blocked unless explicit.
4. Admin token for admin endpoints.
5. Secret-safe config templates.

Acceptance:

- Restricted prompts cannot route to cloud.
- Missing auth is rejected.
- Unit tests prove policy enforcement.

### Milestone 5: GUI

Deliverables:

1. Open WebUI connected to gateway.
2. Model aliases visible.
3. Basic route selection available.
4. Backend used visible via response metadata or logs.

Acceptance:

- James can use a browser to choose local-fast/local-quality/private-docs.
- No direct user interaction with raw backend ports required.

### Milestone 6: Evals and model lifecycle

Deliverables:

1. Eval prompt set.
2. Eval runner.
3. Model score reports.
4. Model approval workflow.
5. Update script.

Acceptance:

- New model can be benchmarked and evaluated before becoming default.
- Regression report created for each model change.

### Milestone 7: RAG/private docs

Deliverables:

1. Local document ingestion.
2. Local embeddings.
3. Vector DB.
4. Private-docs route.
5. GUI access.

Acceptance:

- Private docs can be queried locally.
- Cloud fallback is impossible in private-docs mode.

## 21. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Local models are weaker than cloud coding models | Lower answer quality | Use task routing, evals, and cloud fallback only when allowed. |
| PC sleeps or is unavailable | Failed route | Wake-on-LAN, health checks, fallback to Mac. |
| LAN endpoints exposed insecurely | Data leakage | API keys, firewall rules, Tailscale, local-only binding. |
| Too many models downloaded | Disk bloat and confusion | Model registry and approval workflow. |
| GUI duplicates existing tools | Wasted time | Start with Open WebUI. Build custom only if needed. |
| Benchmark results are misleading | Poor routing decisions | Prefer real inference benchmarks over synthetic FLOPS where possible. |
| Prompt logs leak data | Privacy issue | Disable prompt logging by default. |

## 22. Initial backlog

1. Commit existing `profile_machine.py` into `profiler/`.
2. Create `inventory/machines.json` from current Mac and PC results.
3. Add `config/*.example.yaml` files.
4. Add FastAPI gateway skeleton.
5. Add provider adapters for Ollama and LM Studio.
6. Add `/health` and `/v1/models`.
7. Add non-streaming `/v1/chat/completions`.
8. Add streaming support.
9. Add API key middleware.
10. Add route policy engine.
11. Add unit tests for policy and routing.
12. Add Open WebUI docker compose file.
13. Add eval dataset and runner.
14. Add model update script.
15. Add old MacBook benchmark once available.

## 23. Initial definition of done

The first complete version is done when:

1. James can open a GUI and ask a question using `local-fast` or `local-quality`.
2. James can point an OpenAI-compatible coding client at `http://ai.local/v1`.
3. Requests route to the PC or Mac based on model alias and policy.
4. Private mode never routes to cloud.
5. Basic metrics show backend, model, latency, and tokens/sec.
6. Tests cover routing, policy, provider mapping, and auth.
7. Model updates require benchmark and eval evidence before promotion.

## 24. Design principles

1. Measure first, buy later.
2. Prefer boring working tools over heroic custom builds.
3. Make cloud fallback explicit, not sneaky.
4. Keep the gateway simple enough to debug.
5. Treat model choice as configuration, not code.
6. Treat privacy as a routing constraint, not a disclaimer.
7. Use old hardware where it helps, not where nostalgia demands it.

## 25. Open questions

1. Which machine should host the gateway long term: old MacBook, MacBook M4, PC, or Raspberry Pi?
2. Should Open WebUI run on old MacBook, PC, or Docker on MacBook M4?
3. Which IDE client should be standardised first?
4. What is the first approved coding model family: Qwen Coder, DeepSeek Coder, Code Llama, or another?
5. Should cloud fallback include only ChatGPT/OpenAI-compatible endpoints, or also Anthropic/Gemini if later subscribed?
6. How much telemetry history should be retained?
7. Should RAG be part of MVP or milestone 7 only?
