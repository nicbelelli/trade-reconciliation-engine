"""The four stages of the matching waterfall.

The stages run in order of decreasing confidence. Each one only sees records
that every earlier stage failed to pair, so a record can never be matched
twice, and the stage that caught it is itself a signal: a book where most
volume only matches at stage 3 has a data quality problem upstream, even
though its final break count looks healthy.

    Stage 1  exact        strict composite key, no tolerance
    Stage 2  tolerance    same economics, small numeric differences allowed
    Stage 3  normalised   identifier and date representation differences
    Stage 4  aggregate    one block against its several allocations

Settlement date is required to agree exactly at every stage. Trade date may
slip by a day between systems and mean nothing; a settlement date that differs
is a trade that will fail to settle, which is exactly the kind of break the
engine exists to surface.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import combinations

from .audit import MATCHED, AuditTrail
from .models import Match, MatchStage, Trade
from .normalise import (
    normalise_counterparty,
    normalise_instrument,
    within_tolerance,
)


@dataclass(frozen=True)
class MatchConfig:
    """Tolerances the desk is willing to accept.

    Defaults are deliberately tight. Loosening a tolerance raises the match
    rate and lowers precision -- that trade-off is the whole design question,
    so it belongs in configuration and not buried in the matching code.
    """

    price_abs_tol: Decimal = Decimal("0.005")
    price_rel_tol: Decimal = Decimal("0.0001")     # 1 basis point
    amount_abs_tol: Decimal = Decimal("0.05")
    amount_rel_tol: Decimal = Decimal("0.0001")
    date_slack_days: int = 1                        # T vs T+1 booking lag
    max_aggregate_legs: int = 4                     # bounds stage 4's search
    instrument_aliases: dict[str, str] = field(default_factory=dict)


def _exact_key(trade: Trade) -> tuple:
    """Strict composite key. Every economic field participates.

    Settlement date is part of the key. Omitting it lets a trade booked to
    settle on the wrong date match as if it were identical, and because stage 1
    runs first that break would never reach the stages that could catch it.
    """
    return (
        trade.account,
        trade.instrument.strip().upper(),
        trade.side,
        trade.quantity,
        trade.price,
        trade.trade_date,
        trade.settlement_date,
    )


def stage_1_exact(
    internal: list[Trade],
    external: list[Trade],
    config: MatchConfig,
    audit: AuditTrail,
) -> list[Match]:
    """Match on a strict composite key, with no tolerance of any kind.

    Two passes. The first pairs on `trade_ref` where both sides carry one,
    because a shared business reference is stronger evidence than any
    combination of attributes. The second pairs whatever is left on the full
    composite key.

    Where a bucket holds several records on both sides -- genuine duplicates,
    or two identical fills booked separately -- they are paired off in order.
    This is safe here precisely because the key is exact: any pairing within
    the bucket produces the same economics.

    This stage is the reference implementation for the three that follow.
    """
    matches: list[Match] = []
    used_internal: set[str] = set()
    used_external: set[str] = set()

    # --- pass 1: shared trade reference -------------------------------------
    ext_by_ref: dict[str, list[Trade]] = defaultdict(list)
    for trade in external:
        if trade.trade_ref:
            ext_by_ref[trade.trade_ref].append(trade)

    for trade in internal:
        if not trade.trade_ref:
            continue
        candidates = [
            candidate
            for candidate in ext_by_ref.get(trade.trade_ref, [])
            if candidate.record_id not in used_external
        ]
        if not candidates:
            continue
        counterparty = candidates[0]
        if _exact_key(trade) != _exact_key(counterparty):
            # Same reference but different economics: leave it to a later
            # stage rather than forcing a match the numbers do not support.
            audit.record(
                stage=MatchStage.EXACT.value,
                action="REJECTED",
                reason=f"trade_ref {trade.trade_ref} shared but economics differ",
                internal_ids=(trade.record_id,),
                external_ids=(counterparty.record_id,),
            )
            continue
        used_internal.add(trade.record_id)
        used_external.add(counterparty.record_id)
        match = Match(
            internal_ids=(trade.record_id,),
            external_ids=(counterparty.record_id,),
            stage=MatchStage.EXACT,
            reason=f"shared trade_ref {trade.trade_ref}",
        )
        matches.append(match)
        audit.record(
            stage=MatchStage.EXACT.value,
            action=MATCHED,
            reason=match.reason,
            internal_ids=match.internal_ids,
            external_ids=match.external_ids,
        )

    # --- pass 2: full composite key -----------------------------------------
    ext_by_key: dict[tuple, list[Trade]] = defaultdict(list)
    for trade in external:
        if trade.record_id not in used_external:
            ext_by_key[_exact_key(trade)].append(trade)

    for trade in internal:
        if trade.record_id in used_internal:
            continue
        bucket = ext_by_key.get(_exact_key(trade))
        if not bucket:
            continue
        counterparty = bucket.pop(0)
        used_internal.add(trade.record_id)
        used_external.add(counterparty.record_id)
        match = Match(
            internal_ids=(trade.record_id,),
            external_ids=(counterparty.record_id,),
            stage=MatchStage.EXACT,
            reason="exact composite key",
        )
        matches.append(match)
        audit.record(
            stage=MatchStage.EXACT.value,
            action=MATCHED,
            reason=match.reason,
            internal_ids=match.internal_ids,
            external_ids=match.external_ids,
        )

    return matches


def _economic_key(trade: Trade) -> tuple:
    """Fields that must agree exactly before tolerance is even considered.

    Quantity is here, not among the tolerated fields, on purpose: a share count
    is discrete and either agrees or does not. Settlement date is here for the
    same reason -- a different settlement date is a failed settlement, not a
    formatting difference.
    """
    return (
        trade.account,
        trade.instrument.strip().upper(),
        trade.side,
        trade.quantity,
        trade.trade_date,
        trade.settlement_date,
    )


def _economics_within_tolerance(
    a: Trade, b: Trade, config: MatchConfig
) -> bool:
    return within_tolerance(
        a.price, b.price,
        abs_tol=config.price_abs_tol, rel_tol=config.price_rel_tol,
    ) and within_tolerance(
        a.gross_amount, b.gross_amount,
        abs_tol=config.amount_abs_tol, rel_tol=config.amount_rel_tol,
    )


def stage_2_tolerance(
    internal: list[Trade],
    external: list[Trade],
    config: MatchConfig,
    audit: AuditTrail,
) -> list[Match]:
    """Match records whose economics agree to within configured tolerance.

    Account, instrument, side, quantity, trade date and settlement date must
    agree exactly. Only price and gross amount are tolerated, covering rounding
    and fee treatment differences between two booking systems.

    Where several candidates qualify, the smallest absolute price difference
    wins, with record id as a tie-break. Both criteria are explicit so that the
    result cannot depend on the order rows happened to arrive in -- an engine
    whose output shifts with input order cannot be signed off, because it
    cannot be reproduced.
    """
    matches: list[Match] = []
    used_external: set[str] = set()

    ext_by_key: dict[tuple, list[Trade]] = defaultdict(list)
    for trade in external:
        ext_by_key[_economic_key(trade)].append(trade)

    for trade in sorted(internal, key=lambda t: t.record_id):
        candidates = [
            candidate
            for candidate in ext_by_key.get(_economic_key(trade), [])
            if candidate.record_id not in used_external
            and _economics_within_tolerance(trade, candidate, config)
        ]
        if not candidates:
            continue

        best = min(
            candidates,
            key=lambda c: (abs(c.price - trade.price), c.record_id),
        )
        used_external.add(best.record_id)

        price_delta = abs(best.price - trade.price)
        amount_delta = abs(best.gross_amount - trade.gross_amount)
        match = Match(
            internal_ids=(trade.record_id,),
            external_ids=(best.record_id,),
            stage=MatchStage.TOLERANCE,
            reason=(
                f"within tolerance: price delta {price_delta}, "
                f"amount delta {amount_delta}"
            ),
        )
        matches.append(match)
        audit.record(
            stage=MatchStage.TOLERANCE.value,
            action=MATCHED,
            reason=match.reason,
            internal_ids=match.internal_ids,
            external_ids=match.external_ids,
        )

    return matches


def _normalised_key(trade: Trade, config: MatchConfig) -> tuple:
    """Economic key with representation folded out, and trade date dropped.

    Trade date is deliberately absent: it is the field stage 3 is allowed to
    flex, so it is checked per candidate against date_slack_days rather than
    forming part of the bucket key.
    """
    return (
        trade.account,
        normalise_instrument(trade.instrument, config.instrument_aliases),
        trade.side,
        trade.quantity,
        trade.settlement_date,
        normalise_counterparty(trade.counterparty),
    )


def stage_3_normalised(
    internal: list[Trade],
    external: list[Trade],
    config: MatchConfig,
    audit: AuditTrail,
) -> list[Match]:
    """Match records that differ only in how they represent the same facts.

    Three representational differences are folded out: instrument identifiers
    through the security master alias table, counterparty names through legal
    form stripping, and a booking lag of up to date_slack_days on trade date.

    Everything stage 2 required exactly is still required exactly, and the
    stage 2 price and amount tolerances still apply. Relaxing representation is
    not the same as relaxing economics.

    Candidates are ranked by date difference first, then price difference, so a
    same-day candidate always beats a next-day one.
    """
    matches: list[Match] = []
    used_external: set[str] = set()

    ext_by_key: dict[tuple, list[Trade]] = defaultdict(list)
    for trade in external:
        ext_by_key[_normalised_key(trade, config)].append(trade)

    for trade in sorted(internal, key=lambda t: t.record_id):
        candidates = []
        for candidate in ext_by_key.get(_normalised_key(trade, config), []):
            if candidate.record_id in used_external:
                continue
            day_gap = abs((candidate.trade_date - trade.trade_date).days)
            if day_gap > config.date_slack_days:
                continue
            if not _economics_within_tolerance(trade, candidate, config):
                continue
            candidates.append((day_gap, candidate))

        if not candidates:
            continue

        day_gap, best = min(
            candidates,
            key=lambda pair: (
                pair[0],
                abs(pair[1].price - trade.price),
                pair[1].record_id,
            ),
        )
        used_external.add(best.record_id)

        carried = []
        if best.instrument.strip().upper() != trade.instrument.strip().upper():
            carried.append(
                f"instrument alias {trade.instrument} = {best.instrument}"
            )
        if best.counterparty != trade.counterparty:
            carried.append("counterparty legal form")
        if day_gap:
            carried.append(f"trade date slack {day_gap}d")
        if best.price != trade.price:
            carried.append(f"price delta {abs(best.price - trade.price)}")

        match = Match(
            internal_ids=(trade.record_id,),
            external_ids=(best.record_id,),
            stage=MatchStage.NORMALISED,
            reason="; ".join(carried) or "normalised match",
        )
        matches.append(match)
        audit.record(
            stage=MatchStage.NORMALISED.value,
            action=MATCHED,
            reason=match.reason,
            internal_ids=match.internal_ids,
            external_ids=match.external_ids,
        )

    return matches


def _group_key(trade: Trade, config: MatchConfig) -> tuple:
    """Bucket for aggregation. Bounding the search is what keeps it tractable."""
    return (
        trade.account,
        normalise_instrument(trade.instrument, config.instrument_aliases),
        trade.side,
        trade.trade_date,
        trade.settlement_date,
    )


def _vwap(legs: tuple[Trade, ...]) -> Decimal:
    """Quantity-weighted average price of an allocation set."""
    total_quantity = sum((leg.quantity for leg in legs), Decimal(0))
    weighted = sum((leg.quantity * leg.price for leg in legs), Decimal(0))
    return weighted / total_quantity


def _find_leg_subset(
    block: Trade,
    legs: list[Trade],
    config: MatchConfig,
) -> tuple[Trade, ...] | None:
    """Smallest set of legs that reconstitutes `block`, or None.

    Subsets are tried smallest first, so the simplest explanation of a block
    wins. Size is capped by max_aggregate_legs: the search is combinatorial,
    and an uncapped one will stall on a busy account.
    """
    cap = min(config.max_aggregate_legs, len(legs))
    for size in range(2, cap + 1):
        best: tuple[Trade, ...] | None = None
        for subset in combinations(legs, size):
            if sum((leg.quantity for leg in subset), Decimal(0)) != block.quantity:
                continue
            if not within_tolerance(
                _vwap(subset), block.price,
                abs_tol=config.price_abs_tol, rel_tol=config.price_rel_tol,
            ):
                continue
            if not within_tolerance(
                sum((leg.gross_amount for leg in subset), Decimal(0)),
                block.gross_amount,
                abs_tol=config.amount_abs_tol, rel_tol=config.amount_rel_tol,
            ):
                continue
            # Deterministic choice among equally sized valid subsets.
            key = tuple(leg.record_id for leg in subset)
            if best is None or key < tuple(leg.record_id for leg in best):
                best = subset
        if best is not None:
            return best
    return None


def stage_4_aggregate(
    internal: list[Trade],
    external: list[Trade],
    config: MatchConfig,
    audit: AuditTrail,
) -> list[Match]:
    """Match one block trade against the several allocations it was split into.

    A desk fills a 10,000 share order and allocates it across three funds. The
    custodian reports one block, the internal book holds three records. Nothing
    is broken, but no one-to-one stage can see that.

    A subset must contain at least two legs. Allowing a single leg would turn
    this stage into a second, looser pass at one-to-one matching, quietly
    matching pairs that stages 2 and 3 correctly rejected.

    Aggregation is attempted in both directions, since either side may be the
    one reporting the block.
    """
    matches: list[Match] = []
    used_internal: set[str] = set()
    used_external: set[str] = set()

    groups: dict[tuple, tuple[list[Trade], list[Trade]]] = defaultdict(
        lambda: ([], [])
    )
    for trade in internal:
        groups[_group_key(trade, config)][0].append(trade)
    for trade in external:
        groups[_group_key(trade, config)][1].append(trade)

    for key in sorted(groups, key=str):
        ints, exts = groups[key]
        ints = sorted(ints, key=lambda t: t.record_id)
        exts = sorted(exts, key=lambda t: t.record_id)

        # (block side, leg side, orientation flag)
        for blocks, legs, block_is_external in (
            (exts, ints, True),
            (ints, exts, False),
        ):
            for block in blocks:
                block_used = used_external if block_is_external else used_internal
                leg_used = used_internal if block_is_external else used_external
                if block.record_id in block_used:
                    continue

                available = [
                    leg for leg in legs if leg.record_id not in leg_used
                ]
                subset = _find_leg_subset(block, available, config)
                if subset is None:
                    continue

                leg_ids = tuple(leg.record_id for leg in subset)
                block_used.add(block.record_id)
                leg_used.update(leg_ids)

                match = Match(
                    internal_ids=(block.record_id,) if not block_is_external else leg_ids,
                    external_ids=leg_ids if not block_is_external else (block.record_id,),
                    stage=MatchStage.AGGREGATE,
                    reason=(
                        f"{len(subset)} allocations aggregate to block "
                        f"{block.record_id}, vwap {_vwap(subset)}"
                    ),
                )
                matches.append(match)
                audit.record(
                    stage=MatchStage.AGGREGATE.value,
                    action=MATCHED,
                    reason=match.reason,
                    internal_ids=match.internal_ids,
                    external_ids=match.external_ids,
                )

    return matches


WATERFALL = (
    stage_1_exact,
    stage_2_tolerance,
    stage_3_normalised,
    stage_4_aggregate,
)
