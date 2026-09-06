"""Normalisation and tolerance helpers.

Two systems almost never agree on formatting. The internal book calls it
"VOD LN", the custodian calls it "GB00BH4HKS39"; one says "Morgan Stanley & Co.
International plc", the other "MORGAN STANLEY AND CO INTL PLC". None of that is
an economic difference, and an engine that raises breaks on it produces a
report nobody reads.
"""

from __future__ import annotations

import re
from decimal import Decimal

# Legal-form suffixes carry no identifying information for matching purposes.
_LEGAL_SUFFIXES = {
    "LTD", "LIMITED", "LLC", "LLP", "PLC", "INC", "INCORPORATED", "CORP",
    "CORPORATION", "CO", "COMPANY", "SA", "SAS", "NV", "BV", "AG", "GMBH",
    "SPA", "SRL", "AB", "AS", "OY", "PTE", "PTY", "INTL", "INTERNATIONAL",
}

_PUNCTUATION = re.compile(r"[^A-Z0-9\s]")
_WHITESPACE = re.compile(r"\s+")


def normalise_instrument(
    raw: str, aliases: dict[str, str] | None = None
) -> str:
    """Fold an instrument identifier to a canonical form.

    `aliases` maps a source-specific symbol to the canonical identifier, e.g.
    {"VOD LN": "GB00BH4HKS39"}. Lookup happens after case folding, so the
    caller's table does not need to anticipate casing.
    """
    token = _WHITESPACE.sub(" ", raw.strip().upper())
    if aliases:
        canonical = {k.strip().upper(): v for k, v in aliases.items()}
        if token in canonical:
            return canonical[token].strip().upper()
    return token


def _collapse_initialisms(words: list[str]) -> list[str]:
    """Join runs of single letters: ["J", "P", "MORGAN"] -> ["JP", "MORGAN"].

    Stripping punctuation turns "J.P. Morgan" into "J P MORGAN" but leaves
    "JP MORGAN" alone, so the two forms of the same name would not compare
    equal without this step.
    """
    out: list[str] = []
    run: list[str] = []
    for word in words:
        if len(word) == 1:
            run.append(word)
            continue
        if run:
            out.append("".join(run))
            run = []
        out.append(word)
    if run:
        out.append("".join(run))
    return out


def normalise_counterparty(raw: str) -> str:
    """Strip punctuation, conjunctions, initialism spacing and legal suffixes."""
    token = _PUNCTUATION.sub(" ", raw.upper())
    words = [w for w in _WHITESPACE.sub(" ", token).strip().split(" ") if w]
    words = [w for w in words if w != "AND"]
    words = _collapse_initialisms(words)
    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def within_tolerance(
    a: Decimal,
    b: Decimal,
    *,
    abs_tol: Decimal = Decimal("0"),
    rel_tol: Decimal = Decimal("0"),
) -> bool:
    """True if `a` and `b` agree to within either tolerance.

    Relative tolerance is taken against the larger magnitude, so the test is
    symmetric in its arguments -- otherwise the engine's answer would depend on
    which source happened to be passed first.
    """
    if a == b:
        return True
    difference = abs(a - b)
    if abs_tol > 0 and difference <= abs_tol:
        return True
    if rel_tol > 0:
        scale = max(abs(a), abs(b))
        if scale > 0 and (difference / scale) <= rel_tol:
            return True
    return False
