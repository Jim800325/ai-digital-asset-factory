from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from app.providers.animation.shrimp.bilibili_live_cloud_kms_acceptance import (
    run_live_cross_cloud_acceptance,
    run_live_provider_acceptance,
)


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument(
        "--provider",
        choices=["AWS_KMS","GCP_KMS","AZURE_KEY_VAULT"],
    )
    parser.add_argument(
        "--cross-cloud",
        nargs="+",
        choices=["AWS_KMS","GCP_KMS","AZURE_KEY_VAULT"],
    )
    parser.add_argument("--threshold",type=int,default=2)
    parser.add_argument("--actor",default="step10b27-live-cli")
    args=parser.parse_args()

    if bool(args.provider)==bool(args.cross_cloud):
        parser.error("choose exactly one of --provider or --cross-cloud")

    if args.provider:
        result=run_live_provider_acceptance(
            args.provider,actor=args.actor
        )
    else:
        result=run_live_cross_cloud_acceptance(
            args.cross_cloud,
            threshold=args.threshold,
            actor=args.actor,
        )

    print(json.dumps(result,default=str,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
