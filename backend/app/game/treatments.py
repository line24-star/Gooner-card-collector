"""Card treatments (rarities) for the UI, derived from config/economy.toml.

The frontend owns how each treatment LOOKS (frontend/src/cards/treatments.ts); economy
numbers live in the config so clients can't tamper with them.
"""

from .config import get_config


def as_dicts() -> list[dict]:
    cfg = get_config()
    total = sum(t.weight for t in cfg.treatments.values())
    return [
        {"id": t.id, "name": t.name, "tier": t.tier, "multiplier": t.multiplier,
         "pack_weight": t.weight, "pack_odds": t.weight / total}
        for t in cfg.tiers()
    ]
