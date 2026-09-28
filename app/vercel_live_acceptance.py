import hashlib
import json
import os
import secrets
import shlex
import time
from pathlib import Path
from typing import Any

VERCEL_PROJECT_ID="prj_orLCRCIm7aVfImH8ihB3gponFOEl"
VERCEL_TEAM_ID="team_JO3GTfLCviMWb2pAvSClH0iK"

AIHUBMIX_BASE_URL="https://aihubmix.com/v1"
AIHUBMIX_DOMAIN="aihubmix.com"
LIVE_MODEL="gpt-5.6-luna"

# Raw trigger is never stored in Git. Replacing this digest invalidates prior triggers.
TRIGGER_TOKEN_SHA256="c3ff2167789520f2046404939f27c558e1a849e3fe099693ee8d2e7561dd0959"

MAX_REQUESTS=4
MAX_PROMPT_TOKENS_PER_REQUEST=12_000
MAX_COMPLETION_TOKENS_PER_REQUEST=4_000
MAX_TOTAL_TOKENS=40_000
MAX_COST_PER_REQUEST_USD=0.01
MAX_COST_USD=0.04

# Conservative accounting. Input is intentionally above the current standard
# AIHubMix input price so the local budget gate remains on the safe side.
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


def _sdk_types():
    try:
        from vercel.sandbox import (
            NetworkPolicyCustom,
            NetworkPolicyRule,
            NetworkTransformer,
            Sandbox,
        )
    except Exception as exc:
        raise LiveAcceptanceError(f"Vercel Sandbox SDK import failed: {exc}") from exc
    return NetworkPolicyCustom, NetworkPolicyRule, NetworkTransformer, Sandbox


def _setup_policy()->Any:
    NetworkPolicyCustom, _, _, _ = _sdk_types()
    return NetworkPolicyCustom(allow=list(SETUP_DOMAINS))


def _provider_policy(key:str)->Any:
    NetworkPolicyCustom, NetworkPolicyRule, NetworkTransformer, _ = _sdk_types()
    return NetworkPolicyCustom(
        allow={
            AIHUBMIX_DOMAIN:[
                NetworkPolicyRule(
                    transform=[
                        NetworkTransformer(
                            headers={"Authorization":f"Bearer {key}"}
                        )
                    ]
                )
            ]
        }
    )


def _provider_policy_confirmed(policy:Any)->bool:
    NetworkPolicyCustom, _, _, _ = _sdk_types()
    if not isinstance(policy,NetworkPolicyCustom):
        return False
    if not isinstance(policy.allow,dict):
        return False
    rules=policy.allow.get(AIHUBMIX_DOMAIN) or []
    for rule in rules:
        for transform in rule.transform or []:
            headers=transform.headers or {}
            if any(name.lower()=="authorization" for name in headers):
                return True
    return False


def _write_acceptance_files(sandbox:Any)->None:
    gateway=(Path(__file__).resolve().parent/"openhands_gateway.py").read_bytes()
    files=[
        {
            "path":GATEWAY_PATH,
            "content":gateway,
            "mode":0o644,
        },
        {
            "path":TASK_PATH,
            "content":TASK_TEXT.encode("utf-8"),
            "mode":0o644,
        },
    ]
    sandbox.write_files(files)


def _run_script(
    sandbox:Any,
    script:str,
    *,
    operation:str,
    sudo:bool=True,
    env:dict[str,str]|None=None,
    timeout_seconds:int=180,
)->int:
    wrapped=f"timeout {int(timeout_seconds)}s sh -lc {shlex.quote(script)}"
    command=sandbox.run_command(
        "sh",
        ["-lc",wrapped],
        cwd=WORKDIR,
        env=env or {},
        sudo=sudo,
    )
    if command.exit_code!=0:
        stdout=command.stdout()[-6000:]
        stderr=command.stderr()[-6000:]
        raise LiveAcceptanceError(
            f"{operation} failed with exit code {command.exit_code}. "
            f"stdout={stdout!r} stderr={stderr!r}"
        )
    return command.exit_code


def _read_file(sandbox:Any,path:str)->bytes:
    data=sandbox.read_file(path)
    if data is None:
        raise LiveAcceptanceError(f"Sandbox file is missing: {path}")
    return bytes(data)


def _read_text_safe(sandbox:Any,path:str,limit:int=20_000)->str:
    try:
        return _read_file(sandbox,path).decode("utf-8",errors="replace")[-limit:]
    except Exception as exc:
        return f"<unavailable: {exc}>"


def _read_audit_safe(sandbox:Any | None)->dict[str,Any]:
    if sandbox is None:
        return {}
    try:
        raw=_read_file(sandbox,AUDIT_PATH)
        payload=json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload,dict) else {}
    except Exception:
        return {}


def _install_openhands(sandbox:Any)->None:
    script=r"""
set -eu
rm -rf /opt/openhands
if ! command -v uv >/dev/null 2>&1; then
  python -m pip install --no-cache-dir uv > /home/vercel-sandbox/install.log 2>&1
else
  : > /home/vercel-sandbox/install.log
fi
uv python install 3.12 >> /home/vercel-sandbox/install.log 2>&1
uv venv /opt/openhands --python 3.12 >> /home/vercel-sandbox/install.log 2>&1
uv pip install --python /opt/openhands/bin/python 'openhands==1.16.0' >> /home/vercel-sandbox/install.log 2>&1
/opt/openhands/bin/openhands --version >> /home/vercel-sandbox/install.log 2>&1
"""
    try:
        _run_script(
            sandbox,
            script,
            operation="install pinned OpenHands CLI",
            timeout_seconds=175,
        )
    except LiveAcceptanceError as exc:
        log=_read_text_safe(sandbox,f"{WORKDIR}/install.log")
        raise LiveAcceptanceError(f"{exc}\ninstall_tail={log}") from exc


def _prepare_agent_and_firewall(sandbox:Any)->None:
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
    _run_script(
        sandbox,
        script,
        operation="establish UID-level OpenHands network isolation",
        timeout_seconds=70,
    )


def _tighten_to_provider(sandbox:Any,key:str)->None:
    updated=sandbox.update_network_policy(_provider_policy(key))
    if not _provider_policy_confirmed(updated):
        raise LiveAcceptanceError(
            "Vercel did not confirm provider credential brokering; model call denied"
        )


def _start_gateway(sandbox:Any,local_token:str)->None:
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
    try:
        _run_script(
            sandbox,
            script,
            operation="start controlled LLM gateway",
            env=env,
            timeout_seconds=35,
        )
    except LiveAcceptanceError as exc:
        log=_read_text_safe(sandbox,f"{WORKDIR}/gateway.log")
        raise LiveAcceptanceError(f"{exc}\ngateway_tail={log}") from exc


def _run_openhands(sandbox:Any,local_token:str)->int:
    script=r"""
set -eu
mkdir -p /home/vercel-sandbox/agent-home
chown -R openhands-agent:openhands-agent /home/vercel-sandbox/agent-home /home/vercel-sandbox/workspace
chmod 0700 /home/vercel-sandbox/agent-home

sudo -u openhands-agent env   HOME=/home/vercel-sandbox/agent-home   OPENHANDS_WORK_DIR=/home/vercel-sandbox/workspace   OPENHANDS_PERSISTENCE_DIR=/home/vercel-sandbox/agent-home/state   OPENHANDS_CONVERSATIONS_DIR=/home/vercel-sandbox/agent-home/conversations   RUNTIME=process   OPENHANDS_SUPPRESS_BANNER=1   PYTHONUTF8=1   PYTHONIOENCODING=utf-8   LANG=C.UTF-8   LC_ALL=C.UTF-8   LLM_API_KEY="$LOCAL_GATEWAY_TOKEN"   LLM_MODEL="$LIVE_MODEL"   LLM_BASE_URL=http://127.0.0.1:9999/v1   /opt/openhands/bin/openhands --headless --json --override-with-envs     -f /home/vercel-sandbox/workspace/openhands-task.md     > /home/vercel-sandbox/openhands.log 2>&1
"""
    command=sandbox.run_command(
        "sh",
        ["-lc",f"timeout 150s sh -lc {shlex.quote(script)}"],
        cwd=WORKDIR,
        env={
            "LOCAL_GATEWAY_TOKEN":local_token,
            "LIVE_MODEL":LIVE_MODEL,
        },
        sudo=True,
    )
    return int(command.exit_code)


def _stop_gateway(sandbox:Any)->None:
    script=r"""
set -eu
if [ -f /home/vercel-sandbox/gateway.pid ]; then
  kill "$(cat /home/vercel-sandbox/gateway.pid)" 2>/dev/null || true
fi
"""
    try:
        _run_script(
            sandbox,
            script,
            operation="stop controlled LLM gateway",
            timeout_seconds=10,
        )
    except Exception:
        pass


def _run_independent_tests(sandbox:Any)->int:
    script=r"""
set -eu
cd /home/vercel-sandbox/workspace
python -m unittest discover -s tests -q > /home/vercel-sandbox/tests.log 2>&1
"""
    command=sandbox.run_command(
        "sh",
        ["-lc",f"timeout 45s sh -lc {shlex.quote(script)}"],
        cwd=WORKDIR,
        sudo=False,
    )
    return int(command.exit_code)


def _hash_artifacts(sandbox:Any)->dict[str,Any]:
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
    _run_script(
        sandbox,
        script,
        operation="hash captured live artifacts",
        sudo=False,
        timeout_seconds=20,
    )
    return json.loads(_read_file(sandbox,RESULT_PATH).decode("utf-8"))


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


def _failure_result(
    *,
    phase:str,
    error:Exception,
    sandbox:Any | None,
    started:float,
)->dict[str,Any]:
    audit=_read_audit_safe(sandbox)
    result={
        "acceptance_status":"FAILED",
        "phase":phase,
        "error":str(error)[:8000],
        "provider":"AIHUBMIX",
        "model":LIVE_MODEL,
        "gateway_mode":"PROXY",
        "live_model_verified":False,
        **_audit_fields(audit),
        "model_request_observed":int(audit.get("request_count") or 0)>0,
        "provider_key_in_sandbox_environment":False,
        "provider_key_in_agent_environment":False,
        "external_side_effects":"DENY",
        "deployment_enabled":False,
        "git_push_enabled":False,
        "release_approved":False,
        "duration_seconds":round(time.time()-started,3),
    }
    if sandbox is not None:
        result["sandbox_session_id"]=sandbox.sandbox_id
        result["openhands_log_tail"]=_read_text_safe(
            sandbox,f"{WORKDIR}/openhands.log",limit=8000
        )
        result["gateway_log_tail"]=_read_text_safe(
            sandbox,f"{WORKDIR}/gateway.log",limit=5000
        )
    return result


def run_vercel_live_acceptance(
    trigger_token:str,
    *,
    vercel_oidc_token:str | None=None,
)->dict[str,Any]:
    _verify_trigger(trigger_token)
    provider_key=_provider_key()
    started=time.time()
    sandbox:Any | None=None
    phase="resolve_vercel_identity"

    try:
        oidc=(vercel_oidc_token or "").strip()
        if not oidc:
            raise LiveAcceptanceError(
                "x-vercel-oidc-token request header is unavailable"
            )
        _, _, _, Sandbox = _sdk_types()
        phase="create_sandbox"
        sandbox=Sandbox.create(
            runtime="python3.13",
            timeout=290_000,
            resources={"vcpus":2,"memory":4096},
            token=oidc,
            project_id=VERCEL_PROJECT_ID,
            team_id=VERCEL_TEAM_ID,
            network_policy=_setup_policy(),
        )

        phase="write_acceptance_files"
        _write_acceptance_files(sandbox)

        phase="install_openhands"
        _install_openhands(sandbox)

        phase="isolate_agent_network"
        _prepare_agent_and_firewall(sandbox)

        phase="credential_brokering"
        _tighten_to_provider(sandbox,provider_key)

        phase="start_gateway"
        local_token=secrets.token_urlsafe(32)
        _start_gateway(sandbox,local_token)

        phase="run_openhands"
        openhands_exit_code=_run_openhands(sandbox,local_token)

        phase="stop_gateway"
        _stop_gateway(sandbox)

        phase="deny_all_network"
        sandbox.update_network_policy("deny-all")

        phase="independent_tests"
        test_exit_code=_run_independent_tests(sandbox)

        phase="collect_evidence"
        audit=_read_audit_safe(sandbox)
        artifacts=_hash_artifacts(sandbox)
        audit_fields=_audit_fields(audit)
        artifact_list=artifacts.get("artifacts") or []

        passed=(
            openhands_exit_code==0
            and test_exit_code==0
            and len(artifact_list)>=1
            and audit_fields["budget_status"]=="WITHIN_BUDGET"
            and not bool(audit.get("blocked"))
            and 0<audit_fields["gateway_request_count"]<=MAX_REQUESTS
            and audit_fields["total_tokens"]<=MAX_TOTAL_TOKENS
            and audit_fields["estimated_cost_usd"]<=MAX_COST_USD
        )

        return {
            "acceptance_status":"PASSED" if passed else "FAILED",
            "phase":"complete",
            "provider":"AIHUBMIX",
            "model":LIVE_MODEL,
            "gateway_mode":"PROXY",
            "live_model_verified":passed,
            **audit_fields,
            "model_request_observed":audit_fields["gateway_request_count"]>0,
            "openhands_exit_code":openhands_exit_code,
            "test_exit_code":test_exit_code,
            "tests_passed":test_exit_code==0,
            "artifact_count":len(artifact_list),
            "artifacts":artifact_list,
            "source_tree_sha256":artifacts.get("source_tree_sha256"),
            "sandbox_session_id":sandbox.sandbox_id,
            "sandbox_network_policy":"AIHUBMIX_ROOT_GATEWAY_ONLY_THEN_DENY_ALL",
            "agent_uid_external_network":"DENY",
            "provider_key_in_sandbox_environment":False,
            "provider_key_in_agent_environment":False,
            "external_side_effects":"DENY",
            "deployment_enabled":False,
            "git_push_enabled":False,
            "release_approved":False,
            "duration_seconds":round(time.time()-started,3),
            "openhands_log_tail":_read_text_safe(
                sandbox,f"{WORKDIR}/openhands.log",limit=10_000
            ),
            "test_log_tail":_read_text_safe(
                sandbox,f"{WORKDIR}/tests.log",limit=6000
            ),
        }
    except Exception as exc:
        return _failure_result(
            phase=phase,
            error=exc,
            sandbox=sandbox,
            started=started,
        )
    finally:
        if sandbox is not None:
            try:
                _stop_gateway(sandbox)
            except Exception:
                pass
            try:
                sandbox.update_network_policy("deny-all")
            except Exception:
                pass
            try:
                sandbox.stop()
            except Exception:
                pass
