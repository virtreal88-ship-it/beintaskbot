FROM --platform=linux/amd64 evoapicloud/evolution-api@sha256:1bd8afc4a6cf48822e6cf02469aeae7bd35a12a6b616eacd1291926307f4d339
COPY patch-status.cjs test-status.cjs /opt/status-ack-fix/
RUN node /opt/status-ack-fix/patch-status.cjs /evolution/node_modules/baileys \
 && node --check /evolution/node_modules/baileys/lib/Socket/messages-recv.js \
 && node /opt/status-ack-fix/test-status.cjs
