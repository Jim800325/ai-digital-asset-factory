from __future__ import annotations

import argparse
import json
from uuid import UUID

from app.providers.animation.shrimp.bilibili_hsm_root_ceremony import run_restore_drill


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--backup-id",required=True)
    parser.add_argument("--actor",default="step10b25-restore-process")
    args=parser.parse_args()

    result=run_restore_drill(UUID(args.backup_id),actor=args.actor)
    print(json.dumps(result,default=str,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
