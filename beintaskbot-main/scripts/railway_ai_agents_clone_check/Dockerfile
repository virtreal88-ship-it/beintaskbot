FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home audit

WORKDIR /opt/runner
COPY scripts/railway_ai_agents_clone_check/requirements-tenant-audit.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY scripts/railway_ai_agents_clone_check/tenant_audit.py ./tenant_audit.py

USER 10001:10001
ENTRYPOINT ["python", "/opt/runner/tenant_audit.py"]
