$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..\..")
docker compose logs --tail=200 api worker postgres redis
