import hashlib
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

from sqlalchemy import text

from app.build_proposals import _current_source_state, _is_build_ready, _proposal_payload
from app.config import settings
from app.db import engine
from app.sandbox_execution import _capture_artifacts, _clip, _run_container
from app.sandbox_policy import workspace_path_for

def _run(args:list[str],*,timeout:int=90,check:bool=True):
    completed=subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if check and completed.returncode!=0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(args[:4])}: "
            f"{_clip(completed.stderr or completed.stdout)}"
        )
    return completed

def _docker_name(prefix:str,request_id)->str:
    compact=str(request_id).replace("-","")[:12]
    return f"asset-factory-{prefix}-{compact}"

def _write_task(workspace:Path,proposal:dict)->Path:
    task=workspace/"openhands-task.md"
    success=proposal.get("success_criteria") or []
    constraints=proposal.get("constraints") or []
    scope=proposal.get("scope") or {}
    stack=proposal.get("proposed_stack") or []
    lines=[
        "# Approved Sandbox Build Task",
        "",
        proposal["objective"],
        "",
        "## Scope",
        json.dumps(scope,ensure_ascii=False,indent=2),
        "",
        "## Proposed stack",
        json.dumps(stack,ensure_ascii=False,indent=2),
        "",
        "## Required outputs",
        "- Write the prototype only under /workspace/artifact.",
        "- Write automated tests only under /workspace/tests.",
        "- Do not modify files outside /workspace.",
        "- Do not initialize, commit, push, deploy, publish, email, register accounts, or spend money.",
        "- Do not attempt network discovery or access any service except the configured LLM gateway.",
        "- Keep all text files UTF-8.",
        "",
        "## Success criteria",
        *[f"- {item}" for item in success],
        "",
        "## Constraints",
        *[f"- {item}" for item in constraints],
        "",
        "Finish after the prototype files and tests are created.",
    ]
    task.write_text("\n".join(lines)+"\n",encoding="utf-8")
    return task

def _create_internal_network(name:str)->None:
    _run(["docker","network","create","--internal",name],timeout=30)

def _start_gateway(network:str,gateway_name:str,local_token:str)->None:
    mode=settings.openhands_gateway_mode.strip().upper()
    script=Path(__file__).with_name("openhands_gateway.py").resolve()
    common=[
        "docker","run","-d","--rm",
        "--name",gateway_name,
        "--read-only",
        "--cap-drop","ALL",
        "--security-opt","no-new-privileges",
        "--pids-limit","64",
        "--memory","128m",
        "--cpus","0.5",
        "--tmpfs","/tmp:rw,nosuid,nodev,noexec,size=32m",
        "-e",f"GATEWAY_MODE={mode}",
        "-e",f"LOCAL_TOKEN={local_token}",
        "-e","PYTHONUTF8=1",
        "-e","PYTHONIOENCODING=utf-8",
        "--mount",f"type=bind,src={script},dst=/gateway.py,readonly",
    ]
    if mode=="MOCK":
        args=common+[
            "--network",network,
            "--network-alias","llm-gateway",
            settings.openhands_gateway_image,
            "python","/gateway.py",
        ]
        _run(args,timeout=60)
    else:
        args=common+[
            "--network","bridge",
            "-e",f"UPSTREAM_BASE_URL={settings.openhands_llm_upstream_url.strip()}",
            "-e",f"UPSTREAM_API_KEY={settings.openhands_llm_api_key}",
            settings.openhands_gateway_image,
            "python","/gateway.py",
        ]
        _run(args,timeout=60)
        _run(
            ["docker","network","connect","--alias","llm-gateway",network,gateway_name],
            timeout=30,
        )

def _wait_gateway(gateway_name:str)->None:
    last=""
    for _ in range(30):
        result=_run([
            "docker","exec",gateway_name,"python","-c",
            "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9999/health',timeout=2).read().decode())",
        ],timeout=10,check=False)
        if result.returncode==0:
            return
        last=result.stderr or result.stdout
        time.sleep(0.5)
    raise RuntimeError("OpenHands LLM gateway did not become healthy: "+_clip(last))

def _openhands_command(workspace:Path,network:str,local_token:str)->list[str]:
    uid=os.getuid() if hasattr(os,"getuid") else 1000
    gid=os.getgid() if hasattr(os,"getgid") else 1000
    return [
        "docker","run","--rm",
        "--network",network,
        "--read-only",
        "--cap-drop","ALL",
        "--security-opt","no-new-privileges",
        "--pids-limit","256",
        "--memory","1024m",
        "--cpus","1.0",
        "--user",f"{uid}:{gid}",
        "--tmpfs","/tmp:rw,nosuid,nodev,size=256m",
        "--mount",f"type=bind,src={workspace},dst=/workspace",
        "--workdir","/workspace",
        "-e","HOME=/tmp/openhands-home",
        "-e","OPENHANDS_WORK_DIR=/workspace",
        "-e","OPENHANDS_PERSISTENCE_DIR=/tmp/openhands-home/state",
        "-e","OPENHANDS_CONVERSATIONS_DIR=/tmp/openhands-home/conversations",
        "-e","RUNTIME=process",
        "-e","OPENHANDS_SUPPRESS_BANNER=1",
        "-e","PYTHONUTF8=1",
        "-e","PYTHONIOENCODING=utf-8",
        "-e","LANG=C.UTF-8",
        "-e","LC_ALL=C.UTF-8",
        "-e",f"LLM_API_KEY={local_token}",
        "-e",f"LLM_MODEL={settings.openhands_model}",
        "-e","LLM_BASE_URL=http://llm-gateway:9999/v1",
        settings.openhands_cli_image,
        "--headless","--json","--override-with-envs",
        "-f","/workspace/openhands-task.md",
    ]

def _cleanup_container(name:str)->None:
    _run(["docker","rm","-f",name],timeout=20,check=False)

def _cleanup_network(name:str)->None:
    _run(["docker","network","rm",name],timeout=20,check=False)

def execute_openhands_request(request_id):
    with engine.begin() as db:
        request=db.execute(text("""
          SELECT sbr.id,sbr.proposal_id,sbr.proposal_revision,sbr.source_fingerprint,
                 sbr.executor_kind,sbr.request_status,sbr.workspace_id,sbr.sandbox_image,
                 bp.opportunity_id,bp.proposal_status,bp.execution_enabled
          FROM sandbox_build_requests sbr
          JOIN build_proposals bp ON bp.id=sbr.proposal_id
          WHERE sbr.id=CAST(:id AS uuid)
          FOR UPDATE OF sbr,bp
        """),{"id":request_id}).mappings().one_or_none()
        if request is None:
            raise LookupError("Sandbox build request not found")
        if request["executor_kind"]!="OPENHANDS":
            raise RuntimeError("Request is not an OpenHands execution")
        if request["request_status"]!="POLICY_PASSED":
            raise RuntimeError("OpenHands request is not POLICY_PASSED")
        if request["proposal_status"]!="APPROVED" or request["execution_enabled"]:
            raise RuntimeError("Approved non-production proposal invariant failed")

        opportunity,report,validation=_current_source_state(db,request["opportunity_id"])
        if not _is_build_ready(opportunity,report,validation):
            raise RuntimeError("BUILD_READY is no longer current")
        current=_proposal_payload(dict(opportunity),dict(report),dict(validation))
        if current["source_fingerprint"]!=request["source_fingerprint"]:
            raise RuntimeError("Proposal source state changed; OpenHands execution denied")

        proposal=db.execute(text("""
          SELECT title,objective,scope,success_criteria,constraints,proposed_stack
          FROM build_proposals
          WHERE id=:id
        """),{"id":request["proposal_id"]}).mappings().one()

    workspace=workspace_path_for(request["workspace_id"])
    if workspace.exists():
        raise RuntimeError("Workspace already exists")
    workspace.mkdir(parents=False,exist_ok=False)
    workspace.chmod(0o700)
    task_path=_write_task(workspace,dict(proposal))
    task_hash=hashlib.sha256(task_path.read_bytes()).hexdigest()

    network=_docker_name("oh-net",request["id"])
    gateway=_docker_name("oh-gateway",request["id"])
    local_token=secrets.token_urlsafe(32)
    run_id=None
    execution_id=None
    cli_out=""
    cli_err=""
    cli_code=1
    gateway_logs=""
    test_code=1
    test_out=""
    test_err=""
    artifact_count=0

    try:
        _create_internal_network(network)
        _start_gateway(network,gateway,local_token)
        _wait_gateway(gateway)

        with engine.begin() as db:
            run_id=db.execute(text("""
              INSERT INTO sandbox_runs(
                request_id,workspace_id,workspace_path,executor_kind,
                container_image,container_network)
              VALUES(
                :request_id,:workspace_id,:workspace_path,'OPENHANDS',
                :image,'internal-gateway')
              RETURNING id
            """),{
                "request_id":request["id"],
                "workspace_id":request["workspace_id"],
                "workspace_path":str(workspace),
                "image":settings.openhands_cli_image,
            }).scalar_one()
            execution_id=db.execute(text("""
              INSERT INTO openhands_executions(
                request_id,run_id,cli_version,model_name,inner_runtime,
                network_policy,gateway_mode,task_sha256)
              VALUES(
                :request_id,:run_id,:version,:model,'process',
                'INTERNAL_GATEWAY_ONLY',:gateway_mode,:task_sha)
              RETURNING id
            """),{
                "request_id":request["id"],
                "run_id":run_id,
                "version":settings.openhands_cli_version,
                "model":settings.openhands_model,
                "gateway_mode":settings.openhands_gateway_mode.strip().upper(),
                "task_sha":task_hash,
            }).scalar_one()
            db.execute(text("""
              UPDATE sandbox_build_requests
              SET request_status='RUNNING',started_at=now(),error=NULL
              WHERE id=:id
            """),{"id":request["id"]})

        cli_timeout=(
            min(90,max(30,settings.sandbox_timeout_seconds))
            if settings.openhands_gateway_mode.strip().upper()=="MOCK"
            else max(60,min(settings.sandbox_timeout_seconds,1800))
        )
        completed=_run(
            _openhands_command(workspace,network,local_token),
            timeout=cli_timeout,
            check=False,
        )
        cli_code=completed.returncode
        cli_out=_clip(completed.stdout)
        cli_err=_clip(completed.stderr)
        gateway_result=_run(["docker","logs",gateway],timeout=20,check=False)
        gateway_logs=_clip((gateway_result.stdout or "")+"\n"+(gateway_result.stderr or ""))

        if cli_code==0:
            test_code,test_out,test_err=_run_container(
                workspace,
                settings.sandbox_image,
                ["python","-m","unittest","discover","-s","tests","-q"],
            )
            artifact_count=_capture_artifacts(run_id,workspace)
        else:
            test_err="Tests not run because OpenHands execution failed"

        passed=cli_code==0 and test_code==0 and artifact_count>0
        status="ARTIFACT_READY" if passed else "FAILED"

        with engine.begin() as db:
            db.execute(text("""
              UPDATE sandbox_runs
              SET exit_code=:code,stdout=:stdout,stderr=:stderr,finished_at=now()
              WHERE id=:id
            """),{
                "code":cli_code,
                "stdout":cli_out,
                "stderr":cli_err,
                "id":run_id,
            })
            db.execute(text("""
              UPDATE openhands_executions
              SET exit_code=:code,trace_jsonl=:trace,finished_at=now()
              WHERE id=:id
            """),{
                "code":cli_code,
                "trace":cli_out,
                "id":execution_id,
            })
            db.execute(text("""
              INSERT INTO sandbox_test_results(
                run_id,test_command,exit_code,stdout,stderr,passed)
              VALUES(
                :run_id,'python -m unittest discover -s tests -q',
                :code,:stdout,:stderr,:passed)
            """),{
                "run_id":run_id,
                "code":test_code,
                "stdout":test_out,
                "stderr":test_err,
                "passed":passed,
            })
            db.execute(text("""
              UPDATE sandbox_build_requests
              SET request_status=:status,error=:error,finished_at=now()
              WHERE id=:id
            """),{
                "status":status,
                "error":None if passed else _clip(
                    cli_err+"\n--- gateway ---\n"+gateway_logs+"\n--- tests ---\n"+test_err
                ),
                "id":request["id"],
            })

        return {
            "request_id":str(request["id"]),
            "run_id":str(run_id),
            "openhands_execution_id":str(execution_id),
            "request_status":status,
            "executor_kind":"OPENHANDS",
            "cli_version":settings.openhands_cli_version,
            "gateway_mode":settings.openhands_gateway_mode.strip().upper(),
            "artifact_count":artifact_count,
            "test_passed":passed,
            "container_network":"internal-gateway",
            "workspace_id":str(request["workspace_id"]),
        }
    except Exception as exc:
        if gateway:
            gateway_result=_run(["docker","logs",gateway],timeout=20,check=False)
            gateway_logs=_clip((gateway_result.stdout or "")+"\n"+(gateway_result.stderr or ""))
        with engine.begin() as db:
            if run_id is not None:
                db.execute(text("""
                  UPDATE sandbox_runs
                  SET exit_code=COALESCE(exit_code,1),
                      stderr=CASE WHEN stderr='' THEN :error ELSE stderr END,
                      finished_at=COALESCE(finished_at,now())
                  WHERE id=:id
                """),{"error":_clip(str(exc)),"id":run_id})
            if execution_id is not None:
                db.execute(text("""
                  UPDATE openhands_executions
                  SET exit_code=COALESCE(exit_code,1),
                      trace_jsonl=CASE WHEN trace_jsonl='' THEN :trace ELSE trace_jsonl END,
                      finished_at=COALESCE(finished_at,now())
                  WHERE id=:id
                """),{"trace":_clip(cli_out+"\n"+str(exc)),"id":execution_id})
            db.execute(text("""
              UPDATE sandbox_build_requests
              SET request_status='FAILED',error=:error,finished_at=now()
              WHERE id=:id
            """),{
                "error":_clip(str(exc)+"\n--- gateway ---\n"+gateway_logs),
                "id":request["id"],
            })
        raise
    finally:
        _cleanup_container(gateway)
        _cleanup_network(network)
