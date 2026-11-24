"""Getting-started checklist rules (pure): which counter each step reads."""

from dataclasses import dataclass

from .config import OnboardingStep


@dataclass
class Counters:
    media: int = 0
    tagged: int = 0
    packs: int = 0
    tagged_owned: int = 0
    upgrades: int = 0


def step_progress(step: OnboardingStep, c: Counters) -> int:
    """Raw progress for a step (not capped at its target)."""
    return getattr(c, step.kind)
