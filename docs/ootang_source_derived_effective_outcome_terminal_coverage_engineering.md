# Ootang source-derived effective outcome terminal coverage v1

## Scope

This leaf authority proves terminal coverage for the current published
overlay's complete effective `D/R` outcome set. It combines exact terminal
events from the source-only and dependent consumption namespaces without
modifying either namespace, the overlay, recovery v6, or the live ledger.

The positive statement is deliberately narrower than whole-effective-workset
or recovery closure. Source items, retained base items, invalidation audit rows,
and general recovery families are outside the covered set. Consequently
`all_effective_items_terminal`, source-parent terminal state, recovery-v6
terminal state, transition closure, drain, lifecycle, activation, trusted/E2
evidence, and formal-warning output remain false.

ConvLSTM/v4, frozen data splits, metrics, thresholds, model parameters, and
scientific conclusions are unchanged.

## Exact required and covered sets

Let the identity of an item be
`(key_id, natural_key, namespace_digest)`. The authority deep-replays the
published overlay and reconstructs every normalized current `D` and `R` row.
These rows form the required set `Q`; readiness is never used to shrink this
denominator.

`Q` is partitioned by provenance:

- `Qsrc` contains only rows whose dependency list is exactly the source key;
- `Qdep` contains every other current `D/R`. For diagnostics, it is split into
  the currently supported source-plus-`D/R` dependency class and an
  `unsupported` class. The latter includes no-source-edge or retained-base
  dependencies and remains required and missing in v1.

The source covered set `S` contains only exact current identities with a deeply
replayed source-consumption terminal event. The dependent covered set `P`
contains only exact current identities with a deeply replayed dependent-
consumption terminal event. Intent, receipt-only, status, materialization, live
ledger bytes, and the source gate do not count.

Before calculating coverage, the assessor requires `S` to be a subset of
`Qsrc`, `P` to be a subset of `Qdep`, and the two sets to be disjoint. Duplicate
identity or natural-key evidence, wrong provenance, an old rebound identity,
or evidence outside `Q` is an integrity failure. A current `I` row is absent
from `Q`; retained base rows are also outside `Q`, although a `D/R` depending
on them remains required and missing.

Coverage is publishable only when `Q` is non-empty and
`Q = S ⊎ P`. A valid missing row produces waiting status and no partial
authority.

## Coverage proof

The deterministic content-addressed proof binds the exact overlay event and
effective identity digests, the ordered required `D/R` identities, and one
ordered evidence row per key. Each row records its overlay topological index,
`D/R` kind, complete current identity, transition-plan digest, evidence
provenance, terminal step, and immutable receipt/event references. A supported
dependent row additionally binds its dependency-proof digest.

The count and digest fields are diagnostic; the ordered exact disjoint
bijection is the authority. The positive coverage claim becomes published only
when a matching singleton event references the proof.

## Durable protocol

The independent namespace is
`workset_recovery_v1/source_derived_effective_outcome_terminal_coverage_v1/`:

```text
proofs/<proof-sha256>.json
events/00000000000000000001-<entry-sha256>.json
status.json
```

The proof contains no creation timestamp and is named by its canonical SHA-256.
The singleton event is hash-linked from the all-zero predecessor and carries
the publication time. `status.json` is replaceable cache only.

If a crash occurs after proof publication, the next poll rebuilds and verifies
the same assessment and appends only the missing event. Event-without-proof,
multiple proof/event children, changed proof semantics, a changed overlay, or
durable coverage bytes whose required evidence disappeared fail closed. The
coordinator uses the existing manager/cycle/replay/shadow lock order and
preserves retryable busy errors.

## Verification

The three focused scenarios pass: exact D1+D2 completion and idempotent replay,
D1-only plus D2 receipt-only waiting without authority, and proof-only crash
forward adoption. The six-module adjacent chain covering this authority,
dependent consumption/dispatch, source-only consumption/dispatch, and the
effective overlay passes `27/27` in 56.632 seconds. After review fixes, the
focused suite passes again at `3/3`.

Ruff E7/E9/F, Python compilation, strict profile loading, and diff checks pass.
The final integrity and scope reviews report P0=0/P1=0/P2=0. Review closed a
missing direct pin for the reused drain publication kernel, clarified that the
public poll is read-only only toward upstream authorities, and aligned the
supported/unsupported diagnostics with the full non-source-only denominator.
No training, full scientific pipeline, or real-network action was run.

## Next edge

This proof may feed a later effective-workset assessor that separately handles
the source parent and any retained base families. This authority itself cannot
be promoted to recovery, transitive, drain, or lifecycle closure.
