$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..\..")
docker compose ps
try { Invoke-RestMethod "http://localhost:8000/health" | ConvertTo-Json } catch { Write-Warning $_ }
