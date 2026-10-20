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
    usd = row["usd_per_unit"] * units
    mult = (row.get("resolution_multipliers") or {}).get((params or {}).get("resolution"), 1.0)
    return round(usd * mult, 4)


def describe(cfg, price_key):
    row = price_row(cfg, price_key)
    return f"${row['usd_per_unit']}/{row['unit']} ({row.get('source')}, checked {row.get('checked')})"


def est_audio_seconds(script, cps=15):
    return max(1.0, round(len(script) / float(cps), 1))


def auto_video_seconds(audio_seconds, lo=3, hi=15):
    return int(min(hi, max(lo, math.ceil(audio_seconds))))
