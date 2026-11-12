"""List-price table used for an OPTIONAL cost estimate.

The estimate is a list-price equivalent: what the same tokens would cost at
public per-token API prices. It is not what you paid (subscriptions, discounts
and batch pricing are not modelled). Prices are USD per million tokens and
change over time - override them with a JSON file:

    [{"match": "opus", "in": 5, "out": 25, "cache_read": 0.5, "cache_write": 6.25}, ...]

Rules are tried in order; the first regex that matches the model id wins.
"""
import json
import re

DEFAULT_PRICES = [
    # older Opus generations were priced higher - keep these before the generic rule
    {"match": r"opus-4-1|opus-4-2025|opus-3", "in": 15.0, "out": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    {"match": r"opus", "in": 5.0, "out": 25.0, "cache_read": 0.5, "cache_write": 6.25},
    {"match": r"sonnet", "in": 3.0, "out": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    {"match": r"haiku-3", "in": 0.25, "out": 1.25, "cache_read": 0.03, "cache_write": 0.3},
    {"match": r"haiku", "in": 1.0, "out": 5.0, "cache_read": 0.1, "cache_write": 1.25},
]

COST_LABEL = "list-price equivalent, not actual spend"


def load_prices(path=None):
    if not path:
        return DEFAULT_PRICES
    with open(path, encoding="utf-8") as fh:
        rules = json.load(fh)
    if not isinstance(rules, list):
        raise ValueError("price file must be a JSON list of rules")
    for r in rules:
        for k in ("match", "in", "out", "cache_read", "cache_write"):
            if k not in r:
                raise ValueError("price rule missing %r: %r" % (k, r))
        re.compile(r["match"])
    return rules


def price_for(model, rules):
    for r in rules:
        if re.search(r["match"], model or ""):
            return r
    return None


def estimate(by_model, rules=None):
    """by_model: {model: {input, output, cache_read, cache_creation}} ->
    {"total_usd", "unpriced_tokens", "by_model": {model: usd}, "label"}"""
    rules = rules or DEFAULT_PRICES
    total, unpriced, per = 0.0, 0, {}
    for model, t in by_model.items():
        p = price_for(model, rules)
        toks = t["input"] + t["output"] + t["cache_read"] + t["cache_creation"]
        if not p:
            unpriced += toks
            continue
        usd = (t["input"] * p["in"] + t["output"] * p["out"]
               + t["cache_read"] * p["cache_read"] + t["cache_creation"] * p["cache_write"]) / 1e6
        per[model] = round(usd, 2)
        total += usd
    return {"total_usd": round(total, 2), "unpriced_tokens": unpriced, "by_model": per, "label": COST_LABEL}
