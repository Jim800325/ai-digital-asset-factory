import hashlib
import io
import json
import os
import secrets
import shlex
import tarfile
import time
from pathlib import Path
from typing import Any

import httpx


VERCEL_API="https://api.vercel.com"
VERCEL_PROJECT_ID="prj_orLCRCIm7aVfImH8ihB3gponFOEl"
VERCEL_TEAM_ID="team_JO3GTfLCviMWb2pAvSClH0iK"
VERCEL_TEAM_SLUG="jim-wus-projects-4bb66217"

AIHUBMIX_BASE_URL="https://aihubmix.com/v1"
AIHUBMIX_DOMAIN="aihubmix.com"
LIVE_MODEL="gpt-5.6-luna"

# Raw trigger is never stored in Git. Rotate after each attempted live acceptance.
TRIGGER_TOKEN_SHA256="2ba1c191767889735822816b80cc40ea16257d4983f58e9d78b78e6fa25cd93a"

MAX_REQUESTS=4
MAX_PROMPT_TOKENS_PER_REQUEST=12_000
MAX_COMPLETION_TOKENS_PER_REQUEST=4_000
MAX_TOTAL_TOKENS=40_000
MAX_COST_PER_REQUEST_USD=0.01
MAX_COST_USD=0.04

# Conservative local accounting.
INPUT_COST_PER_1M_USD=0.25
OUTPUT_COST_PER_1M_USD=1.20

WORKDIR="/home/vercel-sandbox"
WORKSPACE=f"{WORKDIR}/workspace"
GATEWAY_PATH=f"{WORKDIR}/gateway.py"
TASK_PATH=f"{WORKSPACE}/openhands-task.md"
AUDIT_PATH=f"{WORKDIR}/audit/usage.json"
RESULT_PATH=f"{WORKDIR}/live-result.json"

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
- Do not access network resources.
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
    key=(os.environ.get("AIHUBMIX_API_KEY") or "").strip()
    if not key:
        raise LiveAcceptanceError("AIHUBMIX_API_KEY is unavailable")
    return key


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


def _create_sandbox(
    client:httpx.Client,
    oidc:str,
    name:str,
)->tuple[str,str]:
    response=client.post(
        f"{VERCEL_API}/v2/sandboxes",
        params=_query(),
        headers=_headers(oidc),
        json={
            "name":name,
            "projectId":VERCEL_PROJECT_ID,
            "runtime":"python3.13",
            "resources":{"vcpus":2,"memory":4096},
            "timeout":300000,
            "persistent":False,
            "networkPolicy":{
                "mode":"custom",
                "allowedDomains":SETUP_DOMAINS,
                "allowedCIDRs":[],
                "deniedCIDRs":[],
            },
            "tags":{
                "purpose":"controlled-live-llm-acceptance",
                "provider":"aihubmix",
                "model":LIVE_MODEL,
            },
        },
        timeout=60,
    )
    _raise_api(response,"create Vercel Sandbox")
    data=response.json()
    return _extract_session_id(data),_extract_sandbox_name(data,name)


def _stop_session(
    client:httpx.Client,
    oidc:str,
    session_id:str|None,
)->None:
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


def _delete_sandbox(
    client:httpx.Client,
    oidc:str,
    sandbox_name:str|None,
)->None:
    if not sandbox_name:
        return
    try:
        client.delete(
            f"{VERCEL_API}/v2/sandboxes/{sandbox_name}",
            params={
                "projectId":VERCEL_PROJECT_ID,
                **_query(),
            },
            headers=_headers(oidc),
            timeout=30,
        )
    except Exception:
        pass


def _build_upload_tar()->bytes:
    gateway=(Path(__file__).resolve().parent/"openhands_gateway.py").read_bytes()
    files={
        "gateway.py":gateway,
        "workspace/openhands-task.md":TASK_TEXT.encode("utf-8"),
    }
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
)->None:
    response=client.post(
        f"{VERCEL_API}/v2/sandboxes/sessions/{session_id}/fs/write",
        params=_query(),
        headers={
            **_headers(oidc,"application/gzip"),
            "x-cwd":WORKDIR,
        },
        content=_build_upload_tar(),
        timeout=60,
    )
    _raise_api(response,"upload acceptance files")


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
    except json.JSONDecodeError as exc:
        raise LiveAcceptanceError(
            f"{operation} returned non-JSON response: {response.text[:2000]}"
        ) from exc
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
    session_id:str,
    path:str,
    *,
    limit:int=20_000,
)->str:
    try:
        return _read_file(client,oidc,session_id,path).decode(
            "utf-8",errors="replace"
        )[-limit:]
    except Exception as exc:
        return f"<unavailable: {exc}>"


def _read_audit_safe(
    client:httpx.Client,
    oidc:str,
    session_id:str|None,
)->dict[str,Any]:
    if not session_id:
        return {}
    try:
        raw=_read_file(client,oidc,session_id,AUDIT_PATH)
        payload=json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload,dict) else {}
    except Exception:
        return {}


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
uv python install 3.12 >> /home/vercel-sandbox/install.log 2>&1
uv venv /opt/openhands --python 3.12 >> /home/vercel-sandbox/install.log 2>&1
uv pip install --python /opt/openhands/bin/python 'openhands==1.16.0' >> /home/vercel-sandbox/install.log 2>&1
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
        log=_read_text_safe(
            client,oidc,session_id,f"{WORKDIR}/install.log"
        )
        raise LiveAcceptanceError(
            f"Pinned OpenHands installation failed ({code}). Tail: {log}"
        )


def _prepare_agent_and_firewall(
    client:httpx.Client,
    oidc:str,
    session_id:str,
)->None:
    script=r"""
set -eu
if ! command -v sudo >/dev/null 2>&1 || ! command -v iptables >/dev/null 2>&1 || ! command -v ip6tables >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq sudo iptables
fi

id openhands-agent >/dev/null 2>&1 || useradd --create-home --shell /bin/sh openhands-agent
mkdir -p /home/vercel-sandbox/workspace /home/vercel-sandbox/audit /home/vercel-sandbox/agent-home
chown -R openhands-agent:openhands-agent /home/vercel-sandbox/workspace /home/vercel-sandbox/agent-home
chmod 0700 /home/vercel-sandbox/workspace /home/vercel-sandbox/agent-home
chmod 0755 /opt/openhands /opt/openhands/bin

AGENT_UID="$(id -u openhands-agent)"
iptables -C OUTPUT -m owner --uid-owner "$AGENT_UID" '!' -o lo -j REJECT 2>/dev/null ||   iptables -A OUTPUT -m owner --uid-owner "$AGENT_UID" '!' -o lo -j REJECT
ip6tables -C OUTPUT -m owner --uid-owner "$AGENT_UID" '!' -o lo -j REJECT 2>/dev/null ||   ip6tables -A OUTPUT -m owner --uid-owner "$AGENT_UID" '!' -o lo -j REJECT
iptables -C OUTPUT -m owner --uid-owner "$AGENT_UID" '!' -o lo -j REJECT
ip6tables -C OUTPUT -m owner --uid-owner "$AGENT_UID" '!' -o lo -j REJECT

sudo -u openhands-agent env | grep -E 'AIHUBMIX|VERCEL_TOKEN|VERCEL_OIDC_TOKEN' && exit 71 || true
"""
    code=_run_command(
        client,oidc,session_id,
        "sh",["-lc",script],
        sudo=True,
        timeout_ms=70_000,
        operation="establish UID-level OpenHands network isolation",
    )
    if code!=0:
        raise LiveAcceptanceError(
            f"UID-level OpenHands network isolation failed ({code})"
        )


def _lock_network_to_aihubmix(
    client:httpx.Client,
    oidc:str,
    provider_key:str,
    session_id:str,
    broker_nonce:str,
)->None:
    response=client.post(
        f"{VERCEL_API}/v2/sandboxes/sessions/{session_id}/network-policy",
        params=_query(),
        headers=_headers(oidc),
        json={
            "mode":"custom",
            "allowedDomains":[AIHUBMIX_DOMAIN],
            "allowedCIDRs":[],
            "deniedCIDRs":[],
            "injectionRules":[
                {
                    "domain":AIHUBMIX_DOMAIN,
                    "headers":{
                        "Authorization":f"Bearer {provider_key}",
                    },
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
        timeout=30,
    )
    _raise_api(
        response,
        "apply AIHubMix-only credential-brokered network policy",
    )
    payload=response.json()
    session=payload.get("session",payload) if isinstance(payload,dict) else {}
    policy=session.get("networkPolicy",{}) if isinstance(session,dict) else {}
    rules=policy.get("injectionRules",[]) if isinstance(policy,dict) else []
    domains=policy.get("allowedDomains",[]) if isinstance(policy,dict) else []
    confirmed=(
        AIHUBMIX_DOMAIN in domains
        and isinstance(rules,list)
        and len(rules)>0
    )
    if not confirmed:
        raise LiveAcceptanceError(
            "Vercel did not confirm AIHubMix-only credential brokering"
        )


def _deny_all_network(
    client:httpx.Client,
    oidc:str,
    session_id:str,
)->None:
    response=client.post(
        f"{VERCEL_API}/v2/sandboxes/sessions/{session_id}/network-policy",
        params=_query(),
        headers=_headers(oidc),
        json={
            "mode":"custom",
            "allowedDomains":[],
            "allowedCIDRs":[],
            "deniedCIDRs":[],
            "injectionRules":[],
        },
        timeout=30,
    )
    _raise_api(response,"lock sandbox network to deny-all")


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
        "PORT":"9999",
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
        log=_read_text_safe(
            client,oidc,session_id,f"{WORKDIR}/gateway.log"
        )
        raise LiveAcceptanceError(
            f"Controlled LLM gateway failed ({code}). Tail: {log}"
        )


def _run_openhands(
    client:httpx.Client,
    oidc:str,
    session_id:str,
    local_token:str,
)->int:
    script=r"""
set -eu
mkdir -p /home/vercel-sandbox/agent-home
chown -R openhands-agent:openhands-agent /home/vercel-sandbox/agent-home /home/vercel-sandbox/workspace
chmod 0700 /home/vercel-sandbox/agent-home

sudo -u openhands-agent env   HOME=/home/vercel-sandbox/agent-home   OPENHANDS_WORK_DIR=/home/vercel-sandbox/workspace   OPENHANDS_PERSISTENCE_DIR=/home/vercel-sandbox/agent-home/state   OPENHANDS_CONVERSATIONS_DIR=/home/vercel-sandbox/agent-home/conversations   RUNTIME=process   OPENHANDS_SUPPRESS_BANNER=1   PYTHONUTF8=1   PYTHONIOENCODING=utf-8   LANG=C.UTF-8   LC_ALL=C.UTF-8   LLM_API_KEY="$LOCAL_GATEWAY_TOKEN"   LLM_MODEL="$LIVE_MODEL"   LLM_BASE_URL=http://127.0.0.1:9999/v1   /opt/openhands/bin/openhands --headless --json --override-with-envs     -f /home/vercel-sandbox/workspace/openhands-task.md     > /home/vercel-sandbox/openhands.log 2>&1
"""
    return _run_command(
        client,oidc,session_id,
        "sh",["-lc",script],
        env={
            "LOCAL_GATEWAY_TOKEN":local_token,
            "LIVE_MODEL":LIVE_MODEL,
        },
        sudo=True,
        timeout_ms=155_000,
        operation="run real OpenHands acceptance",
    )


def _stop_gateway(
    client:httpx.Client,
    oidc:str,
    session_id:str,
)->None:
    try:
        _run_command(
            client,oidc,session_id,
            "sh",["-lc","if [ -f /home/vercel-sandbox/gateway.pid ]; then kill $(cat /home/vercel-sandbox/gateway.pid) 2>/dev/null || true; fi"],
            sudo=True,
            timeout_ms=10_000,
            operation="stop controlled LLM gateway",
        )
    except Exception:
        pass


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
        sudo=False,
        timeout_ms=45_000,
        operation="run independent deny-all tests",
    )


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
        sudo=False,
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
    phase="create_sandbox"
    session_id:str|None=None
    sandbox_name:str|None=None
    name="asset-live-"+hashlib.sha256(
        f"{time.time_ns()}:{trigger_token}".encode("utf-8")
    ).hexdigest()[:12]

    with httpx.Client(timeout=60,follow_redirects=False) as client:
        try:
            session_id,sandbox_name=_create_sandbox(
                client,oidc,name
            )

            phase="write_acceptance_files"
            _upload_files(client,oidc,session_id)

            phase="install_openhands"
            _install_openhands(client,oidc,session_id)

            phase="isolate_agent_network"
            _prepare_agent_and_firewall(client,oidc,session_id)

            phase="credential_brokering"
            broker_nonce=secrets.token_urlsafe(32)
            _lock_network_to_aihubmix(
                client,oidc,provider_key,session_id,broker_nonce
            )

            phase="start_gateway"
            local_token=secrets.token_urlsafe(32)
            _start_gateway(
                client,oidc,session_id,local_token,broker_nonce
            )

            phase="run_openhands"
            openhands_exit_code=_run_openhands(
                client,oidc,session_id,local_token
            )

            phase="stop_gateway"
            _stop_gateway(client,oidc,session_id)

            phase="deny_all_network"
            _deny_all_network(client,oidc,session_id)

            phase="independent_tests"
            test_exit_code=_run_independent_tests(
                client,oidc,session_id
            )

            phase="collect_evidence"
            audit=_read_audit_safe(client,oidc,session_id)
            artifacts=_hash_artifacts(client,oidc,session_id)
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
                "sandbox_session_id":session_id,
                "sandbox_name":sandbox_name,
                "sandbox_network_policy":"AIHUBMIX_BROKER_ONLY_THEN_DENY_ALL",
                "agent_uid_external_network":"DENY",
                "provider_key_in_sandbox_environment":False,
                "provider_key_in_agent_environment":False,
                "external_side_effects":"DENY",
                "deployment_enabled":False,
                "git_push_enabled":False,
                "release_approved":False,
                "duration_seconds":round(time.time()-started,3),
                "openhands_log_tail":_read_text_safe(
                    client,oidc,session_id,
                    f"{WORKDIR}/openhands.log",
                    limit=10_000,
                ),
                "test_log_tail":_read_text_safe(
                    client,oidc,session_id,
                    f"{WORKDIR}/tests.log",
                    limit=6_000,
                ),
            }
        except Exception as exc:
            audit=_read_audit_safe(client,oidc,session_id)
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
                "provider_key_in_sandbox_environment":False,
                "provider_key_in_agent_environment":False,
                "external_side_effects":"DENY",
                "deployment_enabled":False,
                "git_push_enabled":False,
                "release_approved":False,
                "duration_seconds":round(time.time()-started,3),
            }
            if session_id:
                result["sandbox_session_id"]=session_id
                result["sandbox_name"]=sandbox_name
                result["openhands_log_tail"]=_read_text_safe(
                    client,oidc,session_id,
                    f"{WORKDIR}/openhands.log",
                    limit=8_000,
                )
                result["gateway_log_tail"]=_read_text_safe(
                    client,oidc,session_id,
                    f"{WORKDIR}/gateway.log",
                    limit=5_000,
                )
            return result
        finally:
            if session_id:
                _stop_gateway(client,oidc,session_id)
                try:
                    _deny_all_network(client,oidc,session_id)
                except Exception:
                    pass
            _stop_session(client,oidc,session_id)
            _delete_sandbox(client,oidc,sandbox_name)
