"""Adaption credit prices per 1M tokens (from the Adaption model table, 2026-11-07).

credits(model, input_tokens, output_tokens) -> float. Local models (local:*) cost 0.
Cached-input prices are listed for reference; usage counters don't separate cached tokens,
so estimates use the full input price (an upper bound).
"""
from __future__ import annotations

# model: (input, cached_input, output) credits per 1M tokens
PRICES = {
    "gemma-4-31b-it": (0.09, 0.05, 0.34),
    "glm-5.2": (1.40, 0.26, 4.40),
    "qwen3.5-397b": (0.55, 0.225, 3.50),
    "gemma-4-26b-a4b-it": (0.0765, 0.0425, 0.255),
    "qwen3.8-27b": (0.039, 0.0039, 1.25),
    "deepseek-v4-flash": (0.04444, 0.001414, 0.13332),
    "glm-53-flash": (0.15, 0.03, 0.50),
}
ADAPTION_MODELS = list(PRICES)


def credits(model: str, input_tokens: int, output_tokens: int) -> float:
    if model.startswith("local:"):
        return 0.0
    p = PRICES.get(model)
    if p is None:
        return 0.0
    return (input_tokens * p[0] + output_tokens * p[2]) / 1e6


def usage_credits(usage_by_model: dict) -> float:
    """usage_by_model: {model: {"input_tokens": n, "output_tokens": m}} (run.py's USAGE_JSON line)."""
    return sum(credits(m, u.get("input_tokens", 0), u.get("output_tokens", 0)) for m, u in usage_by_model.items())
