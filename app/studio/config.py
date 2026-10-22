import os
from pathlib import Path
import yaml
from . import APP_DIR


_ycache = {}
_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def load_yaml(path):
    """Cached by (path, mtime): templates are re-read many times per job. Returns a deep copy."""
    import copy
    st = os.stat(path)
    k = (str(path), st.st_mtime_ns, st.st_size)
    if k not in _ycache:
        with open(path) as f:
            _ycache[k] = yaml.load(f, Loader=_Loader) or {}
    return copy.deepcopy(_ycache[k])


def load_config(path=None):
    cfg = load_yaml(path or os.environ.get("STUDIO_CONFIG", APP_DIR / "config/config.yaml"))
    cfg["_personas"] = load_yaml(APP_DIR / "config/personas.yaml")
    if os.environ.get("VERCEL") == "1":
        # The deployment filesystem is read-only outside /tmp. Job bytes live in Blob;
        # this directory is only the scratch space for one stage.
        data = Path(os.environ.get("STUDIO_TMP") or "/tmp/studio-data")
        data.mkdir(parents=True, exist_ok=True)
        cfg["_data_dir"] = data
        cfg["_db_file"] = data / "studio.db"
    else:
        cfg["_data_dir"] = (APP_DIR / cfg.get("data_dir", "data")).resolve()
        cfg["_db_file"] = (APP_DIR / cfg.get("db_file", "data/studio.db")).resolve()
    return cfg


def load_template(name):
    p = Path(name)
    if not p.exists():
        p = APP_DIR / "templates" / f"{name}.yaml"
    t = load_yaml(p)
    t["_path"] = str(p)
    return t


# Vercel injects the runtime OIDC token on the incoming request. It is not an env var
# in the Python function. The value is never logged or placed in an API response.
OIDC_HEADER = "x-vercel-oidc-token"


def oidc_request_token():
    """Return the per-request OIDC token, or "" when this call is outside a request."""
    try:
        from flask import has_request_context, request
    except ImportError:
        return ""
    if not has_request_context():
        return ""
    return (request.headers.get(OIDC_HEADER) or "").strip()


def key_status(provider_cfg):
    """Return (source_name, is_set) for a provider. Never returns the secret."""
    for name in provider_cfg.get("key_env", []) or []:
        if os.environ.get(name):
            return name, True
        if name == "VERCEL_OIDC_TOKEN" and oidc_request_token():
            return OIDC_HEADER, True
    names = provider_cfg.get("key_env") or []
    return (names[0] if names else None), False


def provider_secret(provider_cfg):
    """Return (source_name, secret). The secret is empty when nothing is set. Do not log it."""
    name, ok = key_status(provider_cfg)
    if not ok:
        return name, ""
    if name == OIDC_HEADER:
        return name, oidc_request_token()
    return name, os.environ.get(name) or ""


def connection_status(cfg):
    """Connection rows for Settings. Masks are placeholders. Values are never included."""
    rows = []
    for name, provider in (cfg.get("providers") or {}).items():
        if not provider.get("key_env"):
            continue
        env, ok = key_status(provider)
        rows.append({"id": name, "label": name, "env": env, "set": bool(ok),
                     "mask": "••••••••" if ok else "", "optional": bool(provider.get("optional_key"))})
    url = bool(os.environ.get("ADINTEL_API_URL"))
    secret = bool(os.environ.get("ADINTEL_API_SECRET"))
    rows.append({"id": "adintel_url", "label": "Ads tracker", "env": "ADINTEL_API_URL", "set": url,
                 "mask": "••••••••" if url else "", "optional": True})
    rows.append({"id": "adintel_secret", "label": "Ads tracker secret", "env": "ADINTEL_API_SECRET", "set": secret,
                 "mask": "••••••••" if secret else "", "optional": True})
    return rows


def get_key(provider_cfg):
    _name, secret = provider_secret(provider_cfg)
    if secret:
        return secret
    raise RuntimeError(f"missing API key env var (one of {provider_cfg.get('key_env')})")
