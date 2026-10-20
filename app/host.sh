#!/usr/bin/env bash
# Hosted AI Studio: password-gated app (waitress, 127.0.0.1:8765) + Cloudflare quick tunnel. Dry runs only.
# Secrets: ~/.config/ai_studio/hosting.env (STUDIO_APP_PASSWORD, STUDIO_SESSION_SECRET), mode 600. Never echoed.
# Usage: host.sh start | stop | status | url
set -u
APP=/workspace/ai_studio/app; RUN=$HOME/.local/state/ai_studio; mkdir -p "$RUN"
start() {
  [ -f "$RUN/server.pid" ] && kill -0 "$(cat "$RUN/server.pid")" 2>/dev/null || {
    nohup bash -c "set -a; . $HOME/.config/ai_studio/hosting.env; set +a; cd $APP;
      while true; do STUDIO_COOKIE_SECURE=1 python3 -m studio serve --public --host 127.0.0.1 --port 8765; sleep 3; done" \
      >> "$RUN/server.log" 2>&1 & echo $! > "$RUN/server.pid"; }
  [ -f "$RUN/tunnel.pid" ] && kill -0 "$(cat "$RUN/tunnel.pid")" 2>/dev/null || {
    nohup bash -c "while true; do $HOME/.local/bin/cloudflared tunnel --no-autoupdate --protocol http2 --url http://127.0.0.1:8765 2>&1; sleep 5; done" \
      >> "$RUN/tunnel.log" 2>&1 & echo $! > "$RUN/tunnel.pid"; }
}
stop() { for n in server tunnel; do [ -f "$RUN/$n.pid" ] && pkill -P "$(cat "$RUN/$n.pid")"; [ -f "$RUN/$n.pid" ] && kill "$(cat "$RUN/$n.pid")" 2>/dev/null; rm -f "$RUN/$n.pid"; done; }
url() { grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$RUN/tunnel.log" | tail -1; }
case "${1:-status}" in
  start) start; sleep 8; url;;
  stop) stop;;
  url) url;;
  status) for n in server tunnel; do [ -f "$RUN/$n.pid" ] && kill -0 "$(cat "$RUN/$n.pid")" 2>/dev/null && echo "$n up" || echo "$n down"; done; url;;
esac
