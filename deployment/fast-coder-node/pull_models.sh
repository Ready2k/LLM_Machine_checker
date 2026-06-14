#!/usr/bin/env bash
set -euo pipefail

echo "Pulling models..."
ollama pull qwen2.5-coder:7b
ollama pull llama3.1:8b

echo "Installed models:"
ollama list
