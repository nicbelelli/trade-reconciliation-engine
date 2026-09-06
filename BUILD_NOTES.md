# Handover notes — read this, then delete before pushing

This file is for you, not for the repo.

## State

All four stages implemented. 73 tests, all passing. `make evaluate` reproduces
the README table.

## Before you push

1. `pytest` — confirm green on your machine
2. `ruff check src tests` — confirm clean
3. `pytest --collect-only -q | tail -1` — the real test count for your CV
4. Delete this file, then `git init && git add . && git commit`

## Your CV needs updating

It currently claims **96.4% detection, 0% false positives, 100% precision
across 51 tests**. This engine measures **100% detection, 0% false positives,
100% precision across 73 tests** — different numbers, and the README now states
them. Make the CV match the README. A recruiter who opens the repo is checking
exactly this.

Do not quote type accuracy on the CV unless you also explain it; 92.3% invites
a question you want to answer on your own terms, not under pressure.

## Read these before any interview

You did not write this code, so read it until you would have. Start here, in
order:

1. `stages.py::_exact_key` — and the comment about settlement date. That one
   line was a real bug: without it, stage 1 matched trades booked to settle on
   the wrong date as identical, and detection sat at 75.4%. Adding settlement
   date to the key took it to 100%. This is the best story in the repo because
   it is a genuine failure caught by measurement.
2. `stages.py::stage_2_tolerance` — the tie-break on `(price delta, record_id)`
   and why order independence matters.
3. `stages.py::_find_leg_subset` — the combinatorial cap, and why subsets must
   contain at least two legs.
4. `waterfall.py::classify_breaks` — the weakest part. Know why type accuracy
   is 92.3% and how you would fix it.

## Questions you will be asked

- Why a waterfall rather than one pass with loose criteria?
- Why is quantity never tolerated when price is?
- How do you know the false positive rate is real? (Benign injected
  variations — without them the metric is meaningless.)
- What breaks first at scale? (Stage 4's subset search.)
- Why Decimal and not float?
- What would you do differently with real data? (No ground truth, so detection
  rate becomes unmeasurable; you would fall back to an adjudicated sample.)

If you are asked "did you build this yourself" — answer honestly. "I designed
it and worked through the implementation with AI assistance, and I can walk you
through any decision in it" is a fine answer in 2026. A claim you cannot back
under follow-up questions is not.
