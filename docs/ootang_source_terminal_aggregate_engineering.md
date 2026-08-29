# Ootang source-key terminal aggregate v1

## Scope

This increment adds a read-only assessor for one source key whose frozen recovery-v6 chain could
not persist its final `outcome_batch_settled` receipt because the required outcome dependency was
reserved after the manifest freeze. The assessor combines the unchanged recovery-v6 source tip,
the completed step-dependency sidecar event, and the completed settlement-overlay event into an
independent source-terminal proof.

The proof belongs only to `source_terminal_aggregate_v1`. It is not written into the recovery-v6
receipt namespace and does not reinterpret the persisted v6 receipt as terminal. It performs no
network, live-ledger, model, manifest, sidecar, or overlay mutation.

## Pinned authority

The aggregate profile directly pins the implementation and profile SHA-256 values of all three
upstream layers:

- frozen workset recovery v6;
- cross-freeze step-dependency sidecar v1;
- settlement overlay v1.

The assessor takes the same manager, cycle, replay, and shadow locks. Under those locks it invokes
the overlay authority loader and state replay, which in turn deep-verifies recovery, sidecar, and
overlay intents, receipts, and hash-linked events. Only an overlay receipt with its exact completed
overlay event is eligible. A pending overlay intent or receipt is not terminal authority and is not
completed by this assessor.

## Terminal derivation

For an eligible slot, the stored sidecar candidate must still point to the current recovery tip of
the frozen source key. The source must be a `live_outstanding` item whose latest receipt is:

- `action=anchor_result_recorded`;
- `result_outcome=candidate_confirmed`;
- `next_actions=[outcome_batch_settled]`;
- `terminal_for_key=false`.

Its source receipt/event and the dependency's current terminal recovery receipt/event must exactly
match the references frozen in the sidecar and overlay. The sidecar step index must be one greater
than the source tip, and its step id must equal recovery v6's canonical step-id derivation.

The original source transition plan, the sidecar source-step plan hash, and the overlay effective
item plan hash must agree. Recovery v6 defines `outcome_batch_settled` as a no-successor terminal
action for a closure-resolved `live_outstanding` item. The aggregate proof therefore records
`terminal_for_source_key=true` and `next_actions=[]` after verifying the completed overlay's exact
settlement action semantics and all source/dependency bindings.

The transition-plan digest does not include manifest dependency keys. The proof consequently also
binds the complete sidecar object/event and overlay intent/receipt/event references, including the
effective dependency edge and recovery settlement-contract digest. This prevents the terminal
formula from being used without its post-freeze dependency evidence.

## Durable protocol

The minimum durable protocol is:

```text
workset_recovery_v1/source_terminal_aggregate_v1/
  proofs/<proof-sha256>.json
  events/<sequence>-<entry-sha256>.json
  status.json
```

Proof payloads are deterministic canonical JSON without a creation timestamp and are stored by
their own SHA-256. Aggregate events are append-only, hash-linked, and use the corresponding
completed overlay event sequence. Published aggregate events must form an exact prefix of completed
overlay events.

At most one proof may lack an aggregate event, and it must belong to the next overlay slot. If a
crash occurs after proof publication, the next poll deeply verifies that exact proof and appends
only its event. It does not reselect a slot or rerun an upstream action. A proof becomes published
terminal authority only after its aggregate event is durable.

Event-without-proof, multiple proofs for one slot, multiple orphan proofs, future/skip slots, two
overlay slots for one source key, non-prefix events, branches, or any reference/content/path/hash
change fail closed. `status.json` remains a replaceable non-authoritative cache.

## Explicit non-claims

The proof establishes only one source key's terminal state under the aggregate-v1 authority. The
original recovery-v6 receipt remains nonterminal and all v6 bytes remain unchanged. This increment
does not prove bounded or full-workset recovery, all-item/all-successor support, terminal or
transitive workset closure, derived new-key reservation, drain eligibility, old-epoch drained
state, lifecycle/activation/rotation authority, trusted anchoring, E2 live evidence, real
activation readiness, or formal warning output.

ConvLSTM, v4, frozen validation splits, metrics, thresholds, model parameters, and scientific
conclusions are outside this increment and remain unchanged.

## Next authority boundary

The next increment may consume published aggregate events together with the unchanged v6 terminal
receipts to assess the frozen manifest's key coverage. It must separately reserve content-dependent
new keys from source ingestion before it can claim terminal/transitive closure or drain eligibility.
