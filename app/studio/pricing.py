"""Cost estimate BEFORE a call and actual cost AFTER, from the config price table."""
import math


class PriceError(Exception):
    pass


def price_row(cfg, price_key):
    row = (cfg.get("prices") or {}).get(price_key)
    if row is None:
        raise PriceError(f"no price row '{price_key}' in config.prices")
    return row


def units_for(kind, params, ctx):
    """Billable units a call is expected to consume, in the price row's unit."""
    if kind == "image":
        return float(params.get("num_images", 1))
    if kind == "video":
        return float(ctx["video_seconds"])
    if kind == "voice":
        return len(ctx["script"]) / 1000.0
    if kind == "lipsync":
        return float(ctx["audio_seconds"])
    return 1.0


def cost(cfg, price_key, kind, units, params=None):
    row = price_row(cfg, price_key)
    params = params or {}
    if row.get("input_usd_per_token") is not None and ("input_tokens" in params or "output_tokens" in params):
        spent = (float(params.get("input_tokens") or 0) * float(row["input_usd_per_token"])
                 + float(params.get("output_tokens") or 0) * float(row.get("output_usd_per_token") or 0))
        return round(spent, 6)
    usd = row["usd_per_unit"] * units
    mult = (row.get("resolution_multipliers") or {}).get(params.get("resolution"), 1.0)
    return round(usd * mult, 4)


def describe(cfg, price_key):
    row = price_row(cfg, price_key)
    return f"${row['usd_per_unit']}/{row['unit']} ({row.get('source')}, checked {row.get('checked')})"


def est_audio_seconds(script, cps=15):
    return max(1.0, round(len(script) / float(cps), 1))


def auto_video_seconds(audio_seconds, lo=3, hi=15):
    return int(min(hi, max(lo, math.ceil(audio_seconds))))
