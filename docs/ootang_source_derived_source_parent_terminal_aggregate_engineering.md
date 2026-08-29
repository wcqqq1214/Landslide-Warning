# Ootang source-derived source-parent terminal aggregate v1

## Scope

This leaf authority closes one exact source-ingest parent's content-dependent
outcome branch after the current published effective `D/R` set is terminal. It
does not write a recovery-v6 receipt, reinterpret the cross-freeze completion
as recovery terminal state, or assess retained base rows.

The positive statement is deliberately named
`current_source_ingest_parent_terminal`. The broader
`source_parent_terminal`, whole-effective-workset, recovery/transitive closure,
drain, lifecycle, activation, trusted/E2, and formal-warning claims remain
false. ConvLSTM/v4, frozen data splits, metrics, thresholds, model parameters,
and scientific conclusions are unchanged.

## Why a separate aggregate is required

The pinned recovery transition contract for the source-ingest item is:

```text
source_snapshot_ingested -> derived_outcome_items_required -> []
```

For this item, recovery v6 deliberately records `closure_resolved=false` and
an empty terminal-action set because source ingestion can create an unknown
number of content-dependent outcome obligations. The cross-freeze completion
proves the source snapshot was ingested and the complete derived batch was
classified, but explicitly keeps `terminal_for_recovery_v6_key=false`.

Consequently, neither the cross-freeze event nor the effective `D/R` coverage
event alone is source-parent terminal evidence. The independent aggregate is
needed to bind the exact source completion to the exact fully settled child
set without mutating the frozen recovery authority.

## Exact aggregate condition

Let `U` be the singleton identity
`(key_id, natural_key, namespace_digest)` of the cross-freeze source item. The
aggregate requires all of the following under the existing lock order:

1. the published current effective overlay is deeply reconstructed;
2. `U` is unchanged from the frozen manifest row and appears exactly once in
   the current effective rows;
3. `U` is not a derived addition, rebound replacement, invalidation tombstone,
   or effective `D/R` coverage row;
4. the matching cross-freeze receipt/event proves
   `source_snapshot_ingested=true` and `derived_batch_classified=true`, while
   retaining `terminal_for_recovery_v6_key=false`;
5. the exact derived reservation/event and overlay event bind the same source
   edge and complete `D/R/I` application; and
6. the content-addressed current-effective-`D/R` coverage proof has its
   matching singleton event for the same overlay slot and exact identity,
   dependency-graph, and transition-plan digests.

The derived batch may include `I`, but invalidation is an overlay supersession
operation rather than terminal evidence. Current `D/R` rows must be covered by
their exact new identities; an old rebound identity never satisfies the
aggregate.

When these conditions hold, the proof records the original unresolved source
plan and the scoped aggregate resolution of `derived_outcome_items_required`
as a no-successor terminal step. This makes only the exact current source-
ingest parent terminal under this namespace. It does not alter the original
recovery plan or its receipt chain.

## Durable protocol

The independent namespace is:

```text
workset_recovery_v1/source_derived_source_parent_terminal_aggregate_v1/
  proofs/<proof-sha256>.json
  events/00000000000000000001-<entry-sha256>.json
  status.json
```

The deterministic proof has no timestamp and is addressed by its canonical
SHA-256. The singleton event is hash-linked from the all-zero predecessor and
is the publication boundary for the positive claim. `status.json` is a
replaceable, non-authoritative cache.

A valid missing, incomplete, or proof-only upstream `D/R` coverage state
returns waiting and publishes no aggregate authority. If this aggregate
crashes after writing its proof, the next poll must rebuild and verify the
same proof and append only the matching event. Orphan/branched aggregate
bytes, a changed current overlay, identity/provenance mismatch, or loss of an
already-published upstream relation fails closed.

## Verification

The three focused scenarios pass: exact source-parent publication and
idempotent replay after D1+D2 coverage, waiting when the upstream coverage has
only a proof, and aggregate proof-only crash forward adoption. The adjacent
seven-module chain covering this aggregate, effective `D/R` coverage,
dependent/source-only consumption and dispatch, and the effective overlay
passes `30/30` in 81.570 seconds.

Ruff formatting and E7/E9/F checks, Python compilation, strict profile loading,
and diff checks pass. Review corrected an initially synthetic D/R count,
canonicalized both source transition step ids with the pinned recovery helper,
narrowed machine status names, completed the explicit broad-false claim set,
and marked the replaceable status cache non-authoritative. Final integrity and
scope reviews report P0=0/P1=0/P2=0. No training, full scientific pipeline, or
real-network action was run.

## Next edge

The next narrow authority should assess exact retained-base terminal evidence
for the subset that survives the effective overlay unchanged. It must filter
by complete current identity rather than reuse a whole frozen-manifest boolean:
old `R` identities and removed `I` rows are not members of the current
effective denominator. Only after that subset and this source-parent aggregate
are published can a later authority prove current-effective-workset terminal
coverage; even then, generic recovery/transitive closure remains separate.
