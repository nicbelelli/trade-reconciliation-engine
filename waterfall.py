"""Waterfall orchestration and break classification."""

from __future__ import annotations

from collections import defaultdict

from .audit import BREAK_RAISED, AuditTrail
from .models import (
    Break,
    BreakType,
    Match,
    ReconciliationResult,
    Trade,
)
from .normalise import normalise_instrument
from .stages import WATERFALL, MatchConfig


def reconcile(
    internal: list[Trade],
    external: list[Trade],
    config: MatchConfig | None = None,
    audit: AuditTrail | None = None,
    stages=WATERFALL,
) -> tuple[ReconciliationResult, AuditTrail]:
    """Run the waterfall, then classify whatever is left over.

    Each stage sees only the records no earlier stage could pair. `stages` is
    injectable so a caller can run a partial waterfall -- useful while the
    later stages are still being built, and for measuring how much work each
    stage actually does.
    """
    config = config or MatchConfig()
    audit = audit or AuditTrail()

    remaining_internal = list(internal)
    remaining_external = list(external)
    all_matches: list[Match] = []

    for stage in stages:
        if not remaining_internal or not remaining_external:
            break
        matches = stage(remaining_internal, remaining_external, config, audit)
        all_matches.extend(matches)

        consumed_internal = {i for m in matches for i in m.internal_ids}
        consumed_external = {e for m in matches for e in m.external_ids}
        remaining_internal = [
            t for t in remaining_internal if t.record_id not in consumed_internal
        ]
        remaining_external = [
            t for t in remaining_external if t.record_id not in consumed_external
        ]

    breaks = classify_breaks(
        remaining_internal, remaining_external, config, audit
    )
    return ReconciliationResult(matches=all_matches, breaks=breaks), audit


def _pairing_key(trade: Trade, config: MatchConfig) -> tuple:
    """Loose key used only to pair up leftovers for diagnosis."""
    return (
        trade.account,
        normalise_instrument(trade.instrument, config.instrument_aliases),
        trade.trade_date,
    )


def classify_breaks(
    internal: list[Trade],
    external: list[Trade],
    config: MatchConfig,
    audit: AuditTrail,
) -> list[Break]:
    """Turn unmatched leftovers into typed, actionable exceptions.

    "Unmatched" on its own is not useful to an operations team. A quantity
    mismatch on a known trade goes to the desk; a record missing entirely from
    the custodian statement goes to settlements. Classification is what makes
    the report routable, so leftovers are first paired on a loose key to see
    whether a near-twin exists on the other side, and only records with no
    counterpart at all are reported as missing.
    """
    breaks: list[Break] = []

    ext_by_key: dict[tuple, list[Trade]] = defaultdict(list)
    for trade in external:
        ext_by_key[_pairing_key(trade, config)].append(trade)

    unpaired_external = {t.record_id for t in external}

    for trade in internal:
        bucket = ext_by_key.get(_pairing_key(trade, config))
        candidate = None
        if bucket:
            candidate = bucket.pop(0)
            unpaired_external.discard(candidate.record_id)

        if candidate is None:
            breaks.append(
                Break(
                    break_type=BreakType.MISSING_IN_EXTERNAL,
                    detail=(
                        f"{trade.instrument} {trade.side.value} "
                        f"{trade.quantity} not present in external source"
                    ),
                    internal_id=trade.record_id,
                )
            )
            continue

        # A near-twin exists: report the first economic field that disagrees,
        # most material first, so the exception names one actionable cause.
        if trade.side != candidate.side:
            break_type = BreakType.SIDE_MISMATCH
            detail = f"internal {trade.side.value} vs external {candidate.side.value}"
        elif trade.quantity != candidate.quantity:
            break_type = BreakType.QUANTITY_MISMATCH
            detail = f"internal {trade.quantity} vs external {candidate.quantity}"
        elif trade.price != candidate.price:
            break_type = BreakType.PRICE_MISMATCH
            detail = f"internal {trade.price} vs external {candidate.price}"
        elif trade.gross_amount != candidate.gross_amount:
            break_type = BreakType.AMOUNT_MISMATCH
            detail = (
                f"internal {trade.gross_amount} vs external {candidate.gross_amount}"
            )
        elif trade.settlement_date != candidate.settlement_date:
            break_type = BreakType.DATE_MISMATCH
            detail = (
                f"settlement internal {trade.settlement_date} "
                f"vs external {candidate.settlement_date}"
            )
        else:
            break_type = BreakType.DATE_MISMATCH
            detail = (
                f"trade date internal {trade.trade_date} "
                f"vs external {candidate.trade_date}"
            )

        breaks.append(
            Break(
                break_type=break_type,
                detail=detail,
                internal_id=trade.record_id,
                external_id=candidate.record_id,
            )
        )

    for trade in external:
        if trade.record_id in unpaired_external:
            breaks.append(
                Break(
                    break_type=BreakType.MISSING_IN_INTERNAL,
                    detail=(
                        f"{trade.instrument} {trade.side.value} "
                        f"{trade.quantity} not present in internal source"
                    ),
                    external_id=trade.record_id,
                )
            )

    for item in breaks:
        audit.record(
            stage="CLASSIFICATION",
            action=BREAK_RAISED,
            reason=f"{item.break_type.value}: {item.detail}",
            internal_ids=(item.internal_id,) if item.internal_id else (),
            external_ids=(item.external_id,) if item.external_id else (),
        )

    return breaks
