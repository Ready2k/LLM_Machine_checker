Write-Host "Configuring Ollama to listen on LAN..."

[Environment]::SetEnvironmentVariable(
  "OLLAMA_HOST",
  "0.0.0.0:11434",
  "User"
)

Write-Host "Creating Windows Firewall rule for Ollama TCP 11434..."

New-NetFirewallRule `
  -DisplayName "Ollama LAN 11434" `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort 11434 `
  -Profile Private

Write-Host ""
Write-Host "Now restart Ollama from the system tray or Start Menu."
Write-Host "Then test locally:"
Write-Host "curl http://127.0.0.1:11434/api/tags"
Write-Host ""
Write-Host "Then test from the Mac:"
Write-Host "curl http://pc-ai.local:11434/api/tags"
