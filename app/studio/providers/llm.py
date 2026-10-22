"""LLM text stages. Live script calls go through Vercel AI Gateway.

Auth is AI_GATEWAY_API_KEY when set, otherwise the deployment OIDC token
(VERCEL_OIDC_TOKEN, or the x-vercel-oidc-token header Vercel sends on each
request). ANTHROPIC_API_KEY and OPENAI_API_KEY stay optional direct overrides
and are used only when neither gateway credential is set. Dry runs never
reach this class. The credential is never written to disk or the event log.
"""
import json
from pathlib import Path

import requests

from .base import Provider, ProviderError, NonRetryable
from ..config import OIDC_HEADER, provider_secret

_GATEWAY_SOURCES = ("AI_GATEWAY_API_KEY", "VERCEL_OIDC_TOKEN", OIDC_HEADER)


def _redact(message, secret):
    if secret and secret in message:
        return message.replace(secret, "[redacted]")
    return message


def _route(name):
    if name in _GATEWAY_SOURCES:
        return "gateway"
    if name == "ANTHROPIC_API_KEY":
        return "anthropic"
    if name == "OPENAI_API_KEY":
        return "openai"
    return None


def _json_object(text):
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        fence = raw.rfind("```")
        if fence >= 0:
            raw = raw[:fence]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _result(text, ctx):
    params = ctx.get("params") or {}
    task = params.get("task") or ctx.get("kind") or ctx.get("stage") or "llm"
    parsed = _json_object(text)
    if task == "script" or ctx.get("kind") == "script":
        data = parsed or {}
        script = data.get("script") or (text or "").strip()
        hooks = data.get("hooks") if isinstance(data.get("hooks"), list) else []
        return {"task": "script", "framework": data.get("framework") or str(params.get("framework") or "PAS").upper(),
                "beats": data.get("beats") if isinstance(data.get("beats"), dict) else {},
                "script": script, "hooks": hooks, "text": data.get("text") or script, "items": hooks}
    if task == "creative_director" and not parsed:
        items = [part.strip() for part in (text or "").split("*") if part.strip()]
        return {"task": task, "text": "*".join(items), "items": items}
    if parsed:
        parsed.setdefault("task", task)
        parsed.setdefault("text", text or "")
        parsed.setdefault("items", parsed.get("items") if isinstance(parsed.get("items"), list) else [])
        return parsed
    return {"task": task, "text": text or "", "items": []}


class LLMProvider(Provider):
    paid = True

    def do_llm(self, ctx):
        name, token = provider_secret(self.cfg)
        route = _route(name) if token else None
        if not route:
            raise NonRetryable(
                f"needs key: LLM stage '{ctx.get('stage')}' needs AI_GATEWAY_API_KEY, VERCEL_OIDC_TOKEN, "
                "or the x-vercel-oidc-token request header "
                "(ANTHROPIC_API_KEY or OPENAI_API_KEY remain optional overrides). Nothing was called.")
        if route == "anthropic":
            return self._anthropic(ctx, token)
        model = ctx["model"]
        if route == "openai" and model.startswith("openai/"):
            model = model.split("/", 1)[1]
        base = self.cfg.get("base", "https://ai-gateway.vercel.sh/v1") if route == "gateway" \
            else self.cfg.get("openai_base", "https://api.openai.com/v1")
        return self._chat(ctx, f"{base.rstrip('/')}/chat/completions", token, model, route,
                          self.cfg.get("price_key"))

    def _chat(self, ctx, url, token, model, route, price_key):
        task = (ctx.get("params") or {}).get("task") or ctx.get("kind")
        body = {"model": model, "messages": [{"role": "user", "content": ctx.get("prompt") or ""}],
                "max_tokens": int(self.cfg.get("max_tokens") or 1024)}
        if task == "script" or ctx.get("kind") == "script":
            body["response_format"] = {"type": "json_object"}
        payload = self._post(url, {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, body)
        text = (((payload.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
        usage = payload.get("usage") or {}
        return self._save(ctx, text, usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0,
                          payload.get("id") or "", model, route, price_key)

    def _anthropic(self, ctx, token):
        model = self.cfg.get("anthropic_model") or "claude-haiku-4-5"
        base = self.cfg.get("anthropic_base", "https://api.anthropic.com").rstrip("/")
        body = {"model": model, "max_tokens": int(self.cfg.get("max_tokens") or 1024),
                "messages": [{"role": "user", "content": ctx.get("prompt") or ""}]}
        payload = self._post(f"{base}/v1/messages", {
            "x-api-key": token, "anthropic-version": self.cfg.get("anthropic_version") or "2023-06-01",
            "Content-Type": "application/json"}, body)
        text = "".join(block.get("text") or "" for block in payload.get("content") or [] if isinstance(block, dict))
        usage = payload.get("usage") or {}
        return self._save(ctx, text, usage.get("input_tokens") or 0, usage.get("output_tokens") or 0,
                          payload.get("id") or "", model, "anthropic", self.cfg.get("anthropic_price_key"))

    def _post(self, url, headers, body):
        secret = headers.get("Authorization") or headers.get("x-api-key") or ""
        if secret.lower().startswith("bearer "):
            secret = secret[7:]
        try:
            res = requests.post(url, json=body, headers=headers, timeout=self.cfg.get("timeout_seconds", 60))
        except requests.RequestException as e:
            raise ProviderError(_redact(f"llm request failed: {type(e).__name__}", secret)) from None
        if res.status_code in (401, 403):
            raise NonRetryable(f"llm {res.status_code}: credential refused. Nothing was saved.")
        if res.status_code in (400, 404, 422):
            raise NonRetryable(_redact(f"llm {res.status_code}: {res.text[:300]}", secret))
        if res.status_code >= 400:
            raise ProviderError(_redact(f"llm {res.status_code}: {res.text[:300]}", secret))
        try:
            return res.json()
        except ValueError:
            raise ProviderError("llm response was not JSON") from None

    def _save(self, ctx, text, input_tokens, output_tokens, request_id, model, route, price_key):
        data = _result(text, ctx)
        out = Path(ctx["out_dir"]) / "text.json"
        out.write_text(json.dumps(data, indent=2))
        body = {"output_path": str(out), "request_id": request_id,
                "units_actual": float(input_tokens) + float(output_tokens),
                "meta": {"model": model, "route": route,
                         "usage": {"input_tokens": int(input_tokens), "output_tokens": int(output_tokens)}}}
        if price_key:
            body["price_key"] = price_key
        return body

    do_script = do_llm
