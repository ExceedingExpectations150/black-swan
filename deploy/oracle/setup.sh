#!/usr/bin/env bash
# Black Swan backend — one-shot deploy on an Oracle Cloud "Always Free"
# Ampere A1 (ARM64) VM, exposed publicly via a free Cloudflare Tunnel.
#
# No inbound ports are opened: the tunnel dials OUT to Cloudflare, so you do
# NOT touch Oracle security lists or the VM firewall. Only outbound 443 (open
# by default) is used.
#
# PREREQUISITES (see deploy/oracle/README.md):
#   1. You are SSH'd into the VM and have cloned this repo.
#   2. backend/.env exists with GEMINI_API_KEY_PRIMARY, GEMINI_API_KEY_BACKUP
#      (and optionally ALLOWED_ORIGINS — can be set later).
#
# RUN (from the repo root):  bash deploy/oracle/setup.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE="backend/.env"
IMAGE="black-swan-api"
CONTAINER="black-swan-api"
APP_PORT=8000
UNIT_SRC="deploy/oracle/cloudflared-quick.service"
UNIT_DST="/etc/systemd/system/cloudflared-quick.service"

# ---------------------------------------------------------------- checks ---
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE not found." >&2
  echo "Create it first:  cp .env.example backend/.env  then fill in the two Gemini keys." >&2
  exit 1
fi
if ! grep -q '^GEMINI_API_KEY_PRIMARY=.\+' "$ENV_FILE"; then
  echo "ERROR: GEMINI_API_KEY_PRIMARY is empty in $ENV_FILE." >&2
  exit 1
fi

ARCH="$(uname -m)"
echo "==> Host architecture: $ARCH"

# --------------------------------------------------------------- docker ---
if ! command -v docker >/dev/null 2>&1; then
  echo "==> [1/4] Installing Docker..."
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER" || true
else
  echo "==> [1/4] Docker already present."
fi

# ---------------------------------------------------------------- build ---
echo "==> [2/4] Building the backend image (first build pulls torch — several minutes)..."
sudo docker build -t "$IMAGE" ./backend

# ------------------------------------------------------------------ run ---
echo "==> [3/4] (Re)starting the backend container, bound to localhost:$APP_PORT..."
sudo docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
# Bind to 127.0.0.1 only: the container is never publicly reachable; the
# Cloudflare Tunnel is the only ingress. A named volume caches the ~1GB
# TimesFM checkpoint so container restarts don't re-download it.
sudo docker run -d --name "$CONTAINER" \
  --restart unless-stopped \
  --env-file "$ENV_FILE" \
  -e PORT="$APP_PORT" \
  -p 127.0.0.1:"$APP_PORT":"$APP_PORT" \
  -v black-swan-hf:/app/.hf-cache \
  "$IMAGE"

# ---------------------------------------------------------- cloudflared ---
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "==> [4/4] Installing cloudflared..."
  case "$ARCH" in
    aarch64|arm64) CF_ARCH=arm64 ;;
    x86_64|amd64)  CF_ARCH=amd64 ;;
    *) echo "Unsupported arch $ARCH for cloudflared .deb" >&2; exit 1 ;;
  esac
  curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}.deb" -o /tmp/cloudflared.deb
  sudo dpkg -i /tmp/cloudflared.deb
else
  echo "==> [4/4] cloudflared already present."
fi

echo "==> Installing the quick-tunnel systemd service..."
sudo cp "$UNIT_SRC" "$UNIT_DST"
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared-quick.service
sudo systemctl restart cloudflared-quick.service

# ---------------------------------------------------- discover the URL ---
echo "==> Waiting for the public tunnel URL..."
TUNNEL_URL=""
for _ in $(seq 1 40); do
  TUNNEL_URL="$(sudo journalctl -u cloudflared-quick --no-pager 2>/dev/null \
    | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1 || true)"
  [ -n "$TUNNEL_URL" ] && break
  sleep 2
done

echo
echo "======================================================================"
if [ -z "$TUNNEL_URL" ]; then
  echo "Tunnel URL not captured yet. Check:  sudo journalctl -u cloudflared-quick -f"
  echo "then look for the https://<random>.trycloudflare.com line."
else
  WSS_URL="${TUNNEL_URL/https:/wss:}/ws"
  echo "  BACKEND IS LIVE:  $TUNNEL_URL"
  echo
  echo "  Paste these into Vercel (frontend) Environment Variables:"
  echo "    NEXT_PUBLIC_API_BASE = $TUNNEL_URL"
  echo "    NEXT_PUBLIC_WS_URL   = $WSS_URL"
  echo
  echo "  Quick check:  curl $TUNNEL_URL/api/economy"
fi
echo "----------------------------------------------------------------------"
echo "  AFTER you have the Vercel URL, allow it through CORS:"
echo "    1) edit backend/.env  ->  ALLOWED_ORIGINS=https://<your-app>.vercel.app"
echo "    2) sudo docker rm -f $CONTAINER && bash deploy/oracle/setup.sh"
echo "       (or just: sudo docker restart $CONTAINER after editing .env — note"
echo "        --env-file is read at start, so a restart alone re-reads it)"
echo "======================================================================"
echo
echo "NOTE: the trycloudflare.com URL persists only while cloudflared runs."
echo "If the service restarts, the URL changes and you must update Vercel's env."
echo "For a permanent URL, use a domain + named tunnel (see README)."
