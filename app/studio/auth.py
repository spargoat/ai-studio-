"""Shared-password sign-in, mirroring ads-tracker (lib/session.mjs + proxy.ts):
APP_PASSWORD-style env password + session secret, HMAC-signed expiring cookie, /login page, 401 JSON for /api,
fail closed (503) when auth is required but not configured. Values are read from the environment only, never logged."""
import base64, hashlib, hmac, json, os, threading, time
from flask import jsonify, redirect, render_template_string, request, url_for

COOKIE = "studio_session"
SESSION_S = 14 * 24 * 3600
PUBLIC = ("/login", "/logout", "/favicon.ico", "/healthz", "/api/inngest")
_fail, _flock = {}, threading.Lock()

LOGIN = """<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Sign in · AI Studio</title><style>*{box-sizing:border-box}body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#09090b;color:#fafafa;font:15px system-ui,-apple-system,sans-serif}form{width:min(92vw,340px);padding:28px;border:1px solid #27272a;border-radius:12px;background:#18181b}
h1{font-size:18px;margin:0 0 4px}p{color:#a1a1aa;font-size:13px;margin:0 0 18px}input{width:100%;height:40px;padding:0 12px;border-radius:8px;border:1px solid #3f3f46;
background:#27272a;color:#fff;font-size:15px}button{width:100%;height:40px;margin-top:12px;border:0;border-radius:8px;background:#7c3aed;color:#fff;font-weight:600;font-size:14px;cursor:pointer}
.e{color:#f87171;font-size:13px;margin-top:10px}</style></head><body><form method=post action="/login">
<h1>AI Studio</h1><p>Enter the password to continue.</p><input type=hidden name=next value="{{nxt}}">
<input type=password name=password autofocus autocomplete=current-password placeholder="Password" aria-label="Password">
<button>Sign in</button>{% if err %}<div class=e>{{err}}</div>{% endif %}</form></body></html>"""


def config():
    pw, secret = os.environ.get("STUDIO_APP_PASSWORD", ""), os.environ.get("STUDIO_SESSION_SECRET", "")
    required = os.environ.get("STUDIO_REQUIRE_AUTH") == "1" or bool(pw)
    return {"pw": pw, "secret": secret, "required": required, "configured": bool(pw and secret)}


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _sig(secret, body):
    return _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def sign(secret, now=None):
    body = _b64(json.dumps({"exp": int((time.time() if now is None else now) + SESSION_S)}).encode())
    return f"{body}.{_sig(secret, body)}"


def verify(token, secret, now=None):
    try:
        body, sig = (token or "").split(".")
        if not secret or not hmac.compare_digest(sig, _sig(secret, body)):
            return False
        exp = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))["exp"]
        return exp > (time.time() if now is None else now)
    except Exception:
        return False


def _safe_next(n):
    return n if n and n.startswith("/") and not n.startswith("//") else "/"


def _ip():
    return (request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr or "?")


def init(app):
    @app.before_request
    def gate():
        c = config()
        if not c["required"] or request.path in PUBLIC:
            return None
        if not c["configured"]:
            return ("Sign-in is not configured.", 503)
        if verify(request.cookies.get(COOKIE), c["secret"]):
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "Sign in required."}), 401
        nxt = request.full_path if request.query_string else request.path
        return redirect(url_for("login", next=nxt))

    @app.get("/healthz")
    def healthz():
        return "ok"

    @app.route("/login", methods=["GET", "POST"])
    def login():
        c = config(); nxt = _safe_next(request.values.get("next"))
        if not c["required"]:
            return redirect(nxt)
        if not c["configured"]:
            return ("Sign-in is not configured.", 503)
        if request.method == "GET":
            return render_template_string(LOGIN, nxt=nxt, err=None)
        ip, now = _ip(), time.time()
        with _flock:
            fails = [t for t in _fail.get(ip, []) if now - t < 600]
            _fail[ip] = fails
        if len(fails) >= 10:
            return render_template_string(LOGIN, nxt=nxt, err="Too many attempts. Try again in a few minutes."), 429
        if not hmac.compare_digest(request.form.get("password", "").encode(), c["pw"].encode()):
            with _flock:
                _fail.setdefault(ip, []).append(now)
            time.sleep(0.5)
            return render_template_string(LOGIN, nxt=nxt, err="Wrong password."), 401
        with _flock:
            _fail.pop(ip, None)
        r = redirect(nxt)
        secure = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https" or os.environ.get("STUDIO_COOKIE_SECURE") == "1"
        r.set_cookie(COOKIE, sign(c["secret"]), max_age=SESSION_S, httponly=True, secure=secure, samesite="Lax", path="/")
        return r

    @app.route("/logout", methods=["GET", "POST"])
    def logout():
        r = redirect("/login"); r.delete_cookie(COOKIE, path="/"); return r
