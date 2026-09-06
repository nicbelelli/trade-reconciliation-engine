"""Scoring the engine against generator ground truth.

Three numbers, and they have to be read together. Detection rate alone is
gamed by flagging everything; precision alone is gamed by flagging only the
obvious. The false positive rate is the one operations teams actually feel,
because every false positive is a person opening a ticket for a trade that was
never broken.

Scoring is at record level: a record is flagged if any raised break names it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .generator import GroundTruth
from .models import BreakType, ReconciliationResult, Trade


@dataclass(frozen=True)
class Metrics:
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    correctly_typed: int

    @property
    def detection_rate(self) -> float:
        """Recall: share of injected breaks the engine actually reported."""
        denominator = self.true_positives + self.false_negatives
        return self.true_positives / denominator if denominator else 0.0

    @property
    def false_positive_rate(self) -> float:
        """Share of sound records wrongly reported as broken."""
        denominator = self.false_positives + self.true_negatives
        return self.false_positives / denominator if denominator else 0.0

    @property
    def precision(self) -> float:
        """Share of reported breaks that were real."""
        denominator = self.true_positives + self.false_positives
        return self.true_positives / denominator if denominator else 0.0

    @property
    def type_accuracy(self) -> float:
        """Of the breaks detected, share also given the right classification."""
        return (
            self.correctly_typed / self.true_positives
            if self.true_positives
            else 0.0
        )

    def as_dict(self) -> dict[str, float | int]:
        return {
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "true_negatives": self.true_negatives,
            "detection_rate": round(self.detection_rate, 4),
            "false_positive_rate": round(self.false_positive_rate, 4),
            "precision": round(self.precision, 4),
            "type_accuracy": round(self.type_accuracy, 4),
        }

    def report(self) -> str:
        return "\n".join(
            [
                f"  detection rate       {self.detection_rate:>8.1%}"
                f"   ({self.true_positives}/{self.true_positives + self.false_negatives} injected breaks found)",
                f"  false positive rate  {self.false_positive_rate:>8.1%}"
                f"   ({self.false_positives}/{self.false_positives + self.true_negatives} sound records flagged)",
                f"  precision            {self.precision:>8.1%}"
                f"   ({self.true_positives}/{self.true_positives + self.false_positives} reported breaks real)",
                f"  type accuracy        {self.type_accuracy:>8.1%}"
                f"   ({self.correctly_typed}/{self.true_positives} classified correctly)",
            ]
        )


def flagged_records(result: ReconciliationResult) -> dict[str, BreakType]:
    """Every record id named by a break, with the type it was reported under."""
    flagged: dict[str, BreakType] = {}
    for item in result.breaks:
        for record_id in (item.internal_id, item.external_id):
            if record_id is not None:
                flagged.setdefault(record_id, item.break_type)
    return flagged


def evaluate(
    result: ReconciliationResult,
    truth: GroundTruth,
    internal: list[Trade],
    external: list[Trade],
) -> Metrics:
    """Score a reconciliation run against what the generator actually did."""
    all_ids = {t.record_id for t in internal} | {t.record_id for t in external}
    expected = set(truth.expected_breaks)
    sound = all_ids - expected

    flagged = flagged_records(result)
    flagged_ids = set(flagged)

    true_positives = expected & flagged_ids
    false_negatives = expected - flagged_ids
    false_positives = flagged_ids & sound
    true_negatives = sound - flagged_ids

    correctly_typed = sum(
        1
        for record_id in true_positives
        if flagged[record_id] is truth.expected_breaks[record_id]
    )

    return Metrics(
        true_positives=len(true_positives),
        false_positives=len(false_positives),
        false_negatives=len(false_negatives),
        true_negatives=len(true_negatives),
        correctly_typed=correctly_typed,
    )
