"""The signal interface every detector emits.

A detector's job is not to say "this looks bad". It is to say what it measured,
how confident it is, which class of explanation it belongs to, and how much of
its evidence depends on attribution it cannot fully trust. A signal that cannot
answer those is not actionable, so they are fields rather than conventions.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field, asdict
from enum import Enum


class Direction(str, Enum):
    IMPROVING = "improving"
    DEGRADING = "degrading"


class Classification(str, Enum):
    """Why this signal moved.

    COMMERCIAL is the only class that should ever reach a recommendation.
    The other two exist so that a real trading decision is never made on a
    broken pipeline or on a measurement that was never going to hold still.
    """

    COMMERCIAL = "COMMERCIAL"
    DATA_QUALITY = "DATA_QUALITY"
    ARTIFACT = "ARTIFACT"


class Tier(str, Enum):
    """How much attribution the evidence leans on.

    A  platform-reported, attribution-free: spend, CPC, CPM, CTR, impressions.
    B  blended, no attribution needed: blended CAC, total new customers, AOV.
    C  channel-attributed last-click: channel CAC, channel ROAS.

    26.8% of orders are unattributed and TikTok has no cost file at all, so
    Tier C is discounted rather than trusted. This is a correctness constraint,
    not conservatism.
    """

    A = "A"
    B = "B"
    C = "C"


#: Multiplier applied to a signal's severity to yield its confidence.
TIER_RELIABILITY = {Tier.A: 1.0, Tier.B: 0.95, Tier.C: 0.55}


@dataclass(frozen=True)
class Signal:
    detector: str
    entity_type: str            # channel | variant | flow | cohort | source | account
    entity_id: str
    as_of_date: dt.date         # the cursor this was detected at
    fired_date: dt.date         # the last date of the evidence window
    severity: float             # 0-1, before attribution discounting
    direction: Direction
    evidence: dict = field(default_factory=dict)
    classification: Classification = Classification.COMMERCIAL
    attribution_tier: Tier = Tier.B
    data_quality_score: float = 1.0     # 1.0 = window is clean
    p_value: float | None = None        # None where the test is not statistical
    passed_fdr: bool = True

    @property
    def signal_id(self) -> str:
        """Stable across runs, so a backtest can track one signal over cursors."""
        raw = f"{self.detector}|{self.entity_type}|{self.entity_id}|{self.fired_date}"
        return hashlib.sha1(raw.encode()).hexdigest()[:12]

    @property
    def confidence(self) -> float:
        """Severity discounted by attribution reliability and window cleanliness.

        A Tier C signal tops out at 0.55 by construction, which is what keeps it
        below the autonomy threshold for anything irreversible.
        """
        return round(
            self.severity
            * TIER_RELIABILITY[self.attribution_tier]
            * self.data_quality_score,
            4,
        )

    @property
    def is_actionable(self) -> bool:
        return self.classification is Classification.COMMERCIAL and self.passed_fdr

    def to_row(self) -> dict:
        row = asdict(self)
        row.update(
            signal_id=self.signal_id,
            confidence=self.confidence,
            is_actionable=self.is_actionable,
            direction=self.direction.value,
            classification=self.classification.value,
            attribution_tier=self.attribution_tier.value,
        )
        return row
