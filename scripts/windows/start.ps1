$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..\..")
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Created .env from .env.example" }
docker version | Out-Null
docker compose up -d --build
Write-Host ""
docker compose ps
Write-Host ""
Write-Host "Waiting for API..."
for ($i=0; $i -lt 30; $i++) {
  try {
    $r = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 2
    if ($r.status -eq "ok") { Write-Host "AI Digital Asset Factory is ready: http://localhost:8000/docs"; exit 0 }
  } catch {}
  Start-Sleep -Seconds 2
}
Write-Error "API did not become healthy. Run .\scripts\windows\logs.ps1"
