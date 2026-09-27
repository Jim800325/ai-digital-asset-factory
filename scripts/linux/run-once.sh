#!/usr/bin/env sh
set -eu
curl -fsS -X POST http://localhost:8000/v1/runs
echo
