"""End-to-end behaviour.

The stage-1-only tests pass today. The full-waterfall tests are the
acceptance criteria for the finished engine: they are what produce the
headline numbers, so do not relax the thresholds to make them go green.
"""

from conftest import external, internal

from recon.generator import alias_table, generate
from recon.metrics import evaluate
from recon.models import BreakType
from recon.stages import WATERFALL, MatchConfig
from recon.waterfall import reconcile


# --- stage 1 only: passing today -----------------------------------------

def test_clean_population_produces_no_breaks():
    result, _ = reconcile(
        [internal("I1")], [external("E1")], stages=WATERFALL[:1]
    )
    assert result.breaks == []
    assert len(result.matches) == 1


def test_missing_external_record_is_reported():
    result, _ = reconcile([internal("I1")], [], stages=WATERFALL[:1])
    assert len(result.breaks) == 1
    assert result.breaks[0].break_type is BreakType.MISSING_IN_EXTERNAL


def test_missing_internal_record_is_reported():
    result, _ = reconcile([], [external("E1")], stages=WATERFALL[:1])
    assert result.breaks[0].break_type is BreakType.MISSING_IN_INTERNAL


def test_quantity_mismatch_is_classified_not_reported_as_missing():
    result, _ = reconcile(
        [internal("I1", quantity="1000")],
        [external("E1", quantity="1500")],
        stages=WATERFALL[:1],
    )
    assert result.breaks[0].break_type is BreakType.QUANTITY_MISMATCH


def test_side_mismatch_outranks_other_differences():
    from recon.models import Side

    result, _ = reconcile(
        [internal("I1", side=Side.BUY, quantity="1000")],
        [external("E1", side=Side.SELL, quantity="1500")],
        stages=WATERFALL[:1],
    )
    assert result.breaks[0].break_type is BreakType.SIDE_MISMATCH


def test_every_record_is_either_matched_or_broken():
    internal_records = [internal(f"I{i}") for i in range(5)]
    external_records = [external(f"E{i}") for i in range(3)]
    result, _ = reconcile(
        internal_records, external_records, stages=WATERFALL[:1]
    )
    accounted = (
        result.matched_internal_ids
        | result.matched_external_ids
        | {b.internal_id for b in result.breaks if b.internal_id}
        | {b.external_id for b in result.breaks if b.external_id}
    )
    expected = {t.record_id for t in internal_records + external_records}
    assert accounted == expected


def test_audit_trail_records_every_break():
    result, audit = reconcile([internal("I1")], [], stages=WATERFALL[:1])
    assert len(audit.filter(action="BREAK_RAISED")) == len(result.breaks)


# --- full waterfall: acceptance criteria ----------------------------------

def _full_run(n=500, seed=42):
    ints, exts, truth = generate(n_trades=n, seed=seed)
    config = MatchConfig(instrument_aliases=alias_table())
    result, _ = reconcile(ints, exts, config)
    return evaluate(result, truth, ints, exts)


def test_detection_rate_at_least_95_percent():
    assert _full_run().detection_rate >= 0.95


def test_no_false_positives():
    """Benign representation differences must not be reported as breaks."""
    assert _full_run().false_positive_rate == 0.0


def test_precision_is_total():
    assert _full_run().precision == 1.0


def test_results_hold_across_seeds():
    for seed in (1, 2, 3):
        metrics = _full_run(seed=seed)
        assert metrics.detection_rate >= 0.95
        assert metrics.false_positive_rate == 0.0
