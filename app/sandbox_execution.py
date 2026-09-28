import hashlib
import json
import mimetypes
import os
import subprocess
import uuid
from pathlib import Path

from sqlalchemy import text

from app.build_proposals import _current_source_state, _is_build_ready, _proposal_payload
from app.config import settings
from app.db import engine
from app.sandbox_policy import validate_execution_policy, workspace_path_for

MAX_LOG_CHARS = 200_000
MAX_ARTIFACT_FILES = 100
MAX_ARTIFACT_BYTES = 10 * 1024 * 1024

def _clip(value: str) -> str:
    return (value or "")[-MAX_LOG_CHARS:]

def _docker_base(workspace: Path, image: str) -> list[str]:
    uid=os.getuid() if hasattr(os,"getuid") else 1000
    gid=os.getgid() if hasattr(os,"getgid") else 1000
    return [
        "docker","run","--rm",
        "--network","none",
        "--read-only",
        "--cap-drop","ALL",
        "--security-opt","no-new-privileges",
        "--pids-limit","128",
        "--memory","512m",
        "--cpus","1.0",
        "--user",f"{uid}:{gid}",
        "--tmpfs","/tmp:rw,nosuid,nodev,noexec,size=64m",
        "--mount",f"type=bind,src={workspace},dst=/workspace",
        "--workdir","/workspace",
        image,
    ]

def _run_container(workspace: Path, image: str, command: list[str]):
    completed=subprocess.run(
        _docker_base(workspace,image)+command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(30,min(settings.sandbox_timeout_seconds,1800)),
        check=False,
    )
    return completed.returncode,_clip(completed.stdout),_clip(completed.stderr)

def _write_acceptance_fixture(workspace: Path) -> None:
    builder=workspace/"acceptance_builder.py"
    builder.write_text(
        """from pathlib import Path
artifact=Path('/workspace/artifact')
tests=Path('/workspace/tests')
artifact.mkdir(parents=True,exist_ok=True)
tests.mkdir(parents=True,exist_ok=True)
(artifact/'main.py').write_text(
    "def quote_price(monthly_usd: float) -> float:\\n"
    "    if monthly_usd < 0:\\n"
    "        raise ValueError('monthly_usd must be non-negative')\\n"
    "    return round(monthly_usd * 12, 2)\\n",
    encoding='utf-8',
)
(artifact/'README.md').write_text(
    '# Sandbox Acceptance Artifact\\n\\nGenerated inside an isolated, network-disabled sandbox.\\n',
    encoding='utf-8',
)
(tests/'test_artifact.py').write_text(
    "import importlib.util\\n"
    "import pathlib\\n"
    "import unittest\\n"
    "path=pathlib.Path('/workspace/artifact/main.py')\\n"
    "spec=importlib.util.spec_from_file_location('artifact_main', path)\\n"
    "mod=importlib.util.module_from_spec(spec)\\n"
    "spec.loader.exec_module(mod)\\n"
    "class ArtifactTest(unittest.TestCase):\\n"
    "    def test_quote_price(self):\\n"
    "        self.assertEqual(mod.quote_price(25), 300)\\n"
    "    def test_negative_rejected(self):\\n"
    "        with self.assertRaises(ValueError):\\n"
    "            mod.quote_price(-1)\\n"
    "if __name__ == '__main__':\\n"
    "    unittest.main()\\n",
    encoding='utf-8',
)
print('acceptance artifact created')
""",
        encoding="utf-8",
    )

def _capture_artifacts(run_id, workspace: Path) -> int:
    root=(workspace/"artifact").resolve()
    if not root.exists() or not root.is_dir():
        return 0
    files=[]
    total=0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("Symlink artifacts are forbidden")
        if not path.is_file():
            continue
        resolved=path.resolve()
        if root not in resolved.parents:
            raise RuntimeError("Artifact escaped workspace")
        size=resolved.stat().st_size
        total+=size
        if len(files)>=MAX_ARTIFACT_FILES or total>MAX_ARTIFACT_BYTES:
            raise RuntimeError("Artifact capture limit exceeded")
        data=resolved.read_bytes()
        files.append({
            "relative_path":resolved.relative_to(root).as_posix(),
            "sha256":hashlib.sha256(data).hexdigest(),
            "byte_size":size,
            "media_type":mimetypes.guess_type(resolved.name)[0] or "application/octet-stream",
        })
    with engine.begin() as db:
        for item in files:
            db.execute(text("""
              INSERT INTO sandbox_artifacts(run_id,relative_path,sha256,byte_size,media_type)
              VALUES(CAST(:run_id AS uuid),:path,:sha,:size,:media)
              ON CONFLICT(run_id,relative_path) DO UPDATE SET
                sha256=excluded.sha256,
                byte_size=excluded.byte_size,
                media_type=excluded.media_type,
                captured_at=now()
            """),{
                "run_id":run_id,
                "path":item["relative_path"],
                "sha":item["sha256"],
                "size":item["byte_size"],
                "media":item["media_type"],
            })
    return len(files)

def create_sandbox_request(
    proposal_id,
    *,
    requested_by: str = "human",
    executor_kind: str = "OPENHANDS",
    sandbox_image: str | None = None,
):
    executor=executor_kind.upper().strip()
    default_image=(
        settings.openhands_cli_image if executor=="OPENHANDS"
        else settings.sandbox_image
    )
    image=sandbox_image or default_image
    policy=validate_execution_policy(executor_kind=executor,sandbox_image=image)
    workspace_id=uuid.uuid4()

    with engine.begin() as db:
        proposal=db.execute(text("""
          SELECT id,opportunity_id,revision,proposal_status,source_fingerprint,
                 requires_human_approval,execution_enabled
          FROM build_proposals
          WHERE id=CAST(:id AS uuid)
          FOR UPDATE
        """),{"id":proposal_id}).mappings().one_or_none()
        if proposal is None:
            raise LookupError("Build proposal not found")
        if proposal["proposal_status"]!="APPROVED":
            raise RuntimeError("Only APPROVED proposals can request sandbox execution")
        if not proposal["requires_human_approval"]:
            raise RuntimeError("Human approval invariant failed")
        if proposal["execution_enabled"]:
            raise RuntimeError("Production execution flag must remain disabled")

        opportunity,report,validation=_current_source_state(db,proposal["opportunity_id"])
        if not _is_build_ready(opportunity,report,validation):
            raise RuntimeError("BUILD_READY is no longer current")
        current=_proposal_payload(dict(opportunity),dict(report),dict(validation))
        if current["source_fingerprint"]!=proposal["source_fingerprint"]:
            raise RuntimeError("Proposal source state changed; sandbox execution denied")

        request_id=db.execute(text("""
          INSERT INTO sandbox_build_requests(
            proposal_id,proposal_revision,source_fingerprint,executor_kind,
            request_status,policy_snapshot,workspace_id,sandbox_image,
            network_policy,workspace_policy,external_side_effects,
            requested_by,policy_checked_at)
          VALUES(
            :proposal_id,:revision,:fingerprint,:executor,
            'POLICY_PASSED',CAST(:policy AS jsonb),:workspace_id,:image,
            :network_policy,'ISOLATED_RW','DENY',:requested_by,now())
          RETURNING id
        """),{
            "proposal_id":proposal["id"],
            "revision":proposal["revision"],
            "fingerprint":proposal["source_fingerprint"],
            "executor":executor,
            "policy":json.dumps(policy,ensure_ascii=False),
            "workspace_id":workspace_id,
            "image":image,
            "network_policy":policy["network"],
            "requested_by":(requested_by or "human")[:200],
        }).scalar_one()

    return {
        "request_id":str(request_id),
        "proposal_id":str(proposal_id),
        "workspace_id":str(workspace_id),
        "request_status":"POLICY_PASSED",
        "executor_kind":executor,
        "policy":policy,
    }

def execute_sandbox_request(request_id):
    dispatch_openhands=False
    with engine.begin() as db:
        request=db.execute(text("""
          SELECT id,proposal_id,proposal_revision,source_fingerprint,
                 executor_kind,request_status,workspace_id,sandbox_image
          FROM sandbox_build_requests
          WHERE id=CAST(:id AS uuid)
          FOR UPDATE
        """),{"id":request_id}).mappings().one_or_none()
        if request is None:
            raise LookupError("Sandbox build request not found")
        if request["request_status"]!="POLICY_PASSED":
            raise RuntimeError("Sandbox request is not POLICY_PASSED")
        if request["executor_kind"]=="OPENHANDS":
            dispatch_openhands=True
        else:
            workspace=workspace_path_for(request["workspace_id"])
            if workspace.exists():
                raise RuntimeError("Workspace already exists")
            workspace.mkdir(parents=False,exist_ok=False)
            workspace.chmod(0o700)
            _write_acceptance_fixture(workspace)

            run_id=db.execute(text("""
              INSERT INTO sandbox_runs(
                request_id,workspace_id,workspace_path,executor_kind,
                container_image,container_network)
              VALUES(:request_id,:workspace_id,:workspace_path,:executor,:image,'none')
              RETURNING id
            """),{
                "request_id":request["id"],
                "workspace_id":request["workspace_id"],
                "workspace_path":str(workspace),
                "executor":request["executor_kind"],
                "image":request["sandbox_image"],
            }).scalar_one()
            db.execute(text("""
              UPDATE sandbox_build_requests
              SET request_status='RUNNING',started_at=now()
              WHERE id=:id
            """),{"id":request["id"]})

    if dispatch_openhands:
        from app.openhands_adapter import execute_openhands_request
        return execute_openhands_request(request["id"])

    build_code=1
    build_out=""
    build_err=""
    test_code=1
    test_out=""
    test_err=""
    artifact_count=0
    try:
        build_code,build_out,build_err=_run_container(
            workspace,
            request["sandbox_image"],
            ["python","/workspace/acceptance_builder.py"],
        )
        if build_code==0:
            test_code,test_out,test_err=_run_container(
                workspace,
                request["sandbox_image"],
                ["python","-m","unittest","discover","-s","tests","-q"],
            )
        else:
            test_err="Tests not run because sandbox build failed"

        if build_code==0:
            artifact_count=_capture_artifacts(run_id,workspace)

        passed=build_code==0 and test_code==0 and artifact_count>0
        with engine.begin() as db:
            db.execute(text("""
              UPDATE sandbox_runs
              SET exit_code=:exit_code,stdout=:stdout,stderr=:stderr,finished_at=now()
              WHERE id=:id
            """),{
                "exit_code":build_code,
                "stdout":build_out,
                "stderr":build_err,
                "id":run_id,
            })
            db.execute(text("""
              INSERT INTO sandbox_test_results(
                run_id,test_command,exit_code,stdout,stderr,passed)
              VALUES(
                :run_id,'python -m unittest discover -s tests -q',
                :exit_code,:stdout,:stderr,:passed)
            """),{
                "run_id":run_id,
                "exit_code":test_code,
                "stdout":test_out,
                "stderr":test_err,
                "passed":passed,
            })
            db.execute(text("""
              UPDATE sandbox_build_requests
              SET request_status=:status,
                  error=:error,
                  finished_at=now()
              WHERE id=:id
            """),{
                "status":"ARTIFACT_READY" if passed else "FAILED",
                "error":None if passed else _clip(build_err+"\n"+test_err),
                "id":request["id"],
            })
        return {
            "request_id":str(request["id"]),
            "run_id":str(run_id),
            "request_status":"ARTIFACT_READY" if passed else "FAILED",
            "artifact_count":artifact_count,
            "test_passed":passed,
            "container_network":"none",
            "workspace_id":str(request["workspace_id"]),
        }
    except subprocess.TimeoutExpired as exc:
        with engine.begin() as db:
            db.execute(text("""
              UPDATE sandbox_runs
              SET exit_code=124,stderr=:stderr,finished_at=now()
              WHERE id=:id
            """),{"stderr":_clip(str(exc)),"id":run_id})
            db.execute(text("""
              UPDATE sandbox_build_requests
              SET request_status='FAILED',error=:error,finished_at=now()
              WHERE id=:id
            """),{"error":_clip(str(exc)),"id":request["id"]})
        raise RuntimeError("Sandbox execution timed out") from exc
    except Exception as exc:
        with engine.begin() as db:
            db.execute(text("""
              UPDATE sandbox_runs
              SET exit_code=1,stderr=:stderr,finished_at=now()
              WHERE id=:id
            """),{"stderr":_clip(str(exc)),"id":run_id})
            db.execute(text("""
              UPDATE sandbox_build_requests
              SET request_status='FAILED',error=:error,finished_at=now()
              WHERE id=:id
            """),{"error":_clip(str(exc)),"id":request["id"]})
        raise
