$ErrorActionPreference = "Stop"
$r = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/v1/runs"
$r | ConvertTo-Json
