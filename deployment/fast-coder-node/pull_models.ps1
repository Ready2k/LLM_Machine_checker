$models = @(
  "qwen2.5-coder:7b",
  "llama3.1:8b"
)

foreach ($model in $models) {
  Write-Host "Pulling $model..."
  ollama pull $model
}

Write-Host "Installed models:"
ollama list
