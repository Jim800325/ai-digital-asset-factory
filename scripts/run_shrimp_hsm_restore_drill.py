from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

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
