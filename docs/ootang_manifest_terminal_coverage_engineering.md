# Ootang frozen-manifest terminal coverage v1

## Scope

This increment adds a read-only assessor for the exact key set of one immutable
frozen-observation manifest. It combines ordinary recovery-v6 terminal receipts with published
source-terminal aggregate-v1 events. It does not add keys to the manifest, modify any upstream
authority, or reinterpret an aggregate proof without its matching event as published authority.

The result is deliberately named frozen-manifest key coverage. It is not full-workset recovery,
terminal/transitive transition closure, drain eligibility, or lifecycle authority. In particular,
content-dependent work created by source ingestion, issue-route replay, or shadow transitions may
still be outside the frozen manifest.

## Pinned and replayed authority

The coverage profile directly pins the implementation and profile bytes of the frozen manifest,
recovery v6, cross-freeze dependency sidecar, settlement overlay, and source-terminal aggregate
layers. The assessor takes the surviving manager, cycle, replay, and shadow locks in that order.
Under those locks it calls the aggregate authority and state loaders, which recursively replay the
manifest reservation, recovery global intent/receipts/events, sidecar objects/events, overlay
intents/receipts/events, and aggregate proofs/events.

The assessor independently reconstructs the manifest key ids and deterministic topological order
from the durable manifest bytes and requires an exact match with the recovered ordered items. It
also strictly re-reads every referenced manifest/global/receipt/event/proof snapshot before using
its in-memory payload. This prevents an injected or stale authority view from shrinking the key
set or turning a nonterminal receipt into terminal evidence.

The assessor never calls an upstream coordinator, selector, ensure function, settlement action,
network transport, or ledger writer. Its writes are confined to its own versioned namespace.

## Exact coverage formula

Let `K` be the ordered frozen manifest key-id set. For every key, only its current recovery-v6
receipt-chain tip may be considered.

`T6` contains a key only when that tip has `terminal_for_key=true` and the exact matching recovery
event is present in the deeply replayed recovery event chain. A terminal receipt without its event
does not count.

`TA` contains a source key only when a source-terminal aggregate event is present in the deeply
replayed aggregate event chain. The event must reference the exact content-addressed aggregate
proof rebuilt from the current recovery, sidecar, and overlay authority. Completed overlay slots,
proof objects, and a pending/orphan proof are not members of `TA`.

Coverage is complete exactly when:

```text
K = T6 ⊎ TA
```

The union is disjoint and exact. Unknown keys, duplicate evidence, evidence from both authorities
for one key, a missing current-tip event, or an extra aggregate source fails closed. A valid but
uncovered manifest key yields a waiting status and no partial coverage proof.

## Coverage proof

The deterministic proof binds the immutable manifest and reservation event, recovery global
intent, workset keyset, dependency graph and transition-plan identities, ordered key ids, and the
complete ordered evidence row for every manifest key. Each row records its manifest position,
key/natural-key/namespace/family identity, transition-plan hash, authority kind, and exact
receipt/proof plus event references.

The proof separately records ordinary, aggregate, and total covered counts and their ordered
identity/evidence digests. Count equality is diagnostic only; the ordered row-by-row bijection is
the authority.

Before publication the assessor also requires the upstream aggregate layer to have no pending
proof, every completed overlay event to have its matching aggregate event, and no incomplete
overlay or sidecar authority. This prevents a coverage proof from racing an upstream publication
boundary.

## Durable protocol

The authority is a singleton for the one frozen manifest:

```text
workset_recovery_v1/manifest_terminal_coverage_v1/
  proofs/<proof-sha256>.json
  events/00000000000000000001-<entry-sha256>.json
  status.json
```

The proof is canonical content-addressed JSON without a timestamp. The event is canonical,
hash-linked from the all-zero predecessor, and contains the publication time. The fact
`frozen_manifest_key_coverage=true` is published only after the matching event is durable.

If a crash occurs after proof publication, the next poll rebuilds and verifies that exact proof
and appends only the missing singleton event. It does not publish a different proof or run any
upstream action. Event-without-proof, more than one proof or event, a different rebuilt proof, or any
reference/content/path/hash change fails closed. `status.json` is a replaceable non-authoritative
cache.

## Explicit non-claims

This authority does not establish bounded/full-workset recovery, all-reserved-item settlement,
all-successor support, terminal/transitive closure, derived future-work reservation, all transition
branches, network recovery, drain eligibility, old-epoch drained state, lifecycle/transition
authority, active switching, rotation, trusted anchoring, E2 evidence, activation readiness, or
formal warning output.

ConvLSTM, v4, frozen validation splits, metrics, thresholds, model parameters, and scientific
conclusions remain unchanged.

## Next authority boundary

The next narrow increment should reserve content-dependent keys created by source ingestion under
its own versioned create-only object/event protocol. Issue-route and shadow-derived work remain
separate later boundaries. Frozen-manifest coverage must not be promoted to terminal/transitive
closure or drain eligibility until those derivation lanes are exhaustively reserved and consumed.
