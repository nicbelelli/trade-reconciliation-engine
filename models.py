"""Core domain objects for trade reconciliation.

Money and quantities are Decimal, never float. Binary floating point cannot
represent 0.1 exactly, so float arithmetic drifts silently on repeated
addition -- unacceptable when the output is a break report an auditor signs off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Source(str, Enum):
    """Which system a record came from."""

    INTERNAL = "INTERNAL"   # front-office book of record
    EXTERNAL = "EXTERNAL"   # counterparty / custodian statement


class MatchStage(str, Enum):
    """Waterfall stage at which a pairing was established."""

    EXACT = "STAGE_1_EXACT"
    TOLERANCE = "STAGE_2_TOLERANCE"
    NORMALISED = "STAGE_3_NORMALISED"
    AGGREGATE = "STAGE_4_AGGREGATE"


class BreakType(str, Enum):
    MISSING_IN_INTERNAL = "MISSING_IN_INTERNAL"
    MISSING_IN_EXTERNAL = "MISSING_IN_EXTERNAL"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    SIDE_MISMATCH = "SIDE_MISMATCH"
    DATE_MISMATCH = "DATE_MISMATCH"


@dataclass(frozen=True)
class Trade:
    """A single trade record as reported by one source.

    `record_id` is unique within a source. `trade_ref` is the shared business
    reference where both sides carry one -- deliberately optional, because in
    practice a counterparty statement often does not echo it back.
    """

    record_id: str
    source: Source
    account: str
    instrument: str
    side: Side
    quantity: Decimal
    price: Decimal
    gross_amount: Decimal
    currency: str
    trade_date: date
    settlement_date: date
    counterparty: str
    trade_ref: str | None = None

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"{self.record_id}: quantity must be positive")
        if self.price < 0:
            raise ValueError(f"{self.record_id}: price cannot be negative")


@dataclass(frozen=True)
class Match:
    """A pairing established by one stage of the waterfall.

    Sides are tuples of ids rather than single ids so that stage 4 can express
    a many-to-one allocation without needing a separate result type.
    """

    internal_ids: tuple[str, ...]
    external_ids: tuple[str, ...]
    stage: MatchStage
    reason: str = ""

    @property
    def is_aggregate(self) -> bool:
        return len(self.internal_ids) > 1 or len(self.external_ids) > 1


@dataclass(frozen=True)
class Break:
    """An exception the engine could not resolve."""

    break_type: BreakType
    detail: str
    internal_id: str | None = None
    external_id: str | None = None


@dataclass
class ReconciliationResult:
    matches: list[Match] = field(default_factory=list)
    breaks: list[Break] = field(default_factory=list)

    @property
    def matched_internal_ids(self) -> set[str]:
        return {i for m in self.matches for i in m.internal_ids}

    @property
    def matched_external_ids(self) -> set[str]:
        return {e for m in self.matches for e in m.external_ids}

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for m in self.matches:
            out[m.stage.value] = out.get(m.stage.value, 0) + 1
        for b in self.breaks:
            out[b.break_type.value] = out.get(b.break_type.value, 0) + 1
        out["TOTAL_MATCHES"] = len(self.matches)
        out["TOTAL_BREAKS"] = len(self.breaks)
        return out
