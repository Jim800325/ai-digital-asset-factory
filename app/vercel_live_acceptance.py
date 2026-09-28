import hashlib
import io
import json
import os
import secrets
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

TRIGGER_TOKEN_SHA256="fc5b5b31b3dad77d99d83d4591ce3d26c1068d1ce550f84a006d7a595fab628e"

MAX_REQUESTS=4
MAX_PROMPT_TOKENS_PER_REQUEST=12_000
MAX_COMPLETION_TOKENS_PER_REQUEST=4_000
MAX_TOTAL_TOKENS=40_000
MAX_COST_PER_REQUEST_USD=0.01
MAX_COST_USD=0.04
# Conservative input accounting: AIHubMix standard input is lower than this,
# but a slightly higher rate leaves room for cache-write uplift.
INPUT_COST_PER_1M_USD=0.25
OUTPUT_COST_PER_1M_USD=1.20

WORKDIR="/home/vercel-sandbox"
AGENT_WORKSPACE=f"{WORKDIR}/workspace"
GATEWAY_PATH=f"{WORKDIR}/gateway.py"
TASK_PATH=f"{AGENT_WORKSPACE}/openhands-task.md"
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


def _api_headers(token:str)->dict[str,str]:
    return {
        "Authorization":f"Bearer {token}",
        "Content-Type":"application/json",
        "User-Agent":"ai-digital-asset-factory-controlled-live/0.3",
    }


def _query()->dict[str,str]:
    return {
        "teamId":VERCEL_TEAM_ID,
        "slug":VERCEL_TEAM_SLUG,
    }


def _require_controller_secrets()->tuple[str,str]:
    vercel_token=(os.environ.get("VERCEL_OIDC_TOKEN") or "").strip()
    aihubmix_key=(os.environ.get("AIHUBMIX_API_KEY") or "").strip()
    if not vercel_token:
        raise LiveAcceptanceError("VERCEL_OIDC_TOKEN is unavailable")
    if not aihubmix_key:
        raise LiveAcceptanceError("AIHUBMIX_API_KEY is unavailable")
    return vercel_token,aihubmix_key


def _verify_trigger(token:str)->None:
    digest=hashlib.sha256((token or "").encode("utf-8")).hexdigest()
    if not secrets.compare_digest(digest,TRIGGER_TOKEN_SHA256):
        raise PermissionError("Invalid or expired live acceptance token")


def _extract_session_id(payload:Any)->str:
    if isinstance(payload,dict):
        direct=payload.get("id")
        if isinstance(direct,str) and direct.startswith("sbx_"):
            return direct
        for key in ("session","sandbox","data","value"):
            if key in payload:
                try:
                    return _extract_session_id(payload[key])
                except LiveAcceptanceError:
                    pass
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
        for key in ("sandbox","session","data","value"):
            value=payload.get(key)
            if isinstance(value,(dict,list)):
                found=_extract_sandbox_name(value,"")
                if found:
                    return found
    return default_name


def _raise_api(response:httpx.Response,operation:str)->None:
    if response.is_success:
        return
    detail=(response.text or "")[:4000]
    raise LiveAcceptanceError(
        f"{operation} failed: HTTP {response.status_code}: {detail}"
    )


def _create_sandbox(client:httpx.Client,vercel_token:str,name:str)->tuple[str,str]:
    payload={
        "name":name,
        "projectId":VERCEL_PROJECT_ID,
        "runtime":"python3.13",
        "resources":{"vcpus":"2","memory":"4096"},
        "timeout":"300000",
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
    }
    response=client.post(
        f"{VERCEL_API}/v2/sandboxes",
        params=_query(),
        headers=_api_headers(vercel_token),
        json=payload,
    )
    _raise_api(response,"create Vercel Sandbox")
    data=response.json()
    return _extract_session_id(data),_extract_sandbox_name(data,name)


def _stop_session(
    client:httpx.Client,
    vercel_token:str,
    session_id:str|None,
)->None:
    if not session_id:
        return
    try:
        client.post(
            f"{VERCEL_API}/v2/sandboxes/sessions/{session_id}/stop",
            params=_query(),
            headers=_api_headers(vercel_token),
            timeout=30,
        )
    except Exception:
        pass


def _delete_sandbox(
    client:httpx.Client,
    vercel_token:str,
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
            headers=_api_headers(vercel_token),
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
    vercel_token:str,
    session_id:str,
)->None:
    headers={
        "Authorization":f"Bearer {vercel_token}",
        "Content-Type":"application/gzip",
        "x-cwd":WORKDIR,
        "User-Agent":"ai-digital-asset-factory-controlled-live/0.3",
    }
    response=client.post(
        f"{VERCEL_API}/v2/sandboxes/sessions/{session_id}/fs/write",
        params=_query(),
        headers=headers,
        content=_build_upload_tar(),
        timeout=60,
    )
    _raise_api(response,"upload acceptance files")


def _command_payload(
    command:str,
    args:list[str],
    *,
    cwd:str=WORKDIR,
    env:dict[str,str]|None=None,
    sudo:bool=False,
    timeout_ms:int=240_000,
)->dict[str,Any]:
    return {
        "command":command,
        "args":args,
        "cwd":cwd,
        "env":env or {},
        "sudo":sudo,
        "wait":True,
        "logs":False,
        "timeout":timeout_ms,
    }


def _command_exit_code(payload:Any)->int:
    if isinstance(payload,dict):
        if "exitCode" in payload:
            try:
                return int(payload["exitCode"])
            except (TypeError,ValueError):
                pass
        for key in ("command","data","value"):
            if key in payload:
                try:
                    return _command_exit_code(payload[key])
                except LiveAcceptanceError:
                    pass
    raise LiveAcceptanceError("Sandbox command exit code was not returned")


def _run_command(
    client:httpx.Client,
    vercel_token:str,
    session_id:str,
    command:str,
    args:list[str],
    *,
    cwd:str=WORKDIR,
    env:dict[str,str]|None=None,
    sudo:bool=False,
    timeout_ms:int=240_000,
    operation:str="sandbox command",
)->dict[str,Any]:
    response=client.post(
        f"{VERCEL_API}/v2/sandboxes/sessions/{session_id}/cmd",
        params=_query(),
        headers=_api_headers(vercel_token),
        json=_command_payload(
            command,args,cwd=cwd,env=env,sudo=sudo,timeout_ms=timeout_ms
        ),
        timeout=(timeout_ms/1000)+30,
    )
    _raise_api(response,operation)
    try:
        payload=response.json()
    except json.JSONDecodeError as exc:
        raise LiveAcceptanceError(
            f"{operation} returned non-JSON response: {response.text[:2000]}"
        ) from exc
    return {
        "payload":payload,
        "exit_code":_command_exit_code(payload),
    }


def _read_file(
    client:httpx.Client,
    vercel_token:str,
    session_id:str,
    path:str,
)->bytes:
    response=client.post(
        f"{VERCEL_API}/v2/sandboxes/sessions/{session_id}/fs/read",
        params=_query(),
        headers=_api_headers(vercel_token),
        json={"cwd":WORKDIR,"path":path},
        timeout=30,
    )
    _raise_api(response,f"read sandbox file {path}")
    content_type=(response.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
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
    vercel_token:str,
    session_id:str,
    path:str,
    *,
    limit:int=30_000,
)->str:
    try:
        return _read_file(client,vercel_token,session_id,path).decode(
            "utf-8",errors="replace"
        )[-limit:]
    except Exception as exc:
        return f"<unavailable: {exc}>"


def _install_openhands(
    client:httpx.Client,
    vercel_token:str,
    session_id:str,
)->None:
    script=r"""
set -eu
rm -rf /opt/openhands
python -m venv /opt/openhands
if /opt/openhands/bin/pip install --no-cache-dir 'openhands==1.16.0' > /home/vercel-sandbox/install.log 2>&1; then
  /opt/openhands/bin/openhands --version >> /home/vercel-sandbox/install.log 2>&1
  exit 0
fi
rm -rf /opt/openhands
if ! command -v uv >/dev/null 2>&1; then
  python -m pip install --no-cache-dir uv >> /home/vercel-sandbox/install.log 2>&1
fi
uv python install 3.12 >> /home/vercel-sandbox/install.log 2>&1
uv venv /opt/openhands --python 3.12 >> /home/vercel-sandbox/install.log 2>&1
uv pip install --python /opt/openhands/bin/python 'openhands==1.16.0' >> /home/vercel-sandbox/install.log 2>&1
/opt/openhands/bin/openhands --version >> /home/vercel-sandbox/install.log 2>&1
"""
    result=_run_command(
        client,vercel_token,session_id,
        "sh",["-lc",script],
        sudo=True,
        timeout_ms=210_000,
        operation="install pinned OpenHands CLI",
    )
    if result["exit_code"]!=0:
        log=_read_text_safe(
            client,vercel_token,session_id,
            f"{WORKDIR}/install.log",
        )
        raise LiveAcceptanceError(
            "Pinned OpenHands installation failed. Tail:\n"+log
        )


def _prepare_workspace(
    client:httpx.Client,
    vercel_token:str,
    session_id:str,
)->None:
    script=r"""
set -eu
id openhands-agent >/dev/null 2>&1 || useradd --create-home --shell /bin/sh openhands-agent
mkdir -p /home/vercel-sandbox/workspace /home/vercel-sandbox/audit /home/vercel-sandbox/agent-home
chown -R openhands-agent:openhands-agent /home/vercel-sandbox/workspace /home/vercel-sandbox/agent-home
chmod 0700 /home/vercel-sandbox/workspace /home/vercel-sandbox/agent-home
chmod 0755 /opt/openhands /opt/openhands/bin
"""
    result=_run_command(
        client,vercel_token,session_id,
        "sh",["-lc",script],
        sudo=True,
        operation="prepare isolated OpenHands user/workspace",
    )
    if result["exit_code"]!=0:
        raise LiveAcceptanceError("Failed to prepare isolated OpenHands workspace")


def _lock_network_to_aihubmix(
    client:httpx.Client,
    vercel_token:str,
    aihubmix_key:str,
    session_id:str,
    broker_nonce:str,
)->None:
    policy={
        "mode":"custom",
        "allowedDomains":[AIHUBMIX_DOMAIN],
        "allowedCIDRs":[],
        "deniedCIDRs":[],
        "injectionRules":[
            {
                "domain":AIHUBMIX_DOMAIN,
                "headers":{
                    "Authorization":f"Bearer {aihubmix_key}",
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
    }
    response=client.post(
        f"{VERCEL_API}/v2/sandboxes/sessions/{session_id}/network-policy",
        params=_query(),
        headers=_api_headers(vercel_token),
        json=policy,
        timeout=30,
    )
    _raise_api(response,"apply AIHubMix-only credential-brokered network policy")
    payload=response.json()
    returned=payload.get("session",payload) if isinstance(payload,dict) else {}
    network=returned.get("networkPolicy",{}) if isinstance(returned,dict) else {}
    rules=network.get("injectionRules",[]) if isinstance(network,dict) else []
    if not rules:
        raise LiveAcceptanceError(
            "Vercel did not confirm credential injection rules; live call denied"
        )


def _deny_all_network(
    client:httpx.Client,
    vercel_token:str,
    session_id:str,
)->None:
    response=client.post(
        f"{VERCEL_API}/v2/sandboxes/sessions/{session_id}/network-policy",
        params=_query(),
        headers=_api_headers(vercel_token),
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
    vercel_token:str,
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
while [ "$i" -lt 30 ]; do
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
        "UPSTREAM_API_KEY":"vercel-brokered-placeholder",
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
    }
    result=_run_command(
        client,vercel_token,session_id,
        "sh",["-lc",script],
        env=env,
        sudo=True,
        operation="start controlled LLM gateway",
    )
    if result["exit_code"]!=0:
        log=_read_text_safe(client,vercel_token,session_id,f"{WORKDIR}/gateway.log")
        raise LiveAcceptanceError("Controlled LLM gateway failed to start:\n"+log)


def _run_openhands(
    client:httpx.Client,
    vercel_token:str,
    session_id:str,
    local_token:str,
)->int:
    script=r"""
set -eu
mkdir -p /home/vercel-sandbox/agent-home
chown -R openhands-agent:openhands-agent /home/vercel-sandbox/agent-home /home/vercel-sandbox/workspace
chmod 0700 /home/vercel-sandbox/agent-home
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
  LLM_BASE_URL=http://127.0.0.1:9999/v1 \
  /opt/openhands/bin/openhands --headless --json --override-with-envs \
    -f /home/vercel-sandbox/workspace/openhands-task.md \
    > /home/vercel-sandbox/openhands.log 2>&1
"""
    result=_run_command(
        client,vercel_token,session_id,
        "sh",["-lc",script],
        env={
            "LOCAL_GATEWAY_TOKEN":local_token,
            "LIVE_MODEL":LIVE_MODEL,
        },
        sudo=True,
        timeout_ms=180_000,
        operation="run real OpenHands acceptance",
    )
    return result["exit_code"]


def _run_independent_tests(
    client:httpx.Client,
    vercel_token:str,
    session_id:str,
)->int:
    script=r"""
set -eu
cd /home/vercel-sandbox/workspace
python -m unittest discover -s tests -q > /home/vercel-sandbox/tests.log 2>&1
"""
    result=_run_command(
        client,vercel_token,session_id,
        "sh",["-lc",script],
        sudo=False,
        timeout_ms=60_000,
        operation="run independent deny-all tests",
    )
    return result["exit_code"]


def _build_result(
    client:httpx.Client,
    vercel_token:str,
    session_id:str,
    openhands_exit_code:int,
    test_exit_code:int,
)->dict[str,Any]:
    audit_raw=_read_file(client,vercel_token,session_id,AUDIT_PATH)
    try:
        audit=json.loads(audit_raw.decode("utf-8"))
    except Exception as exc:
        raise LiveAcceptanceError("Gateway audit file is invalid") from exc

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
payload={'artifacts':items,'source_tree_sha256':tree}
pathlib.Path('/home/vercel-sandbox/live-result.json').write_text(
    json.dumps(payload,sort_keys=True),
    encoding='utf-8',
)
"""
    result=_run_command(
        client,vercel_token,session_id,
        "python",["-c",script],
        operation="hash captured live artifacts",
    )
    if result["exit_code"]!=0:
        raise LiveAcceptanceError("Artifact hashing failed")
    artifact_payload=json.loads(
        _read_file(client,vercel_token,session_id,RESULT_PATH).decode("utf-8")
    )

    budget_status=str(audit.get("budget_status") or "NOT_EVALUATED")
    blocked=bool(audit.get("blocked"))
    artifacts=artifact_payload.get("artifacts") or []
    passed=(
        openhands_exit_code==0
        and test_exit_code==0
        and len(artifacts)>=1
        and budget_status=="WITHIN_BUDGET"
        and not blocked
        and int(audit.get("request_count") or 0)>0
        and int(audit.get("request_count") or 0)<=MAX_REQUESTS
        and int(audit.get("total_tokens") or 0)<=MAX_TOTAL_TOKENS
        and float(audit.get("estimated_cost_usd") or 0)<=MAX_COST_USD
    )

    return {
        "acceptance_status":"PASSED" if passed else "FAILED",
        "provider":"AIHUBMIX",
        "model":LIVE_MODEL,
        "gateway_mode":"PROXY",
        "live_model_verified":passed,
        "budget_status":budget_status,
        "gateway_request_count":int(audit.get("request_count") or 0),
        "prompt_tokens":int(audit.get("prompt_tokens") or 0),
        "completion_tokens":int(audit.get("completion_tokens") or 0),
        "total_tokens":int(audit.get("total_tokens") or 0),
        "estimated_cost_usd":float(audit.get("estimated_cost_usd") or 0),
        "blocked_reason":audit.get("blocked_reason") or None,
        "openhands_exit_code":openhands_exit_code,
        "test_exit_code":test_exit_code,
        "tests_passed":test_exit_code==0,
        "artifact_count":len(artifacts),
        "artifacts":artifacts,
        "source_tree_sha256":artifact_payload.get("source_tree_sha256"),
        "sandbox_network_policy":"AIHUBMIX_ONLY_THEN_DENY_ALL",
        "provider_key_in_agent_environment":False,
        "external_side_effects":"DENY",
        "deployment_enabled":False,
        "git_push_enabled":False,
        "release_approved":False,
    }


def run_vercel_live_acceptance(trigger_token:str)->dict[str,Any]:
    _verify_trigger(trigger_token)
    vercel_token,aihubmix_key=_require_controller_secrets()

    name="asset-live-"+hashlib.sha256(
        f"{time.time_ns()}:{trigger_token}".encode("utf-8")
    ).hexdigest()[:12]
    session_id=None
    sandbox_name=None
    started=time.time()

    with httpx.Client(timeout=60,follow_redirects=False) as client:
        try:
            session_id,sandbox_name=_create_sandbox(
                client,vercel_token,name
            )
            _upload_files(client,vercel_token,session_id)
            _install_openhands(client,vercel_token,session_id)
            _prepare_workspace(client,vercel_token,session_id)

            broker_nonce=secrets.token_urlsafe(32)
            local_token=secrets.token_urlsafe(32)
            _lock_network_to_aihubmix(
                client,
                vercel_token,
                aihubmix_key,
                session_id,
                broker_nonce,
            )
            _start_gateway(
                client,
                vercel_token,
                session_id,
                local_token,
                broker_nonce,
            )
            openhands_code=_run_openhands(
                client,
                vercel_token,
                session_id,
                local_token,
            )

            _deny_all_network(client,vercel_token,session_id)
            test_code=_run_independent_tests(
                client,vercel_token,session_id
            )
            result=_build_result(
                client,
                vercel_token,
                session_id,
                openhands_code,
                test_code,
            )
            result["sandbox_session_id"]=session_id
            result["sandbox_name"]=sandbox_name
            result["duration_seconds"]=round(time.time()-started,3)
            result["openhands_log_tail"]=_read_text_safe(
                client,vercel_token,session_id,
                f"{WORKDIR}/openhands.log",
                limit=12_000,
            )
            result["test_log_tail"]=_read_text_safe(
                client,vercel_token,session_id,
                f"{WORKDIR}/tests.log",
                limit=8_000,
            )
            return result
        finally:
            _stop_session(client,vercel_token,session_id)
            _delete_sandbox(client,vercel_token,sandbox_name)
