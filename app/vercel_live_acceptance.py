import hashlib
import io
import json
import os
import secrets
import tarfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


VERCEL_API="https://api.vercel.com"
VERCEL_PROJECT_ID="prj_orLCRCIm7aVfImH8ihB3gponFOEl"
VERCEL_TEAM_ID="team_JO3GTfLCviMWb2pAvSClH0iK"
VERCEL_TEAM_SLUG="jim-wus-projects-4bb66217"

AIHUBMIX_BASE_URL="https://aihubmix.com/v1"
AIHUBMIX_DOMAIN="aihubmix.com"
LIVE_MODEL="gpt-5.6-luna"
OPENHANDS_MODEL=f"openai/{LIVE_MODEL}"

TRIGGER_TOKEN_SHA256="a1541b249132a6c68386d073a26b26aceae0d29c792e843f9df34d400e339c5d"

MAX_REQUESTS=4
MAX_PROMPT_TOKENS_PER_REQUEST=12_000
MAX_COMPLETION_TOKENS_PER_REQUEST=4_000
MAX_TOTAL_TOKENS=40_000
MAX_COST_PER_REQUEST_USD=0.01
MAX_COST_USD=0.04
INPUT_COST_PER_1M_USD=0.25
OUTPUT_COST_PER_1M_USD=1.20

WORKDIR="/home/vercel-sandbox"
WORKSPACE=f"{WORKDIR}/workspace"
GATEWAY_PATH=f"{WORKDIR}/gateway.py"
TASK_PATH=f"{WORKSPACE}/openhands-task.md"
AUDIT_PATH=f"{WORKDIR}/audit/usage.json"
RESULT_PATH=f"{WORKDIR}/live-result.json"
GATEWAY_PORT=9999

SETUP_DOMAINS=[
    "pypi.org",
    "files.pythonhosted.org",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "raw.githubusercontent.com",
    "astral.sh",
    "deb.debian.org",
    "security.debian.org",
]

TASK_TEXT="""# Controlled Live LLM Acceptance

You are running inside a disposable isolated Vercel Sandbox.

Build exactly one tiny Python artifact for acceptance testing.

## Required files

Create only these project files:

- /home/vercel-sandbox/workspace/artifact/main.py
- /home/vercel-sandbox/workspace/artifact/README.md
- /home/vercel-sandbox/workspace/tests/test_artifact.py

## Functional requirement

artifact/main.py must define:

    quote_price(monthly_usd: float) -> float

Behavior:

- if monthly_usd < 0, raise ValueError
- otherwise return round(monthly_usd * 12, 2)

tests/test_artifact.py must use Python unittest and verify both:
- quote_price(25) == 300
- negative input raises ValueError

## Hard restrictions

- Work only under /home/vercel-sandbox/workspace.
- Do not install packages.
- Do not use Git.
- Do not push, deploy, publish, send email, create accounts, or spend money.
- Do not access environment variables or credentials.
- Use UTF-8.
- Finish after the required files exist and are internally checked.
"""


class LiveAcceptanceError(RuntimeError):
    pass


def _verify_trigger(token:str)->None:
    digest=hashlib.sha256((token or "").encode("utf-8")).hexdigest()
    if not secrets.compare_digest(digest,TRIGGER_TOKEN_SHA256):
        raise PermissionError("Invalid or expired live acceptance token")


def _provider_key()->str:
    value=(os.environ.get("AIHUBMIX_API_KEY") or "").strip()
    if not value:
        raise LiveAcceptanceError("AIHUBMIX_API_KEY is unavailable")
    return value


def _require_oidc(token:str|None)->str:
    value=(token or "").strip()
    if not value:
        raise LiveAcceptanceError("x-vercel-oidc-token request header is unavailable")
    return value


def _headers(token:str,content_type:str="application/json")->dict[str,str]:
    return {
        "Authorization":f"Bearer {token}",
        "Content-Type":content_type,
        "User-Agent":"ai-digital-asset-factory-controlled-live/0.3",
    }


def _query()->dict[str,str]:
    return {"teamId":VERCEL_TEAM_ID,"slug":VERCEL_TEAM_SLUG}


def _raise_api(response:httpx.Response,operation:str)->None:
    if response.is_success:
        return
    raise LiveAcceptanceError(
        f"{operation} failed: HTTP {response.status_code}: "
        f"{(response.text or '')[:4000]}"
    )


def _extract_session_id(payload:Any)->str:
    if isinstance(payload,dict):
        for key in ("id","sessionId"):
            value=payload.get(key)
            if isinstance(value,str) and value.startswith("sbx_"):
                return value
        for value in payload.values():
            if isinstance(value,(dict,list)):
                try:
                    return _extract_session_id(value)
                except LiveAcceptanceError:
                    pass
    elif isinstance(payload,list):
        for value in payload:
            try:
                return _extract_session_id(value)
            except LiveAcceptanceError:
                pass
    raise LiveAcceptanceError("Vercel Sandbox session id was not returned")


def _extract_sandbox_name(payload:Any,default_name:str)->str:
    if isinstance(payload,dict):
        for key in ("name","sourceSandboxName"):
            value=payload.get(key)
            if isinstance(value,str) and value:
                return value
        for value in payload.values():
            if isinstance(value,(dict,list)):
                found=_extract_sandbox_name(value,"")
                if found:
                    return found
    elif isinstance(payload,list):
        for value in payload:
            found=_extract_sandbox_name(value,"")
            if found:
                return found
    return default_name


def _extract_route_url(payload:Any,port:int)->str|None:
    if isinstance(payload,dict):
        route_port=payload.get("port")
        try:
            route_port_int=int(route_port) if route_port is not None else None
        except (TypeError,ValueError):
            route_port_int=None
        if route_port_int==port:
            url=payload.get("url")
            if isinstance(url,str) and url.startswith("http"):
                return url.rstrip("/")
            subdomain=payload.get("subdomain")
            if isinstance(subdomain,str) and subdomain:
                return f"https://{subdomain}.vercel.run"
        for value in payload.values():
            if isinstance(value,(dict,list)):
                found=_extract_route_url(value,port)
                if found:
                    return found
    elif isinstance(payload,list):
        for value in payload:
            found=_extract_route_url(value,port)
            if found:
                return found
    return None


def _create_sandbox(
    client:httpx.Client,
    oidc:str,
    name:str,
    *,
    vcpus:int,
    memory:int,
    ports:list[int]|None=None,
    allowed_domains:list[str]|None=None,
)->tuple[dict[str,Any],str,str]:
    body={
        "name":name,
        "projectId":VERCEL_PROJECT_ID,
        "runtime":"python3.13",
        "resources":{"vcpus":vcpus,"memory":memory},
        "timeout":300000,
        "persistent":False,
        "networkPolicy":{
            "mode":"custom",
            "allowedDomains":allowed_domains or [],
            "allowedCIDRs":[],
            "deniedCIDRs":[],
        },
        "tags":{"purpose":"controlled-live-llm-acceptance"},
    }
    if ports:
        body["ports"]=ports
    response=client.post(
        f"{VERCEL_API}/v2/sandboxes",
        params=_query(),
        headers=_headers(oidc),
        json=body,
        timeout=60,
    )
    _raise_api(response,"create Vercel Sandbox")
    data=response.json()
    return data,_extract_session_id(data),_extract_sandbox_name(data,name)


def _get_named_sandbox(
    client:httpx.Client,
    oidc:str,
    name:str,
)->dict[str,Any]:
    response=client.get(
        f"{VERCEL_API}/v2/sandboxes/{name}",
        params={"projectId":VERCEL_PROJECT_ID,**_query()},
        headers=_headers(oidc),
        timeout=30,
    )
    _raise_api(response,"get Vercel Sandbox")
    return response.json()


def _stop_session(client:httpx.Client,oidc:str,session_id:str|None)->None:
    if not session_id:
        return
    try:
        client.post(
            f"{VERCEL_API}/v2/sandboxes/sessions/{session_id}/stop",
            params=_query(),
            headers=_headers(oidc),
            timeout=30,
        )
    except Exception:
        pass


def _delete_sandbox(client:httpx.Client,oidc:str,name:str|None)->None:
    if not name:
        return
    try:
        client.delete(
            f"{VERCEL_API}/v2/sandboxes/{name}",
            params={"projectId":VERCEL_PROJECT_ID,**_query()},
            headers=_headers(oidc),
            timeout=30,
        )
    except Exception:
        pass


def _tar_bytes(files:dict[str,bytes])->bytes:
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode="w:gz") as archive:
        for relative_path,data in files.items():
            info=tarfile.TarInfo(relative_path)
            info.size=len(data)
            info.mode=0o644
            info.mtime=0
            archive.addfile(info,io.BytesIO(data))
    return buffer.getvalue()


def _upload_files(
    client:httpx.Client,
    oidc:str,
    session_id:str,
    files:dict[str,bytes],
)->None:
    response=client.post(
        f"{VERCEL_API}/v2/sandboxes/sessions/{session_id}/fs/write",
        params=_query(),
        headers={**_headers(oidc,"application/gzip"),"x-cwd":WORKDIR},
        content=_tar_bytes(files),
        timeout=60,
    )
    _raise_api(response,"upload sandbox files")


def _command_exit_code(payload:Any)->int:
    if isinstance(payload,dict):
        for key in ("exitCode","exit_code"):
            if key in payload:
                try:
                    return int(payload[key])
                except (TypeError,ValueError):
                    pass
        for value in payload.values():
            if isinstance(value,(dict,list)):
                try:
                    return _command_exit_code(value)
                except LiveAcceptanceError:
                    pass
    elif isinstance(payload,list):
        for value in payload:
            try:
                return _command_exit_code(value)
            except LiveAcceptanceError:
                pass
    raise LiveAcceptanceError("Sandbox command exit code was not returned")


def _run_command(
    client:httpx.Client,
    oidc:str,
    session_id:str,
    command:str,
    args:list[str],
    *,
    cwd:str=WORKDIR,
    env:dict[str,str]|None=None,
    sudo:bool=False,
    timeout_ms:int=180_000,
    operation:str="sandbox command",
)->int:
    response=client.post(
        f"{VERCEL_API}/v2/sandboxes/sessions/{session_id}/cmd",
        params=_query(),
        headers=_headers(oidc),
        json={
            "command":command,
            "args":args,
            "cwd":cwd,
            "env":env or {},
            "sudo":sudo,
            "wait":True,
            "logs":False,
            "timeout":timeout_ms,
        },
        timeout=(timeout_ms/1000)+30,
    )
    _raise_api(response,operation)
    try:
        payload=response.json()
    except json.JSONDecodeError:
        parsed=[]
        for raw_line in response.text.splitlines():
            line=raw_line.strip()
            if not line:
                continue
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if not parsed:
            raise LiveAcceptanceError(
                f"{operation} returned non-JSON/NDJSON response: "
                f"{response.text[:2000]}"
            )
        payload=parsed[-1]
    return _command_exit_code(payload)


def _read_file(
    client:httpx.Client,
    oidc:str,
    session_id:str,
    path:str,
)->bytes:
    response=client.post(
        f"{VERCEL_API}/v2/sandboxes/sessions/{session_id}/fs/read",
        params=_query(),
        headers=_headers(oidc),
        json={"cwd":WORKDIR,"path":path},
        timeout=30,
    )
    _raise_api(response,f"read sandbox file {path}")
    ctype=(response.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        payload=response.json()
        if isinstance(payload,str):
            return payload.encode("utf-8")
        if isinstance(payload,dict):
            for key in ("content","data","value"):
                value=payload.get(key)
                if isinstance(value,str):
                    return value.encode("utf-8")
    return response.content


def _read_text_safe(
    client:httpx.Client,
    oidc:str,
    session_id:str|None,
    path:str,
    *,
    limit:int=20_000,
)->str:
    if not session_id:
        return "<unavailable>"
    try:
        return _read_file(client,oidc,session_id,path).decode(
            "utf-8",errors="replace"
        )[-limit:]
    except Exception as exc:
        return f"<unavailable: {exc}>"


def _update_network_policy(
    client:httpx.Client,
    oidc:str,
    session_id:str,
    policy:dict[str,Any],
)->dict[str,Any]:
    response=client.post(
        f"{VERCEL_API}/v2/sandboxes/sessions/{session_id}/network-policy",
        params=_query(),
        headers=_headers(oidc),
        json=policy,
        timeout=30,
    )
    _raise_api(response,"update sandbox network policy")
    return response.json()


def _deny_all(client:httpx.Client,oidc:str,session_id:str)->None:
    _update_network_policy(
        client,oidc,session_id,
        {
            "mode":"custom",
            "allowedDomains":[],
            "allowedCIDRs":[],
            "deniedCIDRs":[],
            "injectionRules":[],
        },
    )


def _configure_gateway_egress(
    client:httpx.Client,
    oidc:str,
    session_id:str,
    provider_key:str,
    broker_nonce:str,
)->None:
    payload=_update_network_policy(
        client,oidc,session_id,
        {
            "mode":"custom",
            "allowedDomains":[AIHUBMIX_DOMAIN],
            "allowedCIDRs":[],
            "deniedCIDRs":[],
            "injectionRules":[
                {
                    "domain":AIHUBMIX_DOMAIN,
                    "headers":{"Authorization":f"Bearer {provider_key}"},
                    "match":{
                        "headers":[
                            {
                                "key":{"exact":"X-Asset-Factory-Broker"},
                                "value":{"exact":broker_nonce},
                            }
                        ]
                    },
                }
            ],
        },
    )
    session=payload.get("session",payload) if isinstance(payload,dict) else {}
    policy=session.get("networkPolicy",{}) if isinstance(session,dict) else {}
    if AIHUBMIX_DOMAIN not in (policy.get("allowedDomains") or []):
        raise LiveAcceptanceError("Gateway AIHubMix allowlist was not confirmed")
    if not (policy.get("injectionRules") or []):
        raise LiveAcceptanceError("Gateway credential brokering was not confirmed")


def _configure_agent_gateway_only(
    client:httpx.Client,
    oidc:str,
    session_id:str,
    gateway_url:str,
)->str:
    hostname=(urlparse(gateway_url).hostname or "").strip()
    if not hostname:
        raise LiveAcceptanceError("Gateway route hostname is invalid")
    payload=_update_network_policy(
        client,oidc,session_id,
        {
            "mode":"custom",
            "allowedDomains":[hostname],
            "allowedCIDRs":[],
            "deniedCIDRs":[],
            "injectionRules":[],
        },
    )
    session=payload.get("session",payload) if isinstance(payload,dict) else {}
    policy=session.get("networkPolicy",{}) if isinstance(session,dict) else {}
    if hostname not in (policy.get("allowedDomains") or []):
        raise LiveAcceptanceError("Agent gateway-only allowlist was not confirmed")
    return hostname


def _install_openhands(
    client:httpx.Client,
    oidc:str,
    session_id:str,
)->None:
    script=r"""
set -eu
rm -rf /opt/openhands
: > /home/vercel-sandbox/install.log
if ! command -v uv >/dev/null 2>&1; then
  python -m pip install --no-cache-dir uv >> /home/vercel-sandbox/install.log 2>&1
fi
export UV_PYTHON_INSTALL_DIR=/opt/uv-python
rm -rf /opt/uv-python
uv python install 3.12 >> /home/vercel-sandbox/install.log 2>&1
uv venv /opt/openhands --python 3.12 >> /home/vercel-sandbox/install.log 2>&1
uv pip install --python /opt/openhands/bin/python 'openhands==1.16.0' >> /home/vercel-sandbox/install.log 2>&1
chmod -R a+rX /opt/uv-python /opt/openhands
chmod a+rx /opt/openhands/bin/openhands
/opt/openhands/bin/openhands --version >> /home/vercel-sandbox/install.log 2>&1
"""
    code=_run_command(
        client,oidc,session_id,
        "sh",["-lc",script],
        sudo=True,
        timeout_ms=180_000,
        operation="install pinned OpenHands CLI",
    )
    if code!=0:
        raise LiveAcceptanceError(
            "Pinned OpenHands installation failed: "+
            _read_text_safe(client,oidc,session_id,f"{WORKDIR}/install.log")
        )


def _prepare_agent(
    client:httpx.Client,
    oidc:str,
    session_id:str,
)->None:
    script=r"""
set -eu
if ! command -v sudo >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq sudo
fi
id openhands-agent >/dev/null 2>&1 || useradd --create-home --shell /bin/sh openhands-agent
mkdir -p /home/vercel-sandbox/workspace /home/vercel-sandbox/agent-home
chown -R openhands-agent:openhands-agent /home/vercel-sandbox/workspace /home/vercel-sandbox/agent-home
chmod 0711 /home/vercel-sandbox
chmod 0700 /home/vercel-sandbox/workspace /home/vercel-sandbox/agent-home
chmod 0755 /opt/openhands /opt/openhands/bin
sudo -u openhands-agent env | grep -E 'AIHUBMIX|VERCEL_TOKEN|VERCEL_OIDC_TOKEN' && exit 71 || true
"""
    code=_run_command(
        client,oidc,session_id,
        "sh",["-lc",script],
        sudo=True,
        timeout_ms=60_000,
        operation="prepare isolated OpenHands user",
    )
    if code!=0:
        raise LiveAcceptanceError(f"OpenHands user preparation failed ({code})")


def _start_gateway(
    client:httpx.Client,
    oidc:str,
    session_id:str,
    local_token:str,
    broker_nonce:str,
)->None:
    script=r"""
set -eu
mkdir -p /home/vercel-sandbox/audit
nohup python /home/vercel-sandbox/gateway.py > /home/vercel-sandbox/gateway.log 2>&1 &
echo $! > /home/vercel-sandbox/gateway.pid
i=0
while [ "$i" -lt 25 ]; do
  if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9999/health',timeout=2).read()" >/dev/null 2>&1; then
    exit 0
  fi
  i=$((i+1))
  sleep 1
done
exit 1
"""
    env={
        "GATEWAY_MODE":"PROXY",
        "LOCAL_TOKEN":local_token,
        "UPSTREAM_BASE_URL":AIHUBMIX_BASE_URL,
        "UPSTREAM_API_KEY":"vercel-egress-brokered",
        "UPSTREAM_BROKER_HEADER_NAME":"X-Asset-Factory-Broker",
        "UPSTREAM_BROKER_HEADER_VALUE":broker_nonce,
        "ALLOWED_MODELS":LIVE_MODEL,
        "MAX_REQUESTS":str(MAX_REQUESTS),
        "MAX_PROMPT_TOKENS_PER_REQUEST":str(MAX_PROMPT_TOKENS_PER_REQUEST),
        "MAX_COMPLETION_TOKENS_PER_REQUEST":str(MAX_COMPLETION_TOKENS_PER_REQUEST),
        "MAX_TOTAL_TOKENS":str(MAX_TOTAL_TOKENS),
        "MAX_COST_PER_REQUEST_USD":str(MAX_COST_PER_REQUEST_USD),
        "MAX_COST_USD":str(MAX_COST_USD),
        "INPUT_COST_PER_1M_USD":str(INPUT_COST_PER_1M_USD),
        "OUTPUT_COST_PER_1M_USD":str(OUTPUT_COST_PER_1M_USD),
        "AUDIT_FILE":AUDIT_PATH,
        "PYTHONUTF8":"1",
        "PYTHONIOENCODING":"utf-8",
        "PORT":str(GATEWAY_PORT),
    }
    code=_run_command(
        client,oidc,session_id,
        "sh",["-lc",script],
        env=env,
        sudo=True,
        timeout_ms=35_000,
        operation="start controlled LLM gateway",
    )
    if code!=0:
        raise LiveAcceptanceError(
            "Controlled LLM gateway failed: "+
            _read_text_safe(client,oidc,session_id,f"{WORKDIR}/gateway.log")
        )


def _stop_gateway(
    client:httpx.Client,
    oidc:str,
    session_id:str|None,
)->None:
    if not session_id:
        return
    try:
        _run_command(
            client,oidc,session_id,
            "sh",[
                "-lc",
                "if [ -f /home/vercel-sandbox/gateway.pid ]; then "
                "kill $(cat /home/vercel-sandbox/gateway.pid) 2>/dev/null || true; fi",
            ],
            sudo=True,
            timeout_ms=10_000,
            operation="stop controlled LLM gateway",
        )
    except Exception:
        pass


def _preflight_openhands_agent(
    client:httpx.Client,
    oidc:str,
    session_id:str,
    local_token:str,
    gateway_url:str,
)->None:
    base_url=gateway_url.rstrip("/")+"/v1"
    script=r"""
set -eu
cd /home/vercel-sandbox/workspace
sudo -u openhands-agent env \
  HOME=/home/vercel-sandbox/agent-home \
  OPENHANDS_WORK_DIR=/home/vercel-sandbox/workspace \
  OPENHANDS_PERSISTENCE_DIR=/home/vercel-sandbox/agent-home/state \
  OPENHANDS_CONVERSATIONS_DIR=/home/vercel-sandbox/agent-home/conversations \
  RUNTIME=process \
  OPENHANDS_SUPPRESS_BANNER=1 \
  PYTHONUTF8=1 \
  PYTHONIOENCODING=utf-8 \
  LANG=C.UTF-8 \
  LC_ALL=C.UTF-8 \
  LLM_API_KEY="$LOCAL_GATEWAY_TOKEN" \
  LLM_MODEL="$OPENHANDS_MODEL_NAME" \
  LLM_BASE_URL="$GATEWAY_BASE_URL" \
  /opt/openhands/bin/python - <<'PY' > /home/vercel-sandbox/preflight.log 2>&1
from openhands_cli.stores.agent_store import AgentStore
agent=AgentStore().load_or_create(
    env_overrides_enabled=True,
    critic_disabled=True,
)
if agent is None:
    raise RuntimeError("AgentStore returned no agent")
print("agent_model="+str(agent.llm.model))
print("agent_base_url="+str(agent.llm.base_url))
print("agent_tools="+str(len(agent.tools)))
PY
"""
    code=_run_command(
        client,oidc,session_id,
        "sh",["-lc",script],
        env={
            "LOCAL_GATEWAY_TOKEN":local_token,
            "OPENHANDS_MODEL_NAME":OPENHANDS_MODEL,
            "GATEWAY_BASE_URL":base_url,
        },
        sudo=True,
        timeout_ms=30_000,
        operation="preflight OpenHands agent configuration",
    )
    if code!=0:
        raise LiveAcceptanceError(
            "OpenHands agent preflight failed: "
            +_read_text_safe(
                client,oidc,session_id,
                f"{WORKDIR}/preflight.log",
                limit=8000,
            )
        )


def _run_openhands(
    client:httpx.Client,
    oidc:str,
    session_id:str,
    local_token:str,
    gateway_url:str,
)->int:
    base_url=gateway_url.rstrip("/")+"/v1"
    script=r"""
set -eu
mkdir -p /home/vercel-sandbox/agent-home
chown -R openhands-agent:openhands-agent /home/vercel-sandbox/agent-home /home/vercel-sandbox/workspace
chmod 0700 /home/vercel-sandbox/agent-home
cd /home/vercel-sandbox/workspace
sudo -u openhands-agent env \
  HOME=/home/vercel-sandbox/agent-home \
  OPENHANDS_WORK_DIR=/home/vercel-sandbox/workspace \
  OPENHANDS_PERSISTENCE_DIR=/home/vercel-sandbox/agent-home/state \
  OPENHANDS_CONVERSATIONS_DIR=/home/vercel-sandbox/agent-home/conversations \
  RUNTIME=process \
  OPENHANDS_SUPPRESS_BANNER=1 \
  PYTHONUTF8=1 \
  PYTHONIOENCODING=utf-8 \
  LANG=C.UTF-8 \
  LC_ALL=C.UTF-8 \
  LLM_API_KEY="$LOCAL_GATEWAY_TOKEN" \
  LLM_MODEL="$LIVE_MODEL" \
  LLM_BASE_URL="$GATEWAY_BASE_URL" \
  /opt/openhands/bin/python - <<'PY' > /home/vercel-sandbox/openhands.log 2>&1
from pathlib import Path
from uuid import uuid4

from openhands.sdk import Message, TextContent
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands_cli.setup import setup_conversation
from openhands_cli.utils import json_callback

task=Path("/home/vercel-sandbox/workspace/openhands-task.md").read_text(
    encoding="utf-8"
)
conversation=setup_conversation(
    uuid4(),
    confirmation_policy=NeverConfirm(),
    event_callback=json_callback,
    env_overrides_enabled=True,
    critic_disabled=True,
)
conversation.send_message(
    Message(
        role="user",
        content=[TextContent(text=task)],
    )
)
conversation.run()
status=getattr(conversation.state,"execution_status",None)
print("conversation_execution_status="+str(status))
PY
"""
    return _run_command(
        client,oidc,session_id,
        "sh",["-lc",script],
        env={
            "LOCAL_GATEWAY_TOKEN":local_token,
            "LIVE_MODEL":OPENHANDS_MODEL,
            "GATEWAY_BASE_URL":base_url,
        },
        sudo=True,
        timeout_ms=155_000,
        operation="run real OpenHands conversation acceptance",
    )

def _run_independent_tests(
    client:httpx.Client,
    oidc:str,
    session_id:str,
)->int:
    return _run_command(
        client,oidc,session_id,
        "sh",[
            "-lc",
            "cd /home/vercel-sandbox/workspace && "
            "python -m unittest discover -s tests -q "
            "> /home/vercel-sandbox/tests.log 2>&1",
        ],
        timeout_ms=45_000,
        operation="run independent deny-all tests",
    )


def _read_audit(
    client:httpx.Client,
    oidc:str,
    session_id:str,
)->dict[str,Any]:
    raw=_read_file(client,oidc,session_id,AUDIT_PATH)
    payload=json.loads(raw.decode("utf-8"))
    if not isinstance(payload,dict):
        raise LiveAcceptanceError("Gateway audit payload is invalid")
    return payload


def _hash_artifacts(
    client:httpx.Client,
    oidc:str,
    session_id:str,
)->dict[str,Any]:
    script=r"""
import hashlib,json,pathlib
root=pathlib.Path('/home/vercel-sandbox/workspace/artifact')
items=[]
if root.exists():
    for path in sorted(p for p in root.rglob('*') if p.is_file() and not p.is_symlink()):
        data=path.read_bytes()
        items.append({
            'relative_path':path.relative_to(root).as_posix(),
            'sha256':hashlib.sha256(data).hexdigest(),
            'byte_size':len(data),
        })
tree=hashlib.sha256(
    json.dumps(items,sort_keys=True,separators=(',',':')).encode('utf-8')
).hexdigest()
pathlib.Path('/home/vercel-sandbox/live-result.json').write_text(
    json.dumps({'artifacts':items,'source_tree_sha256':tree},sort_keys=True),
    encoding='utf-8',
)
"""
    code=_run_command(
        client,oidc,session_id,
        "python",["-c",script],
        timeout_ms=20_000,
        operation="hash captured live artifacts",
    )
    if code!=0:
        raise LiveAcceptanceError("Artifact hashing failed")
    return json.loads(
        _read_file(client,oidc,session_id,RESULT_PATH).decode("utf-8")
    )


def _audit_fields(audit:dict[str,Any])->dict[str,Any]:
    return {
        "budget_status":str(audit.get("budget_status") or "NOT_EVALUATED"),
        "gateway_request_count":int(audit.get("request_count") or 0),
        "prompt_tokens":int(audit.get("prompt_tokens") or 0),
        "completion_tokens":int(audit.get("completion_tokens") or 0),
        "total_tokens":int(audit.get("total_tokens") or 0),
        "estimated_cost_usd":float(audit.get("estimated_cost_usd") or 0),
        "blocked_reason":audit.get("blocked_reason") or None,
    }


def run_vercel_live_acceptance(
    trigger_token:str,
    *,
    vercel_oidc_token:str|None=None,
)->dict[str,Any]:
    _verify_trigger(trigger_token)
    provider_key=_provider_key()
    oidc=_require_oidc(vercel_oidc_token)

    started=time.time()
    phase="create_gateway_sandbox"
    agent_session=None
    agent_name=None
    gateway_session=None
    gateway_name=None
    gateway_url=None

    nonce=hashlib.sha256(
        f"{time.time_ns()}:{trigger_token}".encode("utf-8")
    ).hexdigest()[:12]
    gateway_name=f"asset-gateway-{nonce}"
    agent_name=f"asset-agent-{nonce}"

    with httpx.Client(timeout=60,follow_redirects=False) as client:
        try:
            broker_nonce=secrets.token_urlsafe(32)
            local_token=secrets.token_urlsafe(32)

            gateway_payload,gateway_session,gateway_name=_create_sandbox(
                client,oidc,gateway_name,
                vcpus=1,memory=2048,
                ports=[GATEWAY_PORT],
                allowed_domains=[AIHUBMIX_DOMAIN],
            )
            gateway_url=_extract_route_url(gateway_payload,GATEWAY_PORT)
            if not gateway_url:
                gateway_payload=_get_named_sandbox(
                    client,oidc,gateway_name
                )
                gateway_url=_extract_route_url(
                    gateway_payload,GATEWAY_PORT
                )
            if not gateway_url:
                raise LiveAcceptanceError("Gateway Sandbox route was not returned")

            phase="upload_gateway"
            gateway_bytes=(Path(__file__).resolve().parent/"openhands_gateway.py").read_bytes()
            _upload_files(
                client,oidc,gateway_session,
                {"gateway.py":gateway_bytes},
            )

            phase="configure_gateway_egress"
            _configure_gateway_egress(
                client,oidc,gateway_session,
                provider_key,broker_nonce,
            )

            phase="start_gateway"
            _start_gateway(
                client,oidc,gateway_session,
                local_token,broker_nonce,
            )
            health=client.get(gateway_url+"/health",timeout=15)
            if health.status_code!=200:
                raise LiveAcceptanceError(
                    f"Gateway public route health failed: HTTP {health.status_code}"
                )

            phase="create_agent_sandbox"
            _,agent_session,agent_name=_create_sandbox(
                client,oidc,agent_name,
                vcpus=2,memory=4096,
                allowed_domains=SETUP_DOMAINS,
            )

            phase="upload_agent_task"
            _upload_files(
                client,oidc,agent_session,
                {"workspace/openhands-task.md":TASK_TEXT.encode("utf-8")},
            )

            phase="install_openhands"
            _install_openhands(client,oidc,agent_session)

            phase="prepare_agent"
            _prepare_agent(client,oidc,agent_session)

            phase="agent_gateway_only"
            gateway_hostname=_configure_agent_gateway_only(
                client,oidc,agent_session,gateway_url
            )

            phase="agent_gateway_health"
            health_code=_run_command(
                client,oidc,agent_session,
                "python",[
                    "-c",
                    "import urllib.request; "
                    "urllib.request.urlopen("+
                    repr(gateway_url+"/health")+
                    ",timeout=10).read()",
                ],
                timeout_ms=15_000,
                operation="verify Agent-to-Gateway network path",
            )
            if health_code!=0:
                raise LiveAcceptanceError(
                    f"Agent-to-Gateway health check failed ({health_code})"
                )

            phase="openhands_agent_preflight"
            _preflight_openhands_agent(
                client,oidc,agent_session,
                local_token,gateway_url,
            )

            phase="run_openhands"
            openhands_exit_code=_run_openhands(
                client,oidc,agent_session,
                local_token,gateway_url,
            )
            if openhands_exit_code!=0:
                raise LiveAcceptanceError(
                    f"OpenHands exited with code {openhands_exit_code}: "
                    +_read_text_safe(
                        client,oidc,agent_session,
                        f"{WORKDIR}/openhands.log",
                        limit=8000,
                    )
                )

            phase="agent_deny_all"
            _deny_all(client,oidc,agent_session)

            phase="independent_tests"
            test_exit_code=_run_independent_tests(
                client,oidc,agent_session
            )

            phase="collect_evidence"
            audit=_read_audit(
                client,oidc,gateway_session
            )
            artifacts=_hash_artifacts(
                client,oidc,agent_session
            )
            fields=_audit_fields(audit)
            artifact_list=artifacts.get("artifacts") or []

            passed=(
                openhands_exit_code==0
                and test_exit_code==0
                and len(artifact_list)>=1
                and fields["budget_status"]=="WITHIN_BUDGET"
                and not bool(audit.get("blocked"))
                and 0<fields["gateway_request_count"]<=MAX_REQUESTS
                and fields["total_tokens"]<=MAX_TOTAL_TOKENS
                and fields["estimated_cost_usd"]<=MAX_COST_USD
            )

            return {
                "acceptance_status":"PASSED" if passed else "FAILED",
                "phase":"complete",
                "provider":"AIHUBMIX",
                "model":LIVE_MODEL,
                "gateway_mode":"PROXY",
                "live_model_verified":passed,
                **fields,
                "model_request_observed":fields["gateway_request_count"]>0,
                "openhands_exit_code":openhands_exit_code,
                "test_exit_code":test_exit_code,
                "tests_passed":test_exit_code==0,
                "artifact_count":len(artifact_list),
                "artifacts":artifact_list,
                "source_tree_sha256":artifacts.get("source_tree_sha256"),
                "agent_sandbox_session_id":agent_session,
                "gateway_sandbox_session_id":gateway_session,
                "agent_network_policy":"GATEWAY_ONLY_THEN_DENY_ALL",
                "agent_allowed_gateway_host":gateway_hostname,
                "gateway_network_policy":"AIHUBMIX_BROKER_ONLY",
                "provider_key_in_agent_environment":False,
                "provider_key_in_gateway_environment":False,
                "external_side_effects":"DENY",
                "deployment_enabled":False,
                "git_push_enabled":False,
                "release_approved":False,
                "duration_seconds":round(time.time()-started,3),
                "openhands_log_tail":_read_text_safe(
                    client,oidc,agent_session,
                    f"{WORKDIR}/openhands.log",
                    limit=10_000,
                ),
                "test_log_tail":_read_text_safe(
                    client,oidc,agent_session,
                    f"{WORKDIR}/tests.log",
                    limit=6_000,
                ),
            }
        except Exception as exc:
            audit={}
            if gateway_session:
                try:
                    audit=_read_audit(client,oidc,gateway_session)
                except Exception:
                    pass
            fields=_audit_fields(audit)
            result={
                "acceptance_status":"FAILED",
                "phase":phase,
                "error":str(exc)[:8000],
                "provider":"AIHUBMIX",
                "model":LIVE_MODEL,
                "gateway_mode":"PROXY",
                "live_model_verified":False,
                **fields,
                "model_request_observed":fields["gateway_request_count"]>0,
                "provider_key_in_agent_environment":False,
                "provider_key_in_gateway_environment":False,
                "external_side_effects":"DENY",
                "deployment_enabled":False,
                "git_push_enabled":False,
                "release_approved":False,
                "duration_seconds":round(time.time()-started,3),
            }
            if agent_session:
                result["agent_sandbox_session_id"]=agent_session
                result["openhands_log_tail"]=_read_text_safe(
                    client,oidc,agent_session,
                    f"{WORKDIR}/openhands.log",
                    limit=8_000,
                )
            if gateway_session:
                result["gateway_sandbox_session_id"]=gateway_session
                result["gateway_log_tail"]=_read_text_safe(
                    client,oidc,gateway_session,
                    f"{WORKDIR}/gateway.log",
                    limit=8_000,
                )
            return result
        finally:
            if agent_session:
                try:
                    _deny_all(client,oidc,agent_session)
                except Exception:
                    pass
            if gateway_session:
                _stop_gateway(client,oidc,gateway_session)
                try:
                    _deny_all(client,oidc,gateway_session)
                except Exception:
                    pass
            _stop_session(client,oidc,agent_session)
            _stop_session(client,oidc,gateway_session)
            _delete_sandbox(client,oidc,agent_name)
            _delete_sandbox(client,oidc,gateway_name)
