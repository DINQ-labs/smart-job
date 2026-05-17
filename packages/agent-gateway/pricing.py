"""LLM pricing & cost computation.

Prices are $/MTok (per million tokens). Anthropic's published rates as of
2026-04; update when pricing changes.

Cache semantics (Anthropic prompt caching):
  - cache_creation_input_tokens: 1.25× the input rate (writing to cache)
  - cache_read_input_tokens:     0.10× the input rate (reading from cache)
  - input_tokens:                the base input rate (non-cache)

Models routed through OpenRouter are prefixed (e.g. "anthropic/claude-opus-4-7").
_normalize_model() strips common provider prefixes before lookup.

When a model isn't in the table, compute_cost() returns 0.0 rather than
raising — token counts still flow through, cost just reads 0 until the
table is updated.
"""
from __future__ import annotations

# $/MTok — input rate, output rate
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Claude 4.x family
    "claude-opus-4-7":     (15.0, 75.0),
    "claude-opus-4-6":     (15.0, 75.0),
    "claude-opus-4-5":     (15.0, 75.0),
    "claude-sonnet-4-6":   (3.0,  15.0),
    "claude-sonnet-4-5":   (3.0,  15.0),
    "claude-haiku-4-5":    (1.0,  5.0),
    # Claude 3.x family (legacy)
    "claude-3-5-sonnet":   (3.0,  15.0),
    "claude-3-5-haiku":    (0.80, 4.0),
    "claude-3-opus":       (15.0, 75.0),
    # OpenRouter-hosted glm fallback (rough ceiling; real price varies)
    "glm-5-turbo":         (0.50, 2.0),
}

# Cache multipliers applied on top of the input rate.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER  = 0.10


def _normalize_model(model: str) -> str:
    """Strip provider prefixes and common date suffixes used by OpenRouter."""
    if not model:
        return ""
    # OpenRouter format: "anthropic/claude-opus-4-7" or "z-ai/glm-5-turbo"
    if "/" in model:
        model = model.split("/", 1)[1]
    # Trailing date suffixes like "-20251001"
    parts = model.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 8:
        model = parts[0]
    return model


def compute_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float:
    """Return USD cost for a single API response's usage block.

    Unknown model → 0.0. All token counts are non-negative integers; callers
    should pass 0 for missing fields.
    """
    normalized = _normalize_model(model)
    rates = MODEL_PRICING.get(normalized)
    if not rates:
        return 0.0
    in_rate, out_rate = rates
    cost = (
        input_tokens * in_rate
        + cache_creation_input_tokens * in_rate * CACHE_WRITE_MULTIPLIER
        + cache_read_input_tokens * in_rate * CACHE_READ_MULTIPLIER
        + output_tokens * out_rate
    ) / 1_000_000
    return round(cost, 6)
