import hashlib
import json
import re
import tomllib
from collections import Counter
from typing import Any

from sqlalchemy import text

from app.db import engine

GENERATOR_VERSION="release-review-v0.3-deterministic"
MAX_REVIEW_TEXT_BYTES=1_000_000

RISK_RULES=[
    ("HIGH","dynamic_code_eval",re.compile(r"\b(eval|exec)\s*\(")),
    ("HIGH","shell_execution",re.compile(r"\b(os\.system|subprocess\.|Popen\s*\()")),
    ("HIGH","credential_access",re.compile(r"\b(os\.environ|getenv\s*\(|API_KEY|TOKEN|SECRET)\b",re.I)),
    ("HIGH","deployment_action",re.compile(r"\b(vercel\s+deploy|kubectl\s+apply|docker\s+push|git\s+push)\b",re.I)),
    ("MEDIUM","network_client",re.compile(r"\b(requests\.|httpx\.|urllib\.request|socket\.|aiohttp\.)")),
    ("MEDIUM","filesystem_mutation",re.compile(r"\b(shutil\.rmtree|os\.remove|unlink\s*\(|write_text\s*\(|write_bytes\s*\()")),
]

def _canonical_bytes(value:Any)->bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",",":"),
    ).encode("utf-8")

def _sha(value:Any)->str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()

def _source_tree_sha(manifest:list[dict])->str:
    rows=[
        {
            "relative_path":x["relative_path"],
            "sha256":x["sha256"],
            "byte_size":int(x["byte_size"]),
            "media_type":x["media_type"],
        }
        for x in sorted(manifest,key=lambda x:x["relative_path"])
    ]
    return _sha(rows)

def _artifact_diff(current:list[dict],baseline:list[dict])->list[dict]:
    before={x["relative_path"]:x for x in baseline}
    after={x["relative_path"]:x for x in current}
    result=[]
    for path in sorted(set(before)|set(after)):
        old=before.get(path)
        new=after.get(path)
        if old is None:
            status="ADDED"
        elif new is None:
            status="REMOVED"
        elif old["sha256"]!=new["sha256"]:
            status="MODIFIED"
        else:
            status="UNCHANGED"
        result.append({
            "relative_path":path,
            "status":status,
            "before_sha256":old["sha256"] if old else None,
            "after_sha256":new["sha256"] if new else None,
            "before_size":int(old["byte_size"]) if old else None,
            "after_size":int(new["byte_size"]) if new else None,
        })
    return result

def _parse_requirement_line(line:str,source_file:str)->dict|None:
    value=line.strip()
    if not value or value.startswith("#") or value.startswith("-"):
        return None
    value=value.split(" #",1)[0].strip()
    if not value:
        return None
    match=re.match(r"^([A-Za-z0-9_.-]+)(?:\[([^\]]+)\])?\s*(.*)$",value)
    if not match:
        return {
            "ecosystem":"pypi",
            "name":value,
            "version_spec":"",
            "source_file":source_file,
        }
    name=match.group(1)
    extras=match.group(2) or ""
    spec=(match.group(3) or "").strip()
    return {
        "ecosystem":"pypi",
        "name":name,
        "extras":extras,
        "version_spec":spec,
        "source_file":source_file,
    }

def _dependencies_from_requirements(path:str,text_value:str)->list[dict]:
    result=[]
    for line in text_value.splitlines():
        item=_parse_requirement_line(line,path)
        if item:
            result.append(item)
    return result

def _dependencies_from_pyproject(path:str,data:bytes)->list[dict]:
    try:
        payload=tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError,tomllib.TOMLDecodeError):
        return []
    result=[]
    project=payload.get("project") or {}
    for value in project.get("dependencies") or []:
        item=_parse_requirement_line(str(value),path)
        if item:
            result.append(item)
    optional=project.get("optional-dependencies") or {}
    for group,values in optional.items():
        for value in values or []:
            item=_parse_requirement_line(str(value),path)
            if item:
                item["scope"]=f"optional:{group}"
                result.append(item)
    poetry=((payload.get("tool") or {}).get("poetry") or {})
    for name,value in (poetry.get("dependencies") or {}).items():
        if str(name).lower()=="python":
            continue
        if isinstance(value,dict):
            spec=str(value.get("version") or "")
        else:
            spec=str(value)
        result.append({
            "ecosystem":"pypi",
            "name":str(name),
            "version_spec":spec,
            "source_file":path,
            "scope":"poetry",
        })
    return result

def _dependencies_from_package_json(path:str,data:bytes)->list[dict]:
    try:
        payload=json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError,json.JSONDecodeError):
        return []
    result=[]
    for field,scope in (("dependencies","runtime"),("devDependencies","development"),
                        ("peerDependencies","peer"),("optionalDependencies","optional")):
        for name,version in (payload.get(field) or {}).items():
            result.append({
                "ecosystem":"npm",
                "name":str(name),
                "version_spec":str(version),
                "source_file":path,
                "scope":scope,
            })
    return result

def _dependency_inventory(contents:list[dict])->list[dict]:
    deps=[]
    for item in contents:
        path=item["relative_path"]
        data=item["content_bytes"]
        lower=path.lower()
        if re.search(r"(^|/)requirements[^/]*\.txt$",lower):
            try:
                deps.extend(_dependencies_from_requirements(path,data.decode("utf-8")))
            except UnicodeDecodeError:
                continue
        elif lower.endswith("/pyproject.toml") or lower=="pyproject.toml":
            deps.extend(_dependencies_from_pyproject(path,data))
        elif lower.endswith("/package.json") or lower=="package.json":
            deps.extend(_dependencies_from_package_json(path,data))
    unique={}
    for dep in deps:
        key=(
            dep.get("ecosystem",""),
            dep.get("name","").lower(),
            dep.get("version_spec",""),
            dep.get("scope",""),
            dep.get("source_file",""),
        )
        unique[key]=dep
    return [unique[k] for k in sorted(unique)]

def _sbom(dependencies:list[dict])->dict:
    components=[]
    for dep in dependencies:
        item={
            "type":"library",
            "name":dep["name"],
            "version":dep.get("version_spec") or "unspecified",
            "properties":[
                {"name":"ecosystem","value":dep.get("ecosystem","unknown")},
                {"name":"source_file","value":dep.get("source_file","")},
            ],
        }
        if dep.get("scope"):
            item["scope"]=dep["scope"]
        components.append(item)
    return {
        "bomFormat":"CycloneDX",
        "specVersion":"1.5",
        "version":1,
        "components":components,
    }

def _risk_summary(contents:list[dict],dependencies:list[dict])->dict:
    findings=[]
    scanned_files=0
    skipped_binary=0
    for item in contents:
        data=item["content_bytes"]
        path=item["relative_path"]
        if len(data)>MAX_REVIEW_TEXT_BYTES:
            findings.append({
                "severity":"MEDIUM",
                "rule":"large_text_file_not_scanned",
                "relative_path":path,
                "detail":f"{len(data)} bytes exceeds static scan limit",
            })
            continue
        try:
            value=data.decode("utf-8")
        except UnicodeDecodeError:
            skipped_binary+=1
            continue
        scanned_files+=1
        for severity,rule,pattern in RISK_RULES:
            if pattern.search(value):
                findings.append({
                    "severity":severity,
                    "rule":rule,
                    "relative_path":path,
                })
    severity_counts=Counter(x["severity"] for x in findings)
    if severity_counts["HIGH"]:
        level="HIGH"
    elif severity_counts["MEDIUM"]:
        level="MEDIUM"
    else:
        level="LOW"
    return {
        "risk_level":level,
        "finding_count":len(findings),
        "severity_counts":{
            "HIGH":severity_counts["HIGH"],
            "MEDIUM":severity_counts["MEDIUM"],
            "LOW":severity_counts["LOW"],
        },
        "scanned_text_files":scanned_files,
        "skipped_binary_files":skipped_binary,
        "dependency_count":len(dependencies),
        "findings":findings,
        "note":"Static heuristic review only; this does not approve or deploy artifacts.",
    }

def _test_report(rows:list[dict])->dict:
    tests=[]
    for row in rows:
        tests.append({
            "test_command":row["test_command"],
            "exit_code":int(row["exit_code"]),
            "passed":bool(row["passed"]),
            "stdout":(row["stdout"] or "")[-20_000:],
            "stderr":(row["stderr"] or "")[-20_000:],
        })
    return {
        "total":len(tests),
        "passed":sum(1 for x in tests if x["passed"]),
        "failed":sum(1 for x in tests if not x["passed"]),
        "all_passed":bool(tests) and all(x["passed"] for x in tests),
        "results":tests,
    }

def ensure_release_review_package(candidate_id):
    with engine.begin() as db:
        candidate=db.execute(text("""
          SELECT rc.id,rc.proposal_id,rc.proposal_revision,rc.run_id,
                 rc.release_status,rc.deployment_enabled
          FROM release_candidates rc
          WHERE rc.id=CAST(:id AS uuid)
          FOR UPDATE
        """),{"id":candidate_id}).mappings().one_or_none()
        if candidate is None:
            raise LookupError("Release candidate not found")
        if candidate["deployment_enabled"]:
            raise RuntimeError("Review package cannot be generated for deployment-enabled candidate")

        existing=db.execute(text("""
          SELECT id,package_status,source_tree_sha256,package_sha256,
                 content_snapshot_complete
          FROM release_review_packages
          WHERE release_candidate_id=:id
        """),{"id":candidate["id"]}).mappings().one_or_none()
        if existing is not None:
            return {
                "review_package_id":str(existing["id"]),
                "release_candidate_id":str(candidate["id"]),
                "package_status":existing["package_status"],
                "source_tree_sha256":existing["source_tree_sha256"],
                "package_sha256":existing["package_sha256"],
                "content_snapshot_complete":existing["content_snapshot_complete"],
                "created":False,
            }

        artifact_rows=[
            dict(r) for r in db.execute(text("""
              SELECT sa.id,sa.relative_path,sa.sha256,sa.byte_size,sa.media_type,
                     sac.content_bytes,sac.content_sha256
              FROM sandbox_artifacts sa
              LEFT JOIN sandbox_artifact_contents sac ON sac.artifact_id=sa.id
              WHERE sa.run_id=:run_id
              ORDER BY sa.relative_path
            """),{"run_id":candidate["run_id"]}).mappings().all()
        ]
        if not artifact_rows:
            raise RuntimeError("Review package requires captured artifacts")

        manifest=[
            {
                "relative_path":x["relative_path"],
                "sha256":x["sha256"],
                "byte_size":int(x["byte_size"]),
                "media_type":x["media_type"],
            }
            for x in artifact_rows
        ]
        contents=[
            {
                "relative_path":x["relative_path"],
                "content_bytes":bytes(x["content_bytes"]),
            }
            for x in artifact_rows
            if x["content_bytes"] is not None
               and x["content_sha256"]==x["sha256"]
        ]
        snapshot_complete=len(contents)==len(artifact_rows)

        baseline=db.execute(text("""
          SELECT id,artifact_manifest
          FROM release_review_packages
          WHERE proposal_id=:proposal_id
            AND release_candidate_id<>:candidate_id
          ORDER BY generated_at DESC,id DESC
          LIMIT 1
        """),{
            "proposal_id":candidate["proposal_id"],
            "candidate_id":candidate["id"],
        }).mappings().one_or_none()
        baseline_manifest=list(baseline["artifact_manifest"]) if baseline else []
        diff=_artifact_diff(manifest,baseline_manifest)

        dependencies=_dependency_inventory(contents)
        sbom=_sbom(dependencies)
        tests=[
            dict(r) for r in db.execute(text("""
              SELECT test_command,exit_code,stdout,stderr,passed
              FROM sandbox_test_results
              WHERE run_id=:run_id
              ORDER BY captured_at,id
            """),{"run_id":candidate["run_id"]}).mappings().all()
        ]
        test_report=_test_report(tests)
        risk=_risk_summary(contents,dependencies)
        source_sha=_source_tree_sha(manifest)

        package_material={
            "generator_version":GENERATOR_VERSION,
            "release_candidate_id":str(candidate["id"]),
            "proposal_id":str(candidate["proposal_id"]),
            "proposal_revision":int(candidate["proposal_revision"]),
            "run_id":str(candidate["run_id"]),
            "baseline_package_id":str(baseline["id"]) if baseline else None,
            "content_snapshot_complete":snapshot_complete,
            "artifact_manifest":manifest,
            "artifact_diff":diff,
            "dependency_inventory":dependencies,
            "sbom":sbom,
            "test_report":test_report,
            "risk_summary":risk,
            "source_tree_sha256":source_sha,
        }
        package_sha=_sha(package_material)

        package_id=db.execute(text("""
          INSERT INTO release_review_packages(
            release_candidate_id,proposal_id,proposal_revision,run_id,
            baseline_package_id,package_status,content_snapshot_complete,
            artifact_manifest,artifact_diff,dependency_inventory,sbom,
            test_report,risk_summary,source_tree_sha256,package_sha256,
            generator_version)
          VALUES(
            :candidate_id,:proposal_id,:revision,:run_id,:baseline_id,
            'GENERATED',:snapshot_complete,
            CAST(:manifest AS jsonb),CAST(:diff AS jsonb),
            CAST(:dependencies AS jsonb),CAST(:sbom AS jsonb),
            CAST(:tests AS jsonb),CAST(:risk AS jsonb),
            :source_sha,:package_sha,:generator)
          RETURNING id
        """),{
            "candidate_id":candidate["id"],
            "proposal_id":candidate["proposal_id"],
            "revision":candidate["proposal_revision"],
            "run_id":candidate["run_id"],
            "baseline_id":baseline["id"] if baseline else None,
            "snapshot_complete":snapshot_complete,
            "manifest":json.dumps(manifest,ensure_ascii=False),
            "diff":json.dumps(diff,ensure_ascii=False),
            "dependencies":json.dumps(dependencies,ensure_ascii=False),
            "sbom":json.dumps(sbom,ensure_ascii=False),
            "tests":json.dumps(test_report,ensure_ascii=False),
            "risk":json.dumps(risk,ensure_ascii=False),
            "source_sha":source_sha,
            "package_sha":package_sha,
            "generator":GENERATOR_VERSION,
        }).scalar_one()

    return {
        "review_package_id":str(package_id),
        "release_candidate_id":str(candidate["id"]),
        "package_status":"GENERATED",
        "source_tree_sha256":source_sha,
        "package_sha256":package_sha,
        "content_snapshot_complete":snapshot_complete,
        "risk_level":risk["risk_level"],
        "dependency_count":len(dependencies),
        "artifact_count":len(manifest),
        "created":True,
    }
