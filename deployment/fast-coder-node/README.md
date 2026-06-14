# Fast Coder Node Setup (Windows PC)

Run:
- Ollama
- Qwen Coder 7B
- Llama 3.x 8B
- Other fast 7B/8B local models

Do not run:
- Open WebUI
- Gateway
- Private-doc workloads

## Setup Instructions

Use native Windows PowerShell to run the scripts in this directory.

1. **Configure Ollama for LAN access:**
   Run `configure_ollama_windows.ps1` to expose Ollama on `0.0.0.0:11434` and configure the Windows Firewall automatically.

2. **Pull the Models:**
   Run `pull_models.ps1` to automatically pull the recommended fast coding models.

3. **Test:**
   Run `test_ollama.ps1` to verify the models have been downloaded and the API is responding locally.
