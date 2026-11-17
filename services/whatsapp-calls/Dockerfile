FROM golang:1.26.11-bookworm AS engine
WORKDIR /build
COPY services/whatsapp-calls/engine/go.mod services/whatsapp-calls/engine/go.sum ./
RUN go mod download
COPY services/whatsapp-calls/engine/ ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /call-engine ./cmd/server

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY services/whatsapp-calls/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=engine /call-engine /app/call-engine
COPY services/whatsapp-calls/supervisor.py ./
CMD ["python", "supervisor.py"]
