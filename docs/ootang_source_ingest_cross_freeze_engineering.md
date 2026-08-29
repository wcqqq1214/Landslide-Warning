# Ootang cross-freeze source-ingest writer/adoption v1

## Scope

This increment adds a machine-only adapter for the single frozen-manifest parent whose canonical
successor is `source_snapshot_ingested`. It is the narrow writer authority that remains usable
after the admission cut: it never opens, replaces, removes, or re-creates the cut legacy deploy or
runner sentinel. It does not modify recovery v6. Instead, it writes a separate versioned protocol
that binds one immutable feed, one source snapshot edge, and the already versioned derived
classification event.

The adapter preserves the existing live-source transaction. The source snapshot receipt remains
the commit point; the public pointer and replaceable status remain projections. Completion in this
adapter means only that the exact source snapshot was committed and the corresponding derived
outcome batch was classified. It does not make the frozen recovery-v6 key terminal.

## Frozen authority and immutable feed

The coordinator acquires the surviving `manager -> cycle -> replay -> shadow` locks and fully
replays the manifest reservation, dependency graph, source receipt registry, and the unique frozen
source-ingest item. The item must retain its original natural key, namespace digest, transition
plan, predecessor receipt and sequence, incoming-feed digest and size, exported timestamp, and
complete changed/appended revision rows. Multiple source-ingest candidates or a changed authority
fails closed.

Before any source write, the exact manifest-bound feed bytes are copied create-only to:

```text
runtime/ootang_prequential_live_v1/
  source_ingest_cross_freeze_v1/prepared_feeds/sha256/<feed-sha256>.json
```

The copied object must reproduce the frozen digest and size. The deterministic prepare record
binds that object to the manifest and predecessor; the deterministic intent then binds the prepare,
the reviewed source profile and implementation, and the only runtime substitution permitted for
the write. A later change to the mutable incoming-feed path cannot change this slot.

## Source write and crash adoption

For a fresh `N -> N+1` edge, the adapter deep-copies the reviewed source profile and changes only
`runtime.incoming_feed` to the immutable prepared object. It calls the pinned private
`_ingest_source_locked` kernel while already holding the surviving global lock order. It never
calls the public `ingest_source` wrapper, because that wrapper would attempt to acquire the legacy
deploy sentinel closed by the admission cut.

The original source write order is unchanged:

```text
content objects
-> revision receipts
-> pointer object
-> source snapshot receipt       # commit point
-> public current pointer
-> replaceable status
```

Pointer recovery is not unconditional. A missing or verified-stale public pointer may be repaired
only after this adapter has replayed the exact slot's immutable feed, prepare, and durable writer
intent. Without that exact intent, a receipt-before-pointer state fails closed and the adapter does
not mutate the public pointer. After an authorized recovery, the source registry and exact child
are replayed again through the normal source and derived validators.

If the public source is already `N+1`, or has advanced to `N+2+`, the writer is not called. The
adapter adopts the immutable historical `N+1` child of the frozen predecessor and never rolls the
public pointer back. The exact child, feed, revision diff, and source id are verified by the pinned
derived-reservation authority rather than inferred from the current status cache.

## Derived-classification barrier

After the source commit/adoption boundary, the coordinator releases all four locks and invokes the
existing source-ingest derived-reservation coordinator. Releasing first avoids nested acquisition
of the same global lock order. It then re-acquires all locks, reloads the frozen slot, and requires
the prepare and intent snapshots to be unchanged.

Completion requires the matching published derived event, not a status file or an orphan
reservation object. The existing derived replay verifies the exact predecessor and successor
receipts, source key, manifest, complete `D/R/I` classification, reservation object, append-only
event chain, and slot id. Any other slot, branch, changed event, or unpublished object remains
pending or fails closed.

If the source receipt is committed while the derived event is still pending, the machine result
and status preserve `source_snapshot_ingested=true` and the exact N+1 sequence while keeping
`derived_batch_classified=false`. A competing derived poll that owns the lock remains a retryable
busy result, not an integrity failure.

## Durable completion protocol

The adapter writes only below:

```text
runtime/ootang_epoch_registry_v1/workset_recovery_v1/
  source_ingest_cross_freeze_v1/
    prepares/<slot-id>.json
    intents/<slot-id>.json
    receipts/<slot-id>.json
    events/<sequence>-<entry-sha256>.json
    status.json
```

The completion receipt is canonical and create-only. It binds the prepare, intent, committed source
snapshot receipt, source pointer object, semantic manifest, derived reservation object, and derived
event. The event is append-only and hash-linked. A crash after the receipt but before the event is
recovered by validating the exact receipt and appending only the missing event; the source writer
and derived coordinator are not repeated. Namespace branches, orphan completion bytes, changed
receipt semantics, or mismatched source/derived authorities fail closed. `status.json` is a
replaceable observation cache and never authority.

The only positive outcome claims are:

```text
source_snapshot_ingested = true
derived_batch_classified = true
```

The receipt and event explicitly retain:

```text
terminal_for_recovery_v6_key = false
```

They also expose whether rebound (`R`) or invalidation (`I`) work is required, but do not claim that
either overlay has been applied.

## Explicit non-claims

This adapter creates no recovery-v6 receipt or event, does not reactivate a legacy writer, and does
not materialize or consume an outcome. It does not reserve all future derived lanes, apply `R/I`
overlays, prove terminal/transitive closure, settle every workset item, or establish drain,
lifecycle, activation, trusted-anchor, E2, or formal-warning authority.

ConvLSTM, v4, frozen validation splits, metrics, thresholds, model parameters, and scientific
conclusions are unchanged. No training or real-network experiment is part of this increment.

## Verification

The focused suite covers fresh machine ingest, mutable-inbox TOCTOU after durable intent,
receipt-before-pointer crash adoption, rejection of pointer recovery without the exact adapter
intent, completion receipt/event forward adoption, and historical `N+1` adoption while the public
tip is `N+2`. It also verifies truthful source/derived state during the publication gap and
retryable derived-lock contention. Adjacent regression replays the source-derived reservation,
inventory, manifest, recovery, and live-source contracts. Static checks include Ruff, Python
compilation, strict profile loading, upstream SHA-256 pins, and explicit staged-diff review.

## Next authority boundary

The next narrow increment should construct a versioned effective-workset overlay from the
published derived `D/R/I` batch. It must add `D`, explicitly replace `R`, explicitly supersede `I`,
and recompute the effective dependency graph without rewriting the frozen manifest or recovery-v6
bytes. Only after that authority exists should the existing machine materialization and
consumption path be extended to the new effective items.
