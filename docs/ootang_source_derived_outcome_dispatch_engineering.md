# Ootang source-derived outcome dispatch v1

## Scope

This increment adds a machine-only, materialization-only dispatcher for the published
source-derived effective-workset overlay. It advances at most one ready effective `D` or `R`
outcome per poll. It never dispatches `I`, never rewrites the frozen manifest, never constructs a
synthetic `recovery.Reservation`, and never writes a recovery-v6 receipt or event.

The output of this increment is deliberately non-terminal. A successful dispatcher receipt proves
that one exact effective key's outcome was materialized or adopted from the canonical immutable
materializer chain and records `next_action=outcome_or_revision_consumed`. Consumption, live-ledger
CAS, and effective-key terminalization belong to the next separately versioned authority.

No human freeze, approval, cleanup, force, or backdated operation exists in this path. ConvLSTM,
v4, frozen validation splits, metrics, thresholds, model parameters, and scientific conclusions are
outside this control-plane change and remain unchanged.

## Published authority and historical source boundary

Before creating an intent, the coordinator deep-replays the immutable frozen reservation, the
source-derived `D/R/I` reservation and event, the exact cross-freeze completion receipt and event,
and the compact effective overlay object and publication event. Status files are replaceable caches
and never satisfy this barrier.

The eligible set is the intersection of the reconstructed effective workset and the published
`D ∪ R` identities. Retained base rows are not re-dispatched here, and an invalidated `I` row is
never eligible. The stable effective topological order determines selection; one poll can create at
most one new step.

The existing recovery materialization planner cannot be used for these rows because it reloads the
public current source and runs the current selector. When the public tip is `N+2+`, that would
replace the reserved historical `N+1` authority. This dispatcher instead uses the exact historical
successor source reconstructed by the published derived authority and directly builds the selected
record, input manifest, outcome object, and materializer receipt contract. It never calls the
current-source selector.

The real frozen base `Reservation` is passed only as immutable execution context for frozen live
cut verification and active-runtime paths. The standalone effective row remains admitted by the
published overlay; it is not inserted into or presented as a recovery-v6 manifest.

## Historical artifact verification

Every materialized row must be an `outcome_revision` whose successor is `outcome_materialized` and
whose authority is the exact machine-selected source-outcome shape. Target date, revision,
selection kind, predecessor revision/outcome, outcome source id, snapshot sequence and receipt,
export time, frozen epoch, and frozen issue seal must match the historical source and frozen live
prefix.

The source-derived `current_source_pointer` obligation has intentional historical semantics: its
path is the public logical pointer path, while its digest and size are the immutable historical
pointer object's bytes from the `N+1` snapshot receipt. Verification therefore compares that role
to the historical pointer object and does not hash, replace, or roll back the public `N+2+` pointer.
Activation manifest, semantic manifest, target revision head, and snapshot receipt are matched
exactly to the historical source. The materializer input manifest additionally binds the canonical
dataset and scientific-semantics digest.

## Readiness and identity rules

The matching source dependency is accepted only as a narrow source-expansion barrier formed by the
cross-freeze completion receipt/event plus the published overlay event. This does not make the
source parent terminal and does not create a recovery-v6 receipt for it.

This v1 materialization bridge admits only a first effective `D/R` row whose dependencies are the
exact source parent. A later effective row depending on an earlier `D/R` waits, because a
materialization receipt is non-terminal and cannot satisfy that edge. The next consumption
authority will supply the exact effective-key terminal receipt/event required to unlock the
successor. The dispatcher does not weaken this rule by using a materialization event as terminal
evidence.

An `R` row is always addressed by its new namespace-bound effective key id. A receipt for the old
frozen key cannot settle, skip, or unlock the replacement. An existing canonical materializer
receipt at the same target/revision may be adopted only if its complete input, outcome, predecessor
link, sequence, and receipt bytes equal the new historical contract. A different receipt is a
conflict, not idempotence. `I` evidence is retained upstream for audit but never becomes a local
intent or completion record.

## Durable protocol

The dispatcher's own control records are written only below its namespace:

```text
runtime/ootang_epoch_registry_v1/workset_recovery_v1/
  source_derived_outcome_dispatch_v1/
    intents/<step-id>.json
    receipts/<step-id>.json
    events/<sequence>-<entry-sha256>.json
    status.json
```

The single allowed external side effect is the pinned canonical materializer writer. It may create
content-addressed input/outcome objects and the immutable outcome receipt, then reconcile that
receipt to the canonical active-receipt pointer and outcome inbox when it is the unique chain tip.
The dispatcher does not implement a second outcome writer and permits no source, frozen-manifest,
recovery-v6, overlay, derived, cross-freeze, or live-ledger mutation.

The step id is the canonical digest of the overlay publication event, effective key id, step index
zero, and `outcome_materialized` action. The durable order is:

```text
create-only intent
  -> canonical materializer commit or exact adoption
  -> create-only dispatcher receipt
  -> append-only dispatcher event
```

The intent binds the overlay object/event, effective identity, `D` or `R` origin, complete item
digest, dependency evidence, and nested exact materializer action contract. The canonical
materializer receipt remains the action commit point. The dispatcher receipt binds that immutable
postcondition to the new effective key and explicitly records
`terminal_for_effective_key=false` and `terminal_for_recovery_v6_key=false`.

Contract construction has two explicit branches. If the candidate receipt already exists, its
actual immutable chain member fixes the sequence and previous-receipt link, and its receipt, input
manifest, outcome object, and payload bytes must all equal the historical plan; it may be an
interior member. If it does not exist, a first selection requires an empty chain, while a revision
requires its declared predecessor to be the current unique tip. The dispatcher never inserts a
new receipt into the middle of a history.

An intent-only crash reconstructs the same historical contract and either performs the action or
adopts its exact canonical receipt. A materializer-commit-before-dispatcher-receipt crash therefore
does not publish a second outcome. A receipt-only crash deep-verifies the exact intent and immutable
postcondition, then appends only the missing event. If the adopted receipt is already an interior
member of a later valid materializer chain, replay verifies its unique membership and the legal
current pointer/inbox state but must not reconcile either mutable file back to that historical
member. A receipt without its exact intent, an event without its exact receipt, changed intent or
receipt bytes, a second pending step, identity drift, or a branched event chain fails closed.

`status.json` is an observation cache. Removing or changing it cannot authorize an action.

## Claims and non-claims

The narrow positive claims are machine-only operation, deep verification of the published
effective overlay, exact historical `N+1` binding, exact `D/R` effective identity, source-expansion
readiness, deterministic create-only intent/receipt, reuse of the pinned canonical materializer
writer, one ready item per poll, and crash-forward adoption. A successful receipt/event may state
that its exact outcome materialization was performed or adopted.

The authority does not claim outcome consumption, effective-key terminal state, source-parent or
recovery-v6 terminal state, invalidation terminalization, all-item completion, transitive closure,
drain eligibility, lifecycle transition, activation, epoch switching/rotation, trusted anchoring,
E2 evidence, real activation readiness, network action, or formal-warning output. It does not
mutate the frozen manifest, recovery v6, the source-derived reservation, cross-freeze authority, or
the published overlay.

## Verification contract

Focused verification covers normal `D` materialization without the current selector, exact
historical `N+1` materialization while the public pointer is already `N+2+`, preservation of all
upstream authority bytes, materializer-commit crash adoption without duplicate publication, and
new-key binding for an `R` replacement. A missing overlay publication must wait without creating an
intent. Static checks include strict profile loading and direct implementation/profile SHA pins,
Ruff, Python compilation, and diff checks. Adjacent regressions cover the overlay, cross-freeze,
derived reservation, recovery, materializer, and live-source contracts; no training or full
scientific pipeline is required for this control-plane increment.

The focused suite passes `5/5`. A second poll after successful publication is included in the
normal case and must replay as current with exactly one intent, receipt, and event. This regression
closed an independently reproduced event-replay defect in the initial implementation: the replay
path had computed `entry_sha256` with an empty timestamp while comparing it to the persisted event.
Replay now parses the persisted UTC timestamp, reconstructs the complete event with that value, and
requires exact payload, hash-chain, and filename equality. A second independent P1 found that
interior receipt adoption verified the immutable member but not the mutable current outcome inbox;
the historical plan now requires a legal current chain pointer/inbox before adopting an interior
member and still performs no historical reconciliation. Both final reviews report P0=0/P1=0.
Adjacent tests pass `28/28` for the new dispatcher/overlay/cross-freeze/derived path and `120/120`
for recovery/materializer/live source. Ruff formatting and lint, Python compilation, strict profile
loading with six direct SHA pins, and `git diff --check` pass.

## Next authority boundary

The next increment should consume the materialized effective item through a separate deterministic
intent bound to the live ledger's exact expected pre-head and canonical event-spec digest. Its CAS
commit/adoption receipt must be keyed to the new effective identity and may then set
`terminal_for_effective_key=true` while continuing to keep
`terminal_for_recovery_v6_key=false`. Only that terminal receipt/event may unlock a dependent
source-derived outcome.
