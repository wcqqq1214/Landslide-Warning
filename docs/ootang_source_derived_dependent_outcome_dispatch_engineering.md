# Ootang source-derived dependent outcome dispatch v1

## Scope

This authority advances one dependency-ready source-derived effective `D` or
`R` outcome from `outcome_materialized` planning into the canonical historical
materializer. It is a new sibling namespace and does not modify the pinned
source-only dispatcher or effective-key consumption authority. It therefore
avoids a reverse implementation-hash dependency.

The increment is control-plane only. It does not modify ConvLSTM/v4, frozen
data splits, metrics, thresholds, model parameters, or scientific conclusions.
It does not consume the newly materialized outcome into the live ledger and
does not claim terminal closure, drain, lifecycle, activation, trusted-time
evidence, E2 evidence, or formal-warning output.

## Readiness authority

The coordinator first deep-replays the published effective-workset overlay,
the source-only historical materialization dispatcher, and the effective-key
consumption authority. A dependent candidate must be an exact current-overlay
`D` or `R` row in stable topological order and must not be source-only.

Every dependency is resolved against the current effective identity before
evidence is considered:

- the exact source-parent natural key is satisfied only by the already
  deep-verified cross-freeze source expansion gate carried by the overlay;
- an effective `D/R` dependency is satisfied only by a deeply replayed
  consumption terminal event whose key id, natural key, and namespace digest
  equal that current effective row; and
- any dependency outside those reviewed branches remains unsupported and does
  not become ready.

A source-only materialization event, materialization receipt, status cache,
consumption intent, or consumption receipt without its matching terminal event
cannot satisfy a dependency. An invalidated `I` row is absent from the
effective graph. A rebound `R` dependency resolves to its new current key id,
so a frozen-key receipt or event cannot unlock it.

The initial reviewed graph is `source -> D1 -> D2`, with `D2` also depending
directly on the source. Therefore only the exact terminal consumption event for
`D1`, together with the cross-freeze source gate, makes `D2` dispatchable.

## Historical materialization

Readiness directly triggers the existing pinned historical materialization
planner and action; no separate readiness-only proof is published. The planner
reconstructs the immutable historical successor source and exact target record
without consulting the mutable current-source selector. The canonical
materializer remains the only outcome writer, and the public materialization
API is not used.

The intent binds the published overlay, source gate, every exact consumption
terminal receipt/event dependency, the complete current effective row and
transition identity, the dependency-proof digest, the exact historical
materialization contract, and this implementation. The output remains
non-terminal and requests the next action
`outcome_or_revision_consumed`.

## Durable protocol

The namespace is
`workset_recovery_v1/source_derived_dependent_outcome_dispatch_v1/` with
independent `intents/`, `receipts/`, `events/`, and cache-only `status.json`
paths. Under the surviving manager/cycle/replay/shadow lock order, each poll
heals or advances at most one key:

1. publish a create-only dependent-materialization intent;
2. execute or exactly adopt the canonical materializer publication;
3. publish a create-only non-terminal dispatcher receipt; and
4. append the matching hash-chained non-terminal event.

An intent-only or post-materializer crash replays the same contract and adopts
the exact immutable publication without duplicating it. A receipt-only crash
appends only the missing event. Orphaned, branched, rewritten, or no-longer-
authorized state fails closed. The status file is never an authority input.

## Verification

Focused tests are `4/4`. They exercise the reviewed `source -> D1 -> D2`
graph and prove that only `D1`'s terminal consumption event, together with the
exact source gate, dispatches `D2`; materialization-only state, a consumption
receipt without its event, an old rebound identity, and `I` do not authorize
the transition. They also cover post-materializer and receipt-only crash
recovery without duplicate publication. The dependent-dispatch, consumption,
source-only-dispatch, and overlay set passes `19/19`.

Ruff formatting and lint, Python compilation, strict profile loading, and
`git diff --check` pass. Two independent read-only reviews report P0=0 and
P1=0 for authority scope and durable-integrity boundaries. No training, full
scientific pipeline, or real-network run was performed.

## Next edge

Only this namespace's deeply replayed materialization event can authorize a
future versioned consumption bridge for the newly materialized dependent key.
That later bridge must bind the live ledger's exact expected-pre-head and
canonical EventSpecs before declaring the key terminal. This dispatcher itself
keeps `terminal_for_effective_key=false`,
`terminal_for_recovery_v6_key=false`, and `source_parent_terminal=false`.
