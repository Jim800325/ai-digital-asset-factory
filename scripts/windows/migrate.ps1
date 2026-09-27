$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Resolve-Path "$PSScriptRoot\..\..")
docker compose run --rm migrate
