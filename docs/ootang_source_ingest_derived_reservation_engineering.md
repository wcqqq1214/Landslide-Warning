# Ootang source-ingest derived-key reservation v1

## Scope

This increment adds a machine-only, read-only adoption authority for outcome keys whose exact
identity becomes visible after one frozen incoming feed is committed as the next source snapshot.
It does not execute source ingestion. It accepts only an immutable frozen-manifest
`source_snapshot_ingested` parent and its unique immutable `N+1` child in the fully replayed
source-snapshot receipt chain, then publishes the complete derived outcome-item batch in its own
versioned namespace. The current chain tip may already be a later descendant; that does not erase
or reinterpret the exact historical edge.

The source snapshot receipt is the source commit point. The replaceable source status and public
pointer are not authority by themselves. The public pointer must match the unique current
receipt-chain tip so the registry as a whole remains valid, while the adopted `N+1` pointer and
feed are replayed from their immutable content-addressed objects. A revision receipt, prospective
object, mutable incoming feed, or public pointer alone cannot trigger a reservation.

## Authority edge

The assessor runs under the surviving `manager -> cycle -> replay -> shadow` lock order. It
deep-replays the immutable workset-manifest reservation and reconstructs its canonical key ids and
dependency graph. Exactly one eligible parent must have:

- family `outcome_revision` and successor `source_snapshot_ingested`;
- action `ingest_incoming_finalized_feed`;
- the frozen predecessor snapshot sequence and receipt digest;
- the exact incoming-feed artifact and declared changed/appended revision rows.

The live-source registry is independently replayed and must have one valid current tip whose public
pointer matches that tip. Within that unique chain, exactly one receipt must be the frozen
predecessor's `N+1` child: its sequence advances by one, predecessor receipt matches byte-for-byte,
and source id is unchanged. Both predecessor and child are reconstructed from immutable receipt,
pointer, semantic-manifest, dataset, revision-head, and feed objects. The actual edge diff must
exactly equal the rows declared by the frozen parent. A later `N+2` or newer descendant is allowed
and does not invalidate an already published sidecar; a skipped child, branch, rollback, duplicate
edge, different immutable feed, public pointer that disagrees with the registry tip, or changed
revision semantics fails closed.

This authority does not depend on frozen-manifest terminal coverage: doing so would create a cycle,
because the source parent is intentionally nonterminal until its derived work is reserved and later
consumed.

## Complete derived batch

Derived items are rebuilt from the committed successor source and the frozen old-ledger prefix,
never from a later mutable selector or materializer status. The ordering and natural-key formula
reuse the frozen inventory contract:

```text
outcome_revision:<old-epoch>:<target>:<source-revision>:<seal-or-zero>:<source-id>
```

The batch includes every immediately enumerable revision, outstanding, or contiguous backfill
obligation selected by that exact snapshot/frozen-prefix pair. Each row binds the source record,
selection kind, prior revision/outcome identity when applicable, source-snapshot receipt, frozen
issue seal, canonical successor `outcome_materialized`, artifact obligations, and dependencies.
Dependencies may resolve only to the frozen manifest or another item in the same derived batch;
every derived item also retains the source-ingest parent edge. Duplicate keys, missing/unknown
dependencies, or a partial changed-row mapping fails closed.

The entire ordered set is one atomic reservation object. Later outcome-materializer receipts are
not inputs to its identity and cannot rewrite it; they are progress evidence for later dispatch and
consumption authorities.

The assessor also records three disjoint audit sets. Let `P1` be the prospective machine-selected
items rebuilt for the successor snapshot and `K0` the frozen machine-selected outcome natural-key
map:

```text
D = {p in P1 | natural_key(p) not in K0}
R = {p in P1 | natural_key(p) in K0 and namespace_digest(p) changed}
I = {old frozen machine-selected k | k not in P1}
```

Only `D` is newly reserved by this authority. `R` identifies an existing natural key whose
snapshot/artifact binding must be replaced by a later explicit overlay, while `I` identifies a
frozen candidate superseded by the new source revision. Both are bound into the batch, with exact
counts and digests, but are not silently treated as complete. This distinction prevents an old
manifest key from passing a natural-key-only comparison even though its materialization adapter
would reject the new snapshot receipt.

## Durable protocol

The sidecar writes only below:

```text
runtime/ootang_epoch_registry_v1/workset_recovery_v1/
  source_ingest_derived_reservation_v1/
    reservations/sha256/<reservation-sha256>.json
    events/<sequence>-<entry-sha256>.json
    status.json
```

The reservation object is canonical, content-addressed, create-only, and contains no poll time.
Publication requires the matching append-only, previous-hash-linked event. An object left before
its event is deeply reconstructed and forward-adopted by appending only that event; source
ingestion, selection, materialization, and ledger actions are not repeated. Event-without-object,
non-prefix order, a branch, two objects for one source edge, or more than one pending object fails
closed. Historical `N+1` reconstruction remains byte-identical after the public source advances,
because its pointer/feed bindings come from the immutable receipt chain. This read-only adoption
does not authorize or execute a source writer. `status.json` is a replaceable observation cache and
never authority.

## Explicit non-claims

The narrow published fact is only that the exact outcome-key batch for one committed source edge
has been reserved. This does not implement the cross-freeze source-ingest writer, materialize or
consume an outcome, make the original recovery-v6 parent terminal, or establish general derived
future-work coverage. Issue-route and shadow-derived lanes remain separate.

Full/bounded-workset recovery, all-successor support, terminal/transitive closure, drain
eligibility, lifecycle transition, old-epoch drained state, active switching, rotation, trusted
anchoring, E2 evidence, activation readiness, and formal warning output all remain false. ConvLSTM,
v4, frozen validation splits, metrics, thresholds, model parameters, and scientific conclusions
are unchanged.

## Next authority boundary

The next increment should add a separately versioned cross-freeze source-ingest writer/adoption
protocol. It must bind an intent to this deterministic source edge, create only the exact source
objects allowed by the frozen parent, adopt crashes at the source receipt/public-pointer boundary,
and require the matching derived reservation event before treating the parent transition as
resolved. It must not reactivate the cut legacy deploy writer or add a human freeze/approval path.
