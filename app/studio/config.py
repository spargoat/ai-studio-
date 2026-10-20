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


def key_status(provider_cfg):
    """Return (env_name, is_set) for a provider. Never returns the value."""
    for name in provider_cfg.get("key_env", []) or []:
        if os.environ.get(name):
            return name, True
    names = provider_cfg.get("key_env") or []
    return (names[0] if names else None), False


def get_key(provider_cfg):
    for name in provider_cfg.get("key_env", []) or []:
        v = os.environ.get(name)
        if v:
            return v
    raise RuntimeError(f"missing API key env var (one of {provider_cfg.get('key_env')})")
