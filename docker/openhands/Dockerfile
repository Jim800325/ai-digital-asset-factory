FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    OPENHANDS_SUPPRESS_BANNER=1

RUN python -m pip install --no-cache-dir "openhands==1.16.0"

WORKDIR /workspace

ENTRYPOINT ["openhands"]
