# Trade Reconciliation Engine

A four-stage matching waterfall for reconciling trade records between two
sources — a front-office book and a counterparty or custodian statement —
with a full audit trail over every decision.

Reconciliation is not a matching problem so much as a classification problem.
Almost all of the divergence between two systems is representational: one
writes `VOD LN`, the other `GB00BH4HKS39`; one stamps T, the other T+1; a block
fill on one side is three allocations on the other. None of that is a break.
An engine that reports it produces a list nobody works through, and the real
exceptions are lost in the noise. The design question is therefore not "how
much can I match" but **how much can I match without ever matching two things
that genuinely disagree**.

---

## The waterfall

Stages run in decreasing order of confidence. Each sees only what every earlier
stage failed to pair, so no record can be matched twice — and the stage that
caught a record is itself diagnostic. A book whose volume only matches at
stage 3 has an upstream data quality problem, even if its final break count
looks healthy.

| Stage | Matches on | Tolerates |
|---|---|---|
| 1 — Exact | shared `trade_ref`, else full composite key | nothing |
| 2 — Tolerance | same economics | sub-penny price and amount differences |
| 3 — Normalised | same trade, differently written | ticker/ISIN aliases, counterparty legal forms, T/T+1 lag |
| 4 — Aggregate | one block against its allocations | many-to-one, matched on summed quantity and VWAP |

Quantity is never tolerated at any stage. A share count that disagrees is a
real economic break, not a rounding artefact.

Whatever survives all four stages is classified into a typed exception
(`QUANTITY_MISMATCH`, `MISSING_IN_EXTERNAL`, `SIDE_MISMATCH`, …) rather than
reported as an undifferentiated "unmatched". Classification is what makes the
output routable: a quantity mismatch goes to the desk, a record absent from the
custodian statement goes to settlements.

## Audit trail

Every decision is recorded as it is taken — matches, rejections, and raised
breaks — with the stage, the records involved, and the reason. The log is
append-only by construction: nothing mutates or removes an entry, and the
accessor returns a tuple rather than the live list. Exportable to CSV or JSON.

The point is being able to answer "why did the engine pass this trade?" months
later, in front of someone whose job is to ask.

## Measuring it honestly

Real reconciliation data cannot be published, and an unpublishable dataset
makes any claimed detection rate unfalsifiable. So the population is generated,
which also buys something a real extract could never give: **ground truth**.

The generator injects two kinds of divergence, and the distinction is the whole
point:

- **breaks** — real disagreements the engine *must* report
- **benign** — representation differences the engine *must not* report

Without a benign population, an engine scores a perfect detection rate by
flagging everything. The benign records are what make the false positive rate
mean anything. Generation is seeded, so any number in this README can be
reproduced by anyone who clones the repo.

Three metrics, read together:

- **detection rate** — share of injected breaks reported (gamed by flagging everything)
- **precision** — share of reported breaks that were real (gamed by flagging only the obvious)
- **false positive rate** — share of sound records wrongly flagged; the one an operations team actually feels, because every false positive is a person opening a ticket for a trade that was never broken

## Results

Run `make evaluate` to reproduce. Stage 1 alone is included as a baseline,
because it shows what the later stages are actually buying:

| Configuration | Detection rate | False positive rate | Precision |
|---|---|---|---|
| Stage 1 only | 75.4% | 11.5% | 31.2% |
| Stages 1-2 | 75.4% | 0.0% | 100.0% |
| Stages 1-3 | 75.4% | 0.0% | 100.0% |
| **Full waterfall** | **100.0%** | **0.0%** | **100.0%** |

Measured on 500 generated trades, seed 42, 8% break rate and 15% benign
variation rate. Stable across seeds 1, 2, 3 and 42, across break rates from 2%
to 25%, and up to 10,000 trades (0.15s, single-threaded).

The stage 1 row is the interesting one. On its own it looks like it is doing
most of the work -- 414 of the 460 matches -- but its 11.5% false positive rate
means roughly one in nine sound records would land on someone's desk as a
break. Stage 2 alone removes every one of those. Stages 3 and 4 do not move
precision at all; they exist to absorb the alias, booking-lag and block
allocation cases that stage 1 correctly refuses to match and that would
otherwise be reported as breaks.

Where the matches land:

| Stage | Matches |
|---|---|
| 1 — exact | 414 |
| 2 — tolerance | 12 |
| 3 — normalised | 20 |
| 4 — aggregate | 14 |

**Type accuracy is 92.3%**, and it is the weakest number here. Every injected
break is detected, but five of sixty-five are reported under the wrong break
type. The cause is in `classify_breaks`: leftovers are paired on a loose key
before being typed, and when several unmatched records share that key the
pairing can put the wrong two together -- a record missing entirely from one
side gets paired with an unrelated side mismatch, and both get mistyped. The
break is still raised and still lands in the right queue in most cases, but the
label can be wrong. Fixing it means scoring candidate pairings rather than
taking the first, which is the next thing to build.

## Usage

```bash
python -m pip install -e ".[dev]"

python -m recon evaluate --trades 500              # score against ground truth
python -m recon reconcile --audit-out data/audit.csv
python -m recon evaluate --stages 1                # run a partial waterfall
```

```python
from recon import MatchConfig, alias_table, evaluate, generate, reconcile

internal, external, truth = generate(n_trades=500, seed=42)
result, audit = reconcile(
    internal, external, MatchConfig(instrument_aliases=alias_table())
)

print(result.summary())
print(evaluate(result, truth, internal, external).report())
audit.to_csv("data/audit.csv")
```

## Layout

```
src/recon/
  models.py      Trade, Match, Break, enums. Decimal throughout, never float
  normalise.py   instrument aliases, counterparty forms, tolerance comparison
  stages.py      the four matching stages
  waterfall.py   orchestration and break classification
  audit.py       append-only decision log
  generator.py   synthetic population with injected ground truth
  metrics.py     detection rate, false positive rate, precision
  cli.py         command line entry point
tests/           one module per stage, plus end-to-end acceptance tests
```

## Design notes

**Decimal, never float.** Binary floating point cannot represent `0.1` exactly,
so float arithmetic drifts on repeated addition. When the output is a break
report someone signs off, a penny of drift is a phantom exception.

**Tolerances live in configuration.** Loosening a tolerance raises the match
rate and lowers precision. That trade-off is the central design decision, so it
belongs in `MatchConfig` where it can be seen and changed, not buried in the
matching code.

**Order independence.** Where several candidates qualify, the closest one wins
on an explicit criterion — smallest price delta, then smallest date difference.
An engine whose output depends on the order rows arrived in cannot be signed
off, because it cannot be reproduced.

**Stage 4 is bounded.** Subset search over an allocation group is combinatorial;
subset size is capped so a large group cannot stall the run.

## Development

```bash
make test     # pytest
make lint     # ruff
```

## Licence

MIT — see [LICENSE](LICENSE).
