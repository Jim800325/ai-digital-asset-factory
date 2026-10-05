from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.providers.animation.shrimp.bilibili_offline_verifier import (
    verify_exported_bundle,
)


def main() -> int:
    parser=argparse.ArgumentParser(
        description="Verify a Shrimp Bilibili offline attestation bundle."
    )
    parser.add_argument("bundle",type=Path)
    args=parser.parse_args()

    snapshot=json.loads(args.bundle.read_text(encoding="utf-8"))
    result=verify_exported_bundle(snapshot)
    print(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if result["verification_status"]=="PASS" else 2


if __name__=="__main__":
    sys.exit(main())
