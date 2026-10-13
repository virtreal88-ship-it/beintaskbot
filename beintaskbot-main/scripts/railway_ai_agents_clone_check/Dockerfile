FROM postgres:18-bookworm

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg python3 python3-venv; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      | gpg --batch --yes --dearmor -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      postgresql-14 postgresql-client-14 postgresql-contrib-14 \
      postgresql-15 postgresql-client-15 postgresql-contrib-15 \
      postgresql-16 postgresql-client-16 postgresql-contrib-16 \
      postgresql-17 postgresql-client-17 postgresql-contrib-17 \
      postgresql-18 postgresql-client-18 postgresql-contrib-18; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/runner
COPY scripts/railway_ai_agents_clone_check/requirements.txt /opt/runner/requirements.txt
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /opt/runner/requirements.txt

COPY ./*.py /opt/app/
COPY tests/postgres/verify_ai_agents.py /opt/app/tests/postgres/verify_ai_agents.py
COPY scripts/railway_ai_agents_clone_check/run_clone_check.py /opt/runner/run_clone_check.py
COPY scripts/railway_ai_agents_clone_check/entrypoint.sh /usr/local/bin/ai-agents-clone-check
RUN chmod 0755 /usr/local/bin/ai-agents-clone-check

ENTRYPOINT ["/usr/local/bin/ai-agents-clone-check"]
