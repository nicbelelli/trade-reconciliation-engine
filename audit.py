"""Append-only audit trail.

Every decision the engine makes is recorded as it is taken. An internal audit
function cannot accept "the engine matched these two records" -- it needs to
see which stage matched them, on what basis, and what was rejected on the way.

The log is append-only by construction: no public method mutates or removes an
entry, and `entries` hands back a tuple rather than the live list.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

MATCHED = "MATCHED"
REJECTED = "REJECTED"
BREAK_RAISED = "BREAK_RAISED"


@dataclass(frozen=True)
class AuditEntry:
    sequence: int
    timestamp: str
    stage: str
    action: str          # MATCHED | REJECTED | BREAK_RAISED
    internal_ids: str    # pipe-separated, kept flat so CSV export stays trivial
    external_ids: str
    reason: str


class AuditTrail:
    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def record(
        self,
        stage: str,
        action: str,
        reason: str,
        internal_ids: tuple[str, ...] = (),
        external_ids: tuple[str, ...] = (),
    ) -> AuditEntry:
        entry = AuditEntry(
            sequence=len(self._entries) + 1,
            timestamp=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            stage=stage,
            action=action,
            internal_ids="|".join(internal_ids),
            external_ids="|".join(external_ids),
            reason=reason,
        )
        self._entries.append(entry)
        return entry

    @property
    def entries(self) -> tuple[AuditEntry, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def filter(
        self, *, action: str | None = None, stage: str | None = None
    ) -> list[AuditEntry]:
        return [
            e
            for e in self._entries
            if (action is None or e.action == action)
            and (stage is None or e.stage == stage)
        ]

    def to_csv(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=list(AuditEntry.__dataclass_fields__)
            )
            writer.writeheader()
            for entry in self._entries:
                writer.writerow(asdict(entry))
        return path

    def to_json(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([asdict(e) for e in self._entries], indent=2),
            encoding="utf-8",
        )
        return path
