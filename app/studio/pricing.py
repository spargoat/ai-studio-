"""Cost estimate BEFORE a call and actual cost AFTER, from the config price table.

Higgsfield fallback rates live here and are copied into config/config.yaml (prices.hf_*).
They are spend-guard ceilings, not a quote: the account estimate endpoint is authoritative
per call, and these rows are the higher published figure so a fal lock cannot under-budget.
"""
import math


# Checked 2026-10-22. Image: billing docs sample for soul v2 standard returned usd 0.094
# (the sample is illustrative; the API blog's Soul 2 starting rate is $0.0032/image).
# Video: API blog lists Wan 3.0 at $0.20/s; open.higgsfield.ai/pricing lists from $0.050/s.
# Lipsync: the model catalog has no lipsync endpoint; the guard uses the video rate so the
# fal-lock fallback can route, and the provider refuses to submit until a real model id is set.
HIGGSFIELD_PRICES = {
    "hf_image": {
        "unit": "image",
        "usd_per_unit": 0.094,
        "source": "https://docs.higgsfield.ai/docs/concepts/billing-and-retention soul v2 sample usd 0.094 "
                  "(higher than the $0.0032 Soul 2 starting rate at https://higgsfield.ai/blog/higgsfield-api)",
        "checked": "2026-10-22",
    },
    "hf_video": {
        "unit": "second",
        "usd_per_unit": 0.20,
        "source": "https://higgsfield.ai/blog/higgsfield-api Wan 3.0 $0.20/s "
                  "(list floor from $0.050/s at https://open.higgsfield.ai/pricing)",
        "checked": "2026-10-22",
    },
    "hf_lipsync": {
        "unit": "second",
        "usd_per_unit": 0.20,
        "source": "No lipsync endpoint in https://docs.higgsfield.ai/docs/models as of 2026-10-22. "
                  "Guard uses the Wan 3.0 per-second rate; live lipsync is refused until a model id is set.",
        "checked": "2026-10-22",
    },
}


class PriceError(Exception):
    pass


def price_row(cfg, price_key):
    row = (cfg.get("prices") or {}).get(price_key)
    if row is None and price_key in HIGGSFIELD_PRICES:
        row = HIGGSFIELD_PRICES[price_key]
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
