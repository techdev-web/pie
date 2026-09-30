"""Token → USD cost estimation for model runs."""

from __future__ import annotations

from decimal import Decimal

from packages.config import Settings, get_settings


def _is_pro(model: str) -> bool:
    m = (model or "").lower()
    return "pro" in m and "flash" not in m


def _is_embed(model: str) -> bool:
    m = (model or "").lower()
    return "embed" in m


def estimate_cost_usd(
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    settings: Settings | None = None,
) -> Decimal | None:
    """Estimate USD cost from token counts. Returns None when tokens unknown."""
    if input_tokens is None and output_tokens is None:
        return None
    settings = settings or get_settings()
    inp = int(input_tokens or 0)
    out = int(output_tokens or 0)
    if inp == 0 and out == 0:
        return Decimal("0")

    if _is_embed(model):
        rate = Decimal(str(settings.gemini_embed_per_mtok))
        return (Decimal(inp + out) / Decimal("1000000") * rate).quantize(Decimal("0.000001"))

    if _is_pro(model):
        in_rate = Decimal(str(settings.gemini_pro_input_per_mtok))
        out_rate = Decimal(str(settings.gemini_pro_output_per_mtok))
    else:
        in_rate = Decimal(str(settings.gemini_flash_input_per_mtok))
        out_rate = Decimal(str(settings.gemini_flash_output_per_mtok))

    cost = (Decimal(inp) / Decimal("1000000") * in_rate) + (
        Decimal(out) / Decimal("1000000") * out_rate
    )
    return cost.quantize(Decimal("0.000001"))
