#!/usr/bin/env bash
# Synthetic empty-state startup only. Never load production variables or volumes.
set -euo pipefail
: "${POSTGRES_IMAGE:?Pin official postgres image digest}"
: "${REDIS_IMAGE:?Pin official redis image digest}"
IMAGE=evolution-status-ack:ci
BASE=evoapicloud/evolution-api@sha256:1bd8afc4a6cf48822e6cf02469aeae7bd35a12a6b616eacd1291926307f4d339
NET=evolution-ack-smoke
PG=evolution-ack-pg
REDIS=evolution-ack-redis
APP=evolution-ack-api
TMP=$(mktemp -d)
cleanup() {
  rc=$?
  set +e
  if [ "$rc" -ne 0 ]; then
    echo "FAIL isolated smoke exit=$rc; bounded diagnostics (no raw logs)"
    for container in "$APP" "$PG" "$REDIS"; do
      docker inspect --format '{{.Name}} status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}}' "$container" 2>/dev/null || true
    done
    # Only fixed, allowlisted marker counts leave the disposable log stream.
    docker logs --tail 100 "$APP" 2>&1 | python3 -c 'import sys,json; s=sys.stdin.read(65536).lower(); markers=["migration failed","migration succeeded","prisma generate failed","prisma generate succeeded","p1000","p1001","p1003","p2021","econnrefused","enotfound","eaddrinuse","cannot find module","err_require_esm","error","listening"]; print("synthetic startup marker counts:",json.dumps({m:s.count(m) for m in markers if m in s}))'
    docker exec "$PG" pg_isready -U smoke -d evolution 2>/dev/null || true
    docker exec "$REDIS" redis-cli ping 2>/dev/null || true
  fi
  docker rm -fv "$APP" "$PG" "$REDIS" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -f "$TMP/empty.env" "$TMP/base.json" "$TMP/patched.json"
  rmdir "$TMP" 2>/dev/null || true
  exit "$rc"
}
trap cleanup EXIT
# Downloads happen before isolated containers start. No pushes or logins.
docker pull "$BASE"
docker pull "$POSTGRES_IMAGE"
docker pull "$REDIS_IMAGE"
docker build --platform linux/amd64 --network=none -t "$IMAGE" .
docker image inspect "$BASE" > "$TMP/base.json"
docker image inspect "$IMAGE" > "$TMP/patched.json"
python3 - "$TMP" <<'PY'
import json, pathlib, sys
p=pathlib.Path(sys.argv[1]); a=json.loads((p/'base.json').read_text())[0]['Config']; b=json.loads((p/'patched.json').read_text())[0]['Config']
for key in ['Entrypoint','Cmd','WorkingDir','Env','User','ExposedPorts']:
 assert a.get(key)==b.get(key),f'Inherited runtime config changed: {key}'
print('PASS inherited image runtime configuration')
PY
docker run --rm --network none --entrypoint node "$IMAGE" -e 'const c=require("node:crypto"),f=require("node:fs");if(c.createHash("sha256").update(f.readFileSync("/evolution/node_modules/baileys/lib/Socket/messages-recv.js")).digest("hex")!=="c4d111115628d7727fa1bea59a122f217a16b379fe5f7b9e749ea5f7d8f2be77")process.exit(1);console.log("PASS running image patched hash")'
docker network create --internal "$NET" >/dev/null
test "$(docker network inspect --format '{{.Internal}}' "$NET")" = true
# No host ports, production secrets, existing data, or connected WhatsApp sessions.
docker run -d --name "$PG" --network "$NET" --network-alias db \
  -e POSTGRES_USER=smoke -e POSTGRES_PASSWORD=isolated-test-only -e POSTGRES_DB=evolution \
  "$POSTGRES_IMAGE" >/dev/null
docker run -d --name "$REDIS" --network "$NET" --network-alias redis "$REDIS_IMAGE" >/dev/null
for i in $(seq 1 30); do docker exec "$PG" pg_isready -U smoke -d evolution >/dev/null && break; sleep 2; done
docker exec "$PG" pg_isready -U smoke -d evolution >/dev/null
docker exec "$REDIS" redis-cli ping | grep -qx PONG
: > "$TMP/empty.env"
docker run -d --name "$APP" --network "$NET" \
  --mount "type=bind,source=$TMP/empty.env,target=/evolution/.env,readonly" \
  -e DATABASE_PROVIDER=postgresql \
  -e DATABASE_ENABLED=true \
  -e DATABASE_URL='postgresql://smoke:isolated-test-only@db:5432/evolution?schema=public' \
  -e DATABASE_CONNECTION_URI='postgresql://smoke:isolated-test-only@db:5432/evolution?schema=public' \
  -e DATABASE_CONNECTION_CLIENT_NAME=evolution-smoke \
  -e SERVER_TYPE=http -e SERVER_PORT=8080 -e SERVER_URL=http://localhost:8080 \
  -e AUTHENTICATION_API_KEY=isolated-test-only \
  -e CACHE_REDIS_ENABLED=true -e CACHE_REDIS_URI=redis://redis:6379/0 \
  -e CACHE_REDIS_PREFIX_KEY=smoke -e CACHE_REDIS_SAVE_INSTANCES=false \
  -e TELEMETRY_ENABLED=false -e WEBHOOK_GLOBAL_ENABLED=false -e PROVIDER_ENABLED=false \
  -e RABBITMQ_ENABLED=false -e RABBITMQ_GLOBAL_ENABLED=false -e SQS_ENABLED=false \
  -e WEBSOCKET_ENABLED=false -e PUSHER_ENABLED=false -e CHATWOOT_ENABLED=false \
  -e TYPEBOT_ENABLED=false -e OPENAI_ENABLED=false -e DIFY_ENABLED=false \
  -e N8N_ENABLED=false -e EVOAI_ENABLED=false -e FLOWISE_ENABLED=false -e S3_ENABLED=false \
  -e SENTRY_DSN= -e LOG_LEVEL=ERROR,WARN,LOG -e LOG_BAILEYS=error \
  "$IMAGE" >/dev/null
for i in $(seq 1 90); do
  if docker exec "$APP" node -e 'fetch("http://127.0.0.1:8080/").then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))' >/dev/null 2>&1; then break; fi
  test "$(docker inspect --format '{{.State.Running}}' "$APP")" = true || { echo 'FAIL: isolated application exited before readiness'; exit 1; }
  sleep 2
done
docker exec "$APP" node -e 'Promise.all([fetch("http://127.0.0.1:8080/").then(async r=>{if(!r.ok)throw Error("health failed");const j=await r.json();if(j.version!=="2.3.7")throw Error("unexpected version");}),fetch("http://127.0.0.1:8080/instance/fetchInstances",{headers:{apikey:"isolated-test-only"}}).then(async r=>{if(!r.ok)throw Error("instance read failed");const j=await r.json();if(!Array.isArray(j)||j.length)throw Error("nonempty instance state");})]).then(()=>console.log("PASS full entrypoint, migration, HTTP health, empty instances")).catch(e=>{console.error(e.message);process.exit(1)})'
sleep 10
test "$(docker inspect --format '{{.State.Running}}' "$APP")" = true
docker exec "$PG" psql -U smoke -d evolution -Atc 'SELECT count(*) FROM "Instance";' | grep -qx 0
echo 'PASS isolated full startup: empty Postgres/Redis, original entrypoint, zero sessions, internal-only network'
# Deliberately no docker save, upload-artifact, registry push, production call or raw log export.
