# Ootang source-derived dependent outcome consumption v1

## Scope

This leaf authority consumes one evented dependent-outcome materialization into
the canonical live ledger. It is a new sibling namespace and does not modify
the dependent dispatcher, the source-only dispatcher/consumption authority,
or recovery v6. The direct scientific model remains ConvLSTM/v4; frozen data
splits, metrics, thresholds, parameters, and conclusions are unchanged.

The positive claim is deliberately narrow: one exact current effective `D/R`
key has a canonical live-ledger transaction and a matching terminal control
event. It does not make the recovery-v6 key or source parent terminal, and it
does not claim whole-workset closure, drain, lifecycle, activation, trusted/E2
evidence, or formal-warning output.

## Eligibility and identity

The coordinator deep-replays the published effective overlay and every pinned
prerequisite of the dependent dispatcher. It then reconstructs the current
dependency-ready `D/R` set and deeply replays the dependent dispatcher's own
durable state. A key becomes eligible only when its exact dependent-dispatch
intent, receipt, and append-only event all exist and agree with the current
effective `key_id`, `natural_key`, and `namespace_digest`.

A dispatcher receipt without its event, status cache, source-only dispatcher
event, old rebound identity, invalidation row, or item outside the current
dependency-ready set cannot authorize consumption. If this namespace already
contains durable bytes and the exact predecessor leaves the eligible set,
replay fails closed rather than ignoring or rebasing the state.

The dependent materialization receipt is converted only in memory into the
pinned recovery previous-step shape. It remains a non-terminal predecessor and
is never written into recovery v6 as a fabricated receipt.

## Canonical live-ledger transaction

Before any ledger mutation, a create-only intent binds the overlay event, the
dependent dispatcher intent/receipt/event, its dependency proof and digest,
the complete effective row and transition identity, the recovery previous-step
adapter, the exact live-ledger expected pre-head, the full ordered canonical
EventSpecs, and the recovery consumption contract. The step identity also
binds the overlay event, dependent-dispatch event, dependency-proof digest,
effective key, action, and semantic step index.

The existing recovery planner/action and expected-pre-head CAS remain the only
ledger writer. Fresh execution is allowed only at the recorded pre-head. An
already committed exact contiguous slice is adopted in place. A foreign,
partial, displaced, or different suffix cannot move the create-only intent to
a new head and therefore fails closed.

## Durable protocol

The authority uses an independent
`workset_recovery_v1/source_derived_dependent_outcome_consumption_v1/`
namespace with create-only `intents/`, create-only `receipts/`, append-only
hash-chained `events/`, and cache-only `status.json`. Under the surviving
manager/cycle/replay/shadow lock order, each poll advances or heals at most one
key:

1. publish the fully bound create-only consumption intent;
2. execute or exactly adopt the canonical live-ledger CAS transaction;
3. publish a terminal receipt for only the exact effective key; and
4. append the matching terminal control event.

A post-CAS crash leaves the immutable transaction for exact adoption. A
receipt-only crash appends only the missing event. Orphan, branched, rewritten,
or competing crash frontiers fail closed.

## Verification

Focused tests are `5/5`. They use the real SQLite live ledger and cover D2
fresh CAS with the exact D1 post-consumption pre-head and full EventSpecs,
idempotent replay, post-CAS exact adoption, receipt-only event healing,
dependent-dispatch receipt-without-event denial, and retryable upstream busy
classification. The dependent-consumption/dispatch, source-only consumption/
dispatch, and overlay chain passes `24/24`.

The initial focused run found an incorrect nested overlay path before any CAS
mutation; all three references now use the dependent dispatcher's source
dispatcher overlay. Independent integrity review then found that an upstream
busy error was being wrapped as an integrity error. Both prerequisite replay
and committed-slice verification now preserve retryable busy semantics. Final
independent integrity and scope reviews report P0=0, P1=0, and P2=0.

Ruff formatting and lint, Python compilation, strict profile loading, and diff
checks pass. No training, full scientific pipeline, or real-network run was
performed.

## Next edge

Only this namespace's deeply replayed terminal event can authorize a later
versioned dependent frontier or a whole-effective-workset terminal aggregate.
Neither claim is made by this bridge.
