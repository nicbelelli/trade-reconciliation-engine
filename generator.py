"""Synthetic trade population with controlled break injection.

Real reconciliation data cannot be published, and without published data
nobody can check a claimed detection rate. Generating the population solves
that, but it also buys something a real extract could never give: ground truth.
Because the generator knows which records it broke, detection rate and false
positive rate become measurable rather than asserted.

Two kinds of divergence are injected, and the distinction is the whole point:

  breaks   -- real economic disagreements the engine MUST report
  benign   -- representation differences the engine MUST NOT report
              (rounding, ticker vs ISIN, T/T+1 booking lag, block allocation,
              counterparty name formatting)

An engine with no benign records in its test population can score a perfect
detection rate by flagging everything. The benign population is what makes the
false positive rate meaningful.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum

from .models import BreakType, Side, Source, Trade

# ticker as the internal book writes it -> ISIN as the custodian writes it
INSTRUMENTS: dict[str, str] = {
    "VOD LN": "GB00BH4HKS39",
    "HSBA LN": "GB0005405286",
    "AZN LN": "GB0009895292",
    "SHEL LN": "GB00BP6MXD84",
    "BP LN": "GB0007980591",
    "RIO LN": "GB0007188757",
    "BARC LN": "GB0031348658",
    "GSK LN": "GB00BN7SWP63",
}

COUNTERPARTIES: list[tuple[str, str]] = [
    ("Morgan Stanley & Co. International plc", "MORGAN STANLEY AND CO INTL PLC"),
    ("Goldman Sachs International", "GOLDMAN SACHS INTERNATIONAL"),
    ("J.P. Morgan Securities plc", "JP MORGAN SECURITIES PLC"),
    ("Merrill Lynch International", "MERRILL LYNCH INTL"),
    ("UBS AG London Branch", "UBS AG LONDON BRANCH"),
]

ACCOUNTS = ["FUND-ALPHA", "FUND-BETA", "FUND-GAMMA", "FUND-DELTA"]


class Divergence(str, Enum):
    """What was done to a generated pair."""

    CLEAN = "CLEAN"
    # --- benign: must still match ---
    ROUNDING = "BENIGN_ROUNDING"
    ALIAS = "BENIGN_INSTRUMENT_ALIAS"
    DATE_SLACK = "BENIGN_DATE_SLACK"
    CPTY_FORM = "BENIGN_COUNTERPARTY_FORM"
    ALLOCATION = "BENIGN_BLOCK_ALLOCATION"
    # --- breaks: must be reported ---
    DROP_EXTERNAL = "BREAK_MISSING_IN_EXTERNAL"
    DROP_INTERNAL = "BREAK_MISSING_IN_INTERNAL"
    QUANTITY = "BREAK_QUANTITY"
    PRICE = "BREAK_PRICE"
    SIDE = "BREAK_SIDE"
    DATE = "BREAK_DATE"


BREAK_KINDS = (
    Divergence.DROP_EXTERNAL,
    Divergence.DROP_INTERNAL,
    Divergence.QUANTITY,
    Divergence.PRICE,
    Divergence.SIDE,
    Divergence.DATE,
)

BENIGN_KINDS = (
    Divergence.ROUNDING,
    Divergence.ALIAS,
    Divergence.DATE_SLACK,
    Divergence.CPTY_FORM,
    Divergence.ALLOCATION,
)

_EXPECTED_TYPE: dict[Divergence, BreakType] = {
    Divergence.DROP_EXTERNAL: BreakType.MISSING_IN_EXTERNAL,
    Divergence.DROP_INTERNAL: BreakType.MISSING_IN_INTERNAL,
    Divergence.QUANTITY: BreakType.QUANTITY_MISMATCH,
    Divergence.PRICE: BreakType.PRICE_MISMATCH,
    Divergence.SIDE: BreakType.SIDE_MISMATCH,
    Divergence.DATE: BreakType.DATE_MISMATCH,
}


@dataclass
class GroundTruth:
    """What the generator did, kept apart from what the engine will conclude."""

    expected_breaks: dict[str, BreakType] = field(default_factory=dict)
    benign: dict[str, Divergence] = field(default_factory=dict)

    @property
    def break_count(self) -> int:
        return len(self.expected_breaks)

    @property
    def benign_count(self) -> int:
        return len(self.benign)

    def expected_type(self, record_id: str) -> BreakType | None:
        return self.expected_breaks.get(record_id)


def _quantise_amount(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def generate(
    n_trades: int = 500,
    break_rate: float = 0.08,
    benign_rate: float = 0.15,
    seed: int = 42,
    start: date = date(2026, 3, 2),
) -> tuple[list[Trade], list[Trade], GroundTruth]:
    """Build a paired internal/external population with known divergences.

    `seed` is fixed by default so a reported detection rate is reproducible by
    anyone who clones the repo -- a number nobody else can regenerate is not
    evidence of anything.
    """
    if not 0 <= break_rate <= 1 or not 0 <= benign_rate <= 1:
        raise ValueError("rates must be between 0 and 1")
    if break_rate + benign_rate > 1:
        raise ValueError("break_rate + benign_rate cannot exceed 1")

    rng = random.Random(seed)
    internal: list[Trade] = []
    external: list[Trade] = []
    truth = GroundTruth()

    for index in range(n_trades):
        roll = rng.random()
        if roll < break_rate:
            kind = rng.choice(BREAK_KINDS)
        elif roll < break_rate + benign_rate:
            kind = rng.choice(BENIGN_KINDS)
        else:
            kind = Divergence.CLEAN

        ticker = rng.choice(list(INSTRUMENTS))
        isin = INSTRUMENTS[ticker]
        cpty_internal, cpty_external = rng.choice(COUNTERPARTIES)
        account = rng.choice(ACCOUNTS)
        side = rng.choice([Side.BUY, Side.SELL])
        quantity = Decimal(rng.randrange(100, 25_000, 100))
        price = (Decimal(rng.randrange(50, 90_000)) / Decimal(100)).quantize(
            Decimal("0.01")
        )
        trade_date = start + timedelta(days=rng.randrange(0, 20))
        settlement_date = trade_date + timedelta(days=2)
        amount = _quantise_amount(quantity * price)
        ref = f"TRF{index:06d}"
        iid, eid = f"INT{index:06d}", f"EXT{index:06d}"

        base = dict(
            account=account,
            side=side,
            quantity=quantity,
            price=price,
            gross_amount=amount,
            currency="GBP",
            trade_date=trade_date,
            settlement_date=settlement_date,
            trade_ref=ref,
        )

        # ---- benign: block allocated across several internal records --------
        if kind == Divergence.ALLOCATION:
            legs = rng.randint(2, 3)
            unit = quantity / Decimal(legs)
            remainder = quantity
            for leg in range(legs):
                leg_qty = (
                    remainder
                    if leg == legs - 1
                    else unit.quantize(Decimal("1"))
                )
                remainder -= leg_qty
                leg_id = f"{iid}-{leg}"
                internal.append(
                    Trade(
                        record_id=leg_id,
                        source=Source.INTERNAL,
                        instrument=ticker,
                        counterparty=cpty_internal,
                        **{
                            **base,
                            "quantity": leg_qty,
                            "gross_amount": _quantise_amount(leg_qty * price),
                            "trade_ref": None,
                        },
                    )
                )
                truth.benign[leg_id] = kind
            external.append(
                Trade(
                    record_id=eid,
                    source=Source.EXTERNAL,
                    instrument=isin,
                    counterparty=cpty_external,
                    **{**base, "trade_ref": None},
                )
            )
            truth.benign[eid] = kind
            continue

        int_trade = Trade(
            record_id=iid,
            source=Source.INTERNAL,
            instrument=ticker,
            counterparty=cpty_internal,
            **base,
        )

        ext_kwargs = dict(base)
        ext_instrument = ticker
        ext_counterparty = cpty_internal

        if kind == Divergence.ROUNDING:
            ext_kwargs["price"] = price + Decimal("0.002")
            ext_kwargs["gross_amount"] = amount + Decimal("0.02")
        elif kind == Divergence.ALIAS:
            ext_instrument = isin
        elif kind == Divergence.DATE_SLACK:
            ext_kwargs["trade_date"] = trade_date + timedelta(days=1)
            ext_kwargs["trade_ref"] = None
        elif kind == Divergence.CPTY_FORM:
            ext_counterparty = cpty_external
        elif kind == Divergence.QUANTITY:
            ext_kwargs["quantity"] = quantity + Decimal(rng.randrange(50, 500))
            ext_kwargs["gross_amount"] = _quantise_amount(
                ext_kwargs["quantity"] * price
            )
            ext_kwargs["trade_ref"] = None
        elif kind == Divergence.PRICE:
            bumped = (price * Decimal("1.05")).quantize(Decimal("0.01"))
            ext_kwargs["price"] = bumped
            ext_kwargs["gross_amount"] = _quantise_amount(quantity * bumped)
            ext_kwargs["trade_ref"] = None
        elif kind == Divergence.SIDE:
            ext_kwargs["side"] = Side.SELL if side == Side.BUY else Side.BUY
            ext_kwargs["trade_ref"] = None
        elif kind == Divergence.DATE:
            ext_kwargs["settlement_date"] = settlement_date + timedelta(days=5)
            ext_kwargs["trade_ref"] = None

        ext_trade = Trade(
            record_id=eid,
            source=Source.EXTERNAL,
            instrument=ext_instrument,
            counterparty=ext_counterparty,
            **ext_kwargs,
        )

        if kind == Divergence.DROP_EXTERNAL:
            internal.append(int_trade)
            truth.expected_breaks[iid] = _EXPECTED_TYPE[kind]
            continue
        if kind == Divergence.DROP_INTERNAL:
            external.append(ext_trade)
            truth.expected_breaks[eid] = _EXPECTED_TYPE[kind]
            continue

        internal.append(int_trade)
        external.append(ext_trade)

        if kind in _EXPECTED_TYPE:
            truth.expected_breaks[iid] = _EXPECTED_TYPE[kind]
            truth.expected_breaks[eid] = _EXPECTED_TYPE[kind]
        elif kind in BENIGN_KINDS:
            truth.benign[iid] = kind
            truth.benign[eid] = kind

    rng.shuffle(internal)
    rng.shuffle(external)
    return internal, external, truth


def alias_table() -> dict[str, str]:
    """Ticker -> ISIN map, as a desk's security master would supply it."""
    return dict(INSTRUMENTS)
