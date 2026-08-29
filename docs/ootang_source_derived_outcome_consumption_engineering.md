# Ootang source-derived effective outcome consumption v1

## Scope

This authority consumes one already-materialized source-derived effective `D` or
`R` outcome into the old live ledger. It is a sibling of recovery-v6 under
`workset_recovery_v1`; it is not a synthetic recovery reservation and it never
writes recovery-v6 receipts or events. The ConvLSTM/v4 model, frozen data splits,
metrics, thresholds, parameters, and scientific conclusions are outside this
control-plane increment.

The positive claim is deliberately narrow: one exact effective key has a fully
verified canonical outcome-consumption transaction at one immutable
expected-pre-head. The source parent, the corresponding recovery-v6 key, other
effective items, transition closure, drain, lifecycle, activation, trusted-time
evidence, E2 evidence, and formal-warning output remain unproved.

## Authority inputs

The coordinator first deep-replays the published effective-workset overlay and
the historical materialization dispatcher. A candidate is eligible only when:

- it is an exact current-overlay `D` or `R` row;
- the historical dispatcher has a create-only intent, matching create-only
  materialization receipt, and matching append-only event for that same key;
- the materialization event remains non-terminal and requests exactly
  `outcome_or_revision_consumed`; and
- all overlay, historical source, materializer, and frozen-cut bindings still
  replay byte-for-byte.

`I` rows are never candidates. A materialization status file, intent alone, or
receipt without its dispatcher event does not authorize live-ledger mutation.
An old frozen key cannot stand in for a rebound `R` key because the current
overlay key id and namespace digest are bound independently.

The dispatcher receipt is converted to a deterministic in-memory compatibility
view expected by the pinned recovery consumption kernel. This view is never
persisted as a recovery-v6 receipt. The real base reservation is used only as
the immutable frozen-cut and runtime-path execution context.

## Canonical live transaction

The pinned recovery planner selects exactly one existing reviewed branch:

- outstanding outcome settlement: 43 canonical events;
- first backfill: one `backfill_not_blind` event;
- settled-date revision: 16 canonical revision/rescore events; or
- backfill revision: eight canonical revision events.

The consumption intent stores the complete recovery action contract, exact
expected-pre-head, and the complete ordered canonical `EventSpec` payloads plus
their digests. These values cannot be rebased after intent publication. The
pinned `append_transaction_at_pre_head_v1` CAS is the only live-ledger writer.
The coordinator does not call the current-source selector and performs no
network action.

## Durable protocol

The namespace is
`workset_recovery_v1/source_derived_outcome_consumption_v1/` with independent
`intents/`, `receipts/`, `events/`, and cache-only `status.json` paths. Under the
surviving manager/cycle/replay/shadow lock order, each poll heals or advances at
most one key:

1. publish a create-only consumption intent;
2. execute or exactly adopt the canonical live-ledger CAS transaction;
3. publish a create-only terminal consumption receipt; and
4. append the matching hash-chained terminal event.

The step identity binds the overlay event, materialization-dispatch event,
effective key, step index `1`, and action
`outcome_or_revision_consumed`. The intent also binds the exact overlay,
materialization intent/receipt/event references, effective-row identity,
transition plan, compatibility view, recovery contract, canonical event specs,
and this implementation.

An intent-only crash retries the same immutable contract. If the exact complete
slice already exists at the recorded position, the recovery kernel adopts it
without a second append; this also covers a complete matching slice followed by
a legal foreign suffix. If a foreign suffix wins before the recorded slice, or
the slice is partial, displaced, or internally different, replay fails closed
and no terminal receipt or event is emitted. A receipt-only crash appends only
the missing event. Deleting or changing `status.json` has no authority effect.

## Terminal semantics and next edge

Only the deeply replayed event in this namespace is terminal for its exact
effective key. Both receipt and event keep
`terminal_for_recovery_v6_key=false` and `source_parent_terminal=false`.
They do not prove that all effective items are terminal.

The next increment should add a new dependent-dispatch readiness layer (or a
versioned dispatcher successor) that recognizes this exact terminal event for
effective D/R dependencies while retaining the existing cross-freeze source
gate for the source parent. It must not create a reverse profile-hash pin from
the current materialization dispatcher to this authority.

## Verification record

Focused tests exercise a real SQLite append-only live ledger and pass `4/4`:
fresh first-backfill CAS plus idempotent replay, post-CAS exact adoption,
receipt-only event healing, and denial when a materialization receipt lacks its
dispatcher event. The consumption/dispatcher/overlay adjacent set passes
`15/15`; recovery passes `57/57`. Ruff formatting/lint, Python compilation,
strict profile loading, and diff checks pass. No training, full scientific
pipeline, or real-network action was run. Three independent read-only reviews
(durable integrity, authority scope, and test realism) report P0=0/P1=0. The
only non-blocking note is that this bridge's end-to-end fixture exercises the
one-event first-backfill branch; the pinned recovery regressions retain direct
coverage for the 43/16/8-event branches.

The implementation, profile, focused test, and protected `main.py` SHA-256
values are respectively
`fb03dfa502d7402b824cec16b36698d0de6349e419feac8a34981ff9e05b0789`,
`efd33c6c3e9cb386d64cd1e44720b4019c3468a774d695ddc3f77cca3efa863b`,
`1304ec0c4f57f315b50a7a691ab3109a4cd4447c68d47c77e4cf71b273064777`,
and `02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`.
