# Windows deployment

Recommended: Windows 11 / Windows Server host with Docker Desktop using the WSL2 backend and Linux containers.

## Prerequisites

1. Enable virtualization in BIOS/UEFI.
2. Install WSL2.
3. Install Docker Desktop and enable the WSL2 engine.
4. Use Linux containers.
5. Install Git for Windows.

## Install

Open PowerShell:

```powershell
git clone https://github.com/Jim800325/ai-digital-asset-factory.git
cd ai-digital-asset-factory
.\scripts\windows\start.ps1
```

The script creates `.env` if missing, builds containers, starts PostgreSQL/Redis/API/Worker and waits for the health endpoint.

API docs: http://localhost:8000/docs

## First pipeline run

```powershell
.\scripts\windows\run-once.ps1
Invoke-RestMethod http://localhost:8000/v1/runs
Invoke-RestMethod http://localhost:8000/v1/opportunities
```

## Operations

```powershell
.\scripts\windows\status.ps1
.\scripts\windows\logs.ps1
.\scripts\windows\stop.ps1
```

## PowerShell execution policy

If Windows blocks local scripts, run them for the current process only:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

Do not expose PostgreSQL or Redis ports to the LAN/Internet. Only the API publishes port 8000 in v0.1.
