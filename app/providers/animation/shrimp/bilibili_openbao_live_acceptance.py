from __future__ import annotations

import base64
import time
from typing import Any

import httpx
from sqlalchemy import text

from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_post_restore_certification import _ser,_sha


def _request(client:httpx.Client,method:str,url:str,token:str,**kwargs) -> httpx.Response:
    response=client.request(
        method,url,headers={"X-Vault-Token":token},**kwargs
    )
    return response


def _wait_ready(client:httpx.Client,base_url:str) -> None:
    last=None
    for _ in range(30):
        try:
            response=client.get(base_url.rstrip("/")+"/v1/sys/health")
            if response.status_code in (200,429,472,473):
                return
            last=f"HTTP {response.status_code}"
        except Exception as exc:
            last=str(exc)
        time.sleep(1)
    raise RuntimeError(f"OpenBao dev server not ready: {last}")


def run_openbao_live_acceptance(
    *,
    base_url:str,
    token:str,
    key_name:str,
    actor:str,
) -> dict[str,Any]:
    base=base_url.rstrip("/")
    sign_writes=0
    rotate_writes=0
    with httpx.Client(timeout=10.0) as client:
        _wait_ready(client,base)

        mount=_request(
            client,"POST",f"{base}/v1/sys/mounts/transit",token,
            json={"type":"transit"},
        )
        if mount.status_code not in (200,204,400):
            raise RuntimeError(
                f"OpenBao transit mount failed: HTTP {mount.status_code}"
            )

        create=_request(
            client,"POST",f"{base}/v1/transit/keys/{key_name}",token,
            json={"type":"ed25519"},
        )
        if create.status_code not in (200,204):
            raise RuntimeError(
                f"OpenBao key create failed: HTTP {create.status_code}"
            )

        read1=_request(
            client,"GET",f"{base}/v1/transit/keys/{key_name}",token
        )
        read1.raise_for_status()
        data1=read1.json()["data"]
        version1=int(data1["latest_version"])

        payload1=base64.b64encode(b"step-10b23-live-v1").decode("ascii")
        sign1=_request(
            client,"POST",f"{base}/v1/transit/sign/{key_name}",token,
            json={"input":payload1,"key_version":version1},
        )
        sign1.raise_for_status()
        sign_writes+=1
        signature1=sign1.json()["data"]["signature"]

        verify1=_request(
            client,"POST",f"{base}/v1/transit/verify/{key_name}",token,
            json={"input":payload1,"signature":signature1},
        )
        verify1.raise_for_status()
        verified1=bool(verify1.json()["data"]["valid"])

        rotate=_request(
            client,"POST",f"{base}/v1/transit/keys/{key_name}/rotate",token,
            json={},
        )
        rotate.raise_for_status()
        rotate_writes+=1

        read2=_request(
            client,"GET",f"{base}/v1/transit/keys/{key_name}",token
        )
        read2.raise_for_status()
        data2=read2.json()["data"]
        version2=int(data2["latest_version"])
        if version2<=version1:
            raise RuntimeError("OpenBao live rotation did not advance key version")

        historical=_request(
            client,"POST",f"{base}/v1/transit/verify/{key_name}",token,
            json={"input":payload1,"signature":signature1},
        )
        historical.raise_for_status()
        historical_ok=bool(historical.json()["data"]["valid"])

        payload2=base64.b64encode(b"step-10b23-live-v2").decode("ascii")
        sign2=_request(
            client,"POST",f"{base}/v1/transit/sign/{key_name}",token,
            json={"input":payload2,"key_version":version2},
        )
        sign2.raise_for_status()
        sign_writes+=1
        signature2=sign2.json()["data"]["signature"]
        verify2=_request(
            client,"POST",f"{base}/v1/transit/verify/{key_name}",token,
            json={"input":payload2,"signature":signature2},
        )
        verify2.raise_for_status()
        verified2=bool(verify2.json()["data"]["valid"])

    passed=(
        verified1 and verified2 and historical_ok
        and version2==version1+1
        and rotate_writes==1
        and sign_writes==2
    )
    snapshot={
        "schema_version":"shrimp-bilibili-openbao-live-acceptance-v0.1",
        "key_name":key_name,
        "initial_version":version1,
        "rotated_version":version2,
        "sign_write_count":sign_writes,
        "rotate_write_count":rotate_writes,
        "verification_passed":verified1 and verified2,
        "historical_verify_passed":historical_ok,
        "provider":"openbao/openbao",
        "production_writes":False,
    }
    acceptance_sha=_sha(snapshot)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_openbao_live_acceptances(
            acceptance_status,key_name,initial_version,rotated_version,
            sign_write_count,rotate_write_count,verification_passed,
            historical_verify_passed,acceptance_snapshot,acceptance_sha256,
            executed_by)
          VALUES(
            :status,:key,:v1,:v2,:signs,:rotates,:verified,:historical,
            CAST(:snapshot AS jsonb),:sha,:actor)
          RETURNING *
        """),{
            "status":"PASSED" if passed else "FAILED",
            "key":key_name,"v1":version1,"v2":version2,
            "signs":sign_writes,"rotates":rotate_writes,
            "verified":verified1 and verified2,
            "historical":historical_ok,
            "snapshot":canonical_json(snapshot),"sha":acceptance_sha,
            "actor":actor[:200],
        }).mappings().one()
    result=_ser(row)
    if not passed:
        raise RuntimeError("OpenBao live acceptance failed")
    return result


def list_openbao_live_acceptances(*,limit:int=100) -> list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT * FROM shrimp_bilibili_openbao_live_acceptances
          ORDER BY executed_at DESC,id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]
