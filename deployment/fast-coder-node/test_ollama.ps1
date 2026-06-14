Write-Host "Testing local Ollama tags API..."
Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" | ConvertTo-Json -Depth 5
Write-Host "If models are listed above, your installation is working." -ForegroundColor Green
