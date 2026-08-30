# Ootang source-derived bounded terminal closure v1

## Scope

This authority proves a bounded terminal-or-supersession closure for one
immutable source-snapshot edge. It joins:

- the frozen workset reservation inventory;
- the exact current-effective terminal-coverage proof/event;
- the complete source-derived `D/R/I` reservation object/event;
- the cross-freeze completion and current overlay bound to the same source
  edge.

It writes only below

`workset_recovery_v1/source_derived_bounded_terminal_closure_v1/`.

The authority does not edit recovery v6, the derived reservation, overlay,
terminal-coverage leaves, live ledger, source registry, ConvLSTM/v4 model,
frozen splits, metrics, thresholds, model parameters, or scientific
conclusions.

## Two exact inventory equations

Identity is always the complete tuple
`(key_id, natural_key, namespace_digest)`. Let:

- `B` be the independently replayed frozen-base inventory;
- `P` be the exact source-ingest parent;
- `Qretained` be frozen identities retained unchanged;
- `Rold` be frozen identities replaced by rebound rows;
- `I` be frozen identities invalidated by the exact source edge;
- `D` be new derived additions;
- `Rnew` be the current rebound replacements;
- `E` be the exact current-effective identity set.

Publication requires both pairwise-disjoint equations:

`B = {P} disjoint-union Qretained disjoint-union Rold disjoint-union I`

`E = {P} disjoint-union Qretained disjoint-union D disjoint-union Rnew`

It also requires the source-derived reservation to be the complete disjoint
`D/R/I` classification for that exact source-snapshot edge. The parent identity
must agree across the frozen manifest, derived reservation, cross-freeze,
retained-base exclusion, and current-effective authority.

The derived reservation stores canonical D/R rows before their overlay
`key_id` is finalized. This assessor therefore runs the hash-pinned overlay
normalizer with the frozen manifest SHA-256, reproducing the exact current key
identity before comparing the sets. It never accepts a natural-key-only match.
The overlay profile/implementation pair is a direct pin because this
normalization is invoked directly.

## Resolution semantics

The proof contains one ordered resolution row for every frozen item:

- `P` and every retained identity bind their current-effective terminal row;
- each `Rold` binds its exact `Rnew` replacement and that replacement's
  current-effective terminal row;
- each `I` is resolved only as an exact invalidation supersession under the
  same immutable source edge.

It also contains one ordered row for every source-derived classification:

- each `D` binds its exact current terminal row;
- each `Rnew` binds both its old frozen identity and exact current terminal
  row;
- each `I` binds the exact frozen identity it supersedes and does not invent a
  terminal receipt.

Thus invalidation is explicit supersession, not silent completion. All rows are
content-hashed, and their ordered digests are bound to the immutable manifest,
cross completion, derived reservation/event, overlay object/event, and current-
effective coverage proof/event. D/R/I counts and keyset digests are recomputed
from canonical rows rather than copied as blanket booleans.

## Durable publication and machine recovery

Under the existing machine lock order, the coordinator deeply reconstructs the
current-effective proof from all of its leaves and requires the matching
singleton event. It then rebuilds both equations and the resolution rows. A
legitimate missing current-effective event returns a waiting status without
creating closure authority.

Complete state produces one deterministic content-addressed proof followed by
one singleton event. Only that event publishes
`current_source_derived_bounded_terminal_closure=true`. A proof-only crash is
recovered automatically by verifying the same immutable cut and appending only
the matching event. Orphan events, branched namespaces, changed source edges,
changed partitions, or lost upstream evidence fail closed. `status.json` is a
replaceable diagnostic cache with `cache_authority=false`.

## Explicit non-claims

This v1 covers exactly one frozen/current/source-derived cut. It does not claim
generic `bounded_workset_recovery_implemented`, `all_reserved_items_settled`,
`all_reserved_successors_supported`, or generic terminal/transitive closure.
Those names remain false because this proof has not yet been rebound to the
historical drain-start boundary, canonical route fence, and a fresh no-post-
fence-admission capture.

It also does not claim full/all-generation workset terminality, recovery-v6 key
terminality, drain eligibility, `old_epoch_drained`, lifecycle/transition
authority, active switching, rotation, trusted/E2 evidence, activation,
network action, or formal warning output. The inherited current-`D/R` authority
still requires a non-empty denominator, so zero-`D/R` source edges remain an
explicit future machine branch rather than a human waiver.

## Verification

- Focused contracts: `3/3` passed in 16.637 seconds.
- Narrow bounded-closure/current-effective/source-parent/retained-base/current-
  `D/R` chain: `18/18` passed in 59.004 seconds.
- Focused scenarios cover the exact frozen/effective/successor equations and
  idempotent status rebuild, missing current-effective event waiting without
  own authority, and proof-only crash forward adoption that appends only the
  matching event.
- Ruff `E7/E9/F`, Python compilation, strict default-profile loading, protected
  model checks, and diff checks passed.
- No training, full scientific pipeline, real-network action, manual freeze,
  manual approval, cleanup, force, or fabricated backfill was run.

Implementation, profile, focused-test, protected `main.py`, and ConvLSTM model
SHA-256 values are respectively
`9784bebbf9fa560851c1a7184cb9f8bf98a97ae848b8575bff090ff77d62a81f`,
`6f149b0d4aea0906de3e9df27a6159d16bafc57d5fa531602849ae6126a52261`,
`d84f372c788485a4c240bd083c1f2e1cf07dc5b86c37d7ea8dfffca8b3882615`,
`02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`,
and `282c8f6f67c7676470d65653a5f21e2a2b27321aeedc6a44031d4bd6674ad858`.

## Next boundary

The next narrow increment should be a separately versioned drain-completion
assessor. It must deep-replay the historical drain-start transaction and route
fence, bind this closure to the matching frozen manifest/candidate, and recapture
that no old-epoch admission or unresolved runtime work appeared after the
fence. Only that later assessor may consider publishing an old-epoch drained
fact. Active switching, `SEALED(old)+ACTIVE(new)`, scheduler authorization, and
cycle v4 must remain later independent stages.
