# Ootang source-derived effective-workset overlay v1

## Scope

This increment adds a machine-only authority that applies one already published source-derived
`D/R/I` batch to the immutable frozen workset. It publishes a separate effective-workset overlay;
it never rewrites the frozen manifest, its reservation/event, recovery v6, or either upstream
source-ingest protocol. No human freeze, approval, cleanup, force, or backdated operation exists in
this path.

The overlay is an identity and dependency authority only. It does not impersonate a
`recovery.Reservation`, invoke recovery-v6 outcome settlement, dispatch a materializer, consume an
outcome, or make the frozen source parent terminal.

## Required published authority

The coordinator independently deep-replays the frozen manifest and its published reservation
event, the content-addressed source-derived reservation object and matching append-only event, and
the matching cross-freeze completion receipt and completion event. The cross-freeze pair must bind
the same source key, source-snapshot edge, derived slot, derived reservation, and derived event.
Replaceable status files are observations only and cannot satisfy this barrier.

The complete live-source receipt registry is replayed read-only. Its unique public current pointer
must equal the current registry tip, even when the adopted source edge is historical `N -> N+1`
and the public tip has advanced to `N+2+`. The overlay uses its own pure-read cross-freeze replay
instead of the upstream loader's writer-adoption path, then rechecks the pointer against the
registry tip. It therefore never calls pointer recovery or mutates a pointer: a missing, stale,
branched, or contradictory pointer fails closed here and remains the responsibility of the source
writer/adoption protocol.

## Deterministic overlay transform

Let `M0` be the full frozen manifest item map by natural key. The published derived object supplies
three complete, pairwise-disjoint sets:

- `D`: new prospective rows whose natural keys do not occur in `M0`;
- `R`: prospective rows whose natural keys occur in `M0` but whose full namespace-bound identity
  changed;
- `I`: exact old frozen rows that no longer occur in the successor prospective set.

The effective map is constructed in this order:

```text
Meff = M0 - I
Meff[R.natural_key] = full R row
Meff += D
```

Every `I` row must exactly match its old frozen row. Every `R` row must identify exactly one old
frozen natural key, replace that item as a whole, and produce a different namespace-bound key id;
an old receipt keyed by the replaced identity cannot settle the new row. Every `D` key must be
absent from `M0`. Counts, identity-set digests, slot identifiers, and complete rows must agree with
the deeply replayed derived object. Duplicate keys, overlaps, partial rows, natural-key-only
substitution, missing old rows, or an unchanged rebound identity fail closed.

Invalidation is bound as an immutable audit-tombstone digest in this overlay; its complete old row
remains in the referenced source-derived object. The implementation does not delete upstream
evidence and does not invent a successor transition or mark an `I` item terminal in recovery v6.

## Effective identity and dependency graph

For every surviving, replaced, or added row, the coordinator recomputes and verifies the namespace
digest, then recomputes the effective key id against the frozen manifest digest. It publishes both
a natural-key-set digest and a separate identity-set digest over canonical
`{natural_key, namespace_digest, key_id}` rows. The latter ensures an `R` replacement cannot
collapse into its old frozen identity. The complete effective dependency graph is rebuilt from
`Meff` in stable family/natural-key order and topologically sorted; it is not inherited by patching
the frozen graph digest.

Every dependency must resolve to an effective natural key. In particular, a surviving base row
may not retain an edge to an `I` row. Unknown dependencies, retained-to-invalidated edges,
self-dependencies, duplicate dependencies, and cycles fail closed before publication. The overlay
does not guess a replacement edge, drop a dependency automatically, or otherwise repair graph
semantics.

## Durable protocol

The coordinator writes only below its separately versioned namespace:

```text
runtime/ootang_epoch_registry_v1/workset_recovery_v1/
  source_derived_workset_overlay_v1/
    overlays/sha256/<overlay-sha256>.json
    events/<sequence>-<entry-sha256>.json
    status.json
```

The overlay object is canonical, compact, content-addressed, create-only, and contains no poll
time. It references the frozen manifest, published derived reservation/event, matching cross-freeze
completion receipt/event, natural-key-set digest, effective identity-set digest, effective
dependency-graph digest, and auditable `D/R/I` application-mapping digests rather than duplicating
the full effective workset; `effective_rows_embedded=false` makes that boundary explicit.

Publication requires its singleton append-only event. If a crash leaves the exact overlay object
before the event, a retry deeply reconstructs the same object and appends only the missing event.
It does not repeat source ingestion or mutate any upstream authority. Event-without-object, an
unexpected second object or event, changed object bytes, mismatched upstream references, or a
competing slot/branch fails closed. `status.json` is a replaceable cache and never authority.

## Claims and non-claims

The positive claim is limited to deterministic construction and publication of the effective
source-derived workset for one exact completed source-snapshot edge: `D` was added, `R` was fully
replaced, `I` was superseded in the overlay, and the effective keyset and dependency DAG were
rebuilt and validated. Within this narrow source-outcome lane the profile therefore sets
`effective_workset_overlay_implemented=true` and
`derived_future_work_reservation_implemented=true`; it still keeps
`all_content_dependent_lanes_reserved=false`.

This does not mutate the frozen manifest or recovery v6, establish recovery-v6 terminal receipts,
materialize or consume an effective item, prove full/bounded-workset or transitive closure, settle
all successors, or authorize drain, lifecycle transition, activation, switching, rotation,
trusted anchoring, E2 evidence, or formal-warning output. ConvLSTM, v4, frozen validation splits,
metrics, thresholds, model parameters, and scientific conclusions are unchanged. No training or
real-network experiment is part of this increment.

## Verification

The focused contract covers `D` addition, whole-row `R` replacement with a changed key id, `I`
tombstoning/removal, rejection of a surviving base dependency on `I`, compact content-addressed
publication, upstream byte preservation, object-before-event crash forward adoption, and pointer
loss between the initial gate and cross-freeze replay. The latter asserts that no recovery call or
overlay publication occurs. Adjacent regression must replay the manifest, recovery, source-derived
reservation, cross-freeze completion, inventory, and live-source contracts. Static verification
includes strict profile loading, upstream implementation/profile SHA-256 pins, Ruff, Python
compilation, and staged-diff review.

## Next authority boundary

The next narrow increment should add a historical-`N+1` effective dispatcher that consumes this
published overlay and supplies the existing machine materialization/consumption path with one exact
effective item at a time. It must preserve old `R/I` audit evidence, prevent old-key receipts from
settling replacements, and remain separate from recovery v6 until its effective reservation and
receipt semantics are explicitly versioned.
