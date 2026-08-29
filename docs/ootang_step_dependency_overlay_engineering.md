# Ootang cross-freeze settlement overlay v1

## Scope

This increment adds a versioned dispatcher for one already-reserved cross-freeze dependency. It
consumes the completed `step_dependencies_v1` sidecar event, constructs an effective source item
in memory, and asks the frozen recovery v6 settlement-adoption verifier to re-check the exact
already-existing settlement transaction. It does not append live-ledger events and it does not
modify recovery v6, the frozen workset manifest, or the dependency sidecar.

The overlay is intentionally a narrow bridge. A completed overlay receipt is terminal only for its
overlay slot. It is not a terminal recovery-v6 key receipt and does not by itself prove workset
closure, drain eligibility, lifecycle transition, active-epoch switching, or automatic rotation.

## Pinned authorities and namespace

The overlay profile directly pins both implementation and profile SHA-256 values for:

- `ootang_epoch_workset_recovery.py` / `ootang_epoch_workset_recovery.v1.json`;
- `ootang_epoch_step_dependency_reservation.py` /
  `ootang_epoch_step_dependency_reservation.v1.json`.

The dispatcher takes the same manager, cycle, replay, and shadow locks used by the existing
machine coordinator. While holding those locks it deep-replays the frozen recovery authority and
the complete sidecar object/event chain. Its only durable writes are under:

```text
workset_recovery_v1/step_dependency_overlay_v1/
  intents/<slot-id>.json
  receipts/<slot-id>.json
  events/<sequence>-<entry-sha256>.json
  status.json
```

`status.json` is a replaceable observation cache. Intents and receipts are create-only canonical
JSON objects; events form an append-only hash chain.

## Ordered consumption rule

Only a sidecar reservation with its exact published sidecar event is eligible. Sidecar objects
without events remain external wait states. Overlay completion events must form an exact prefix of
the sidecar event sequence; the dispatcher never skips, reorders, or reselects a candidate. The
next eligible slot is therefore the sidecar event at index `len(completed overlay events)`.

The stored sidecar candidate supplies the source item, source receipt/event, dependency item, and
terminal dependency receipt/event. The dispatcher does not call the sidecar selector again.

## Settlement verification

The dispatcher copies the frozen source manifest item in memory and adds exactly the reserved
dependency natural key to that copy's `dependency_keys`. It then builds the frozen recovery-v6
`outcome_settlement_adoption` contract and cross-checks its target, issue, seal, source revision,
outcome batch, outcome source, and terminal event against the sidecar reservation.

The nested recovery dependency keeps recovery v6's record-type semantics: an
`outcome_receipt_chain` binds the exact outcome batch and leaves its nested source id null, while a
`machine_selected_source_outcome` binds the source id and leaves its nested batch hash null. The
settlement contract's top-level fields still bind the complete durable batch/source identity in
both cases. Treating both nested optional fields as simultaneously present would reject every real
v6 record and is therefore forbidden.

The recovery-v6 adoption action is read-only for this branch: it reconstructs and validates the
same 43-event settlement slice already present in the live ledger. The overlay receipt binds:

- the sidecar reservation object and completed sidecar event;
- the frozen source receipt/event;
- the terminal dependency receipt/event;
- the in-memory effective dependency key;
- the recovery-v6 settlement contract and its digest;
- the resulting read-only action semantics.

No synthetic manifest item is persisted and no new settlement transaction is created.

## Crash-forward protocol

The durable order is `intent -> receipt -> event`.

1. If an intent exists without a receipt, the dispatcher reconstructs the exact effective item,
   verifies the persisted contract byte-for-byte, repeats the read-only adoption verification, and
   publishes the matching receipt.
2. If a receipt exists without an event, the dispatcher verifies the intent and receipt and only
   appends the missing overlay event. It does not rerun settlement adoption or select a candidate.
3. If the full event exists, a later poll only replays and verifies it.

Receipt-without-intent, event-without-receipt, multiple pending intents, multiple missing events,
non-prefix consumption, branch/fork events, or any content/hash mismatch fail closed.

## Explicit non-claims

This v1 overlay establishes only machine-readable cross-freeze settlement adoption for one ordered
sidecar slot. The following remain false: original recovery-key terminality, bounded full-workset
recovery, all-successor support, terminal/transitive closure, derived new-key reservation,
drain/lifecycle/activation authority, network exactly-once, trusted-anchor verification, E2 live
evidence, real activation readiness, and formal warning output.

ConvLSTM, v4, frozen validation splits, metrics, thresholds, model parameters, and scientific
conclusions are outside this increment and remain unchanged.

## Next authority boundary

The next increment must be a separately versioned aggregate assessor or recovery-v7 authority that
can consume the overlay receipt when determining source-key terminality. Source-ingest reservation
for genuinely new outcome keys follows separately. Neither step may infer closure or drained state
from the overlay event alone.
