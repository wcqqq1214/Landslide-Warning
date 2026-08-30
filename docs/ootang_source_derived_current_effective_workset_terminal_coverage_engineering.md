# Ootang source-derived current-effective workset terminal coverage v1

## Scope

This authority proves terminal coverage for exactly one published current
source-derived effective-workset overlay. It combines only three already
published, independently replayed families:

1. the exact current source-ingest parent terminal aggregate;
2. the exact retained frozen-base subset terminal coverage;
3. the exact current derived/rebound (`D/R`) terminal coverage.

It writes only to

`workset_recovery_v1/source_derived_current_effective_workset_terminal_coverage_v1/`.

The ConvLSTM/v4 model, frozen splits, metrics, thresholds, parameters, warning
rules, and scientific conclusions remain outside this authority and are
unchanged.

## Exact current identity union

Identity always means the complete tuple
`(key_id, natural_key, namespace_digest)`. Let:

- `E` be the independently rebuilt current effective item set;
- `P` be the singleton identity from the published current source-parent
  terminal aggregate;
- `Qretained` be the exact target set in the published retained-base proof;
- `Qdri` be the complete ordered current `D/R` denominator in the published
  effective-outcome coverage proof.

Publication requires all of the following:

`E = {P} disjoint-union Qretained disjoint-union Qdri`.

Each family must be internally unique, every pairwise intersection must be
empty, and the union must equal the current overlay identity set exactly. The
implementation then walks the current overlay's topological item order and
creates one coverage row for every current effective identity. Every row binds
the selected leaf proof SHA-256, leaf event SHA-256, and—where applicable—the
exact per-item leaf coverage-row SHA-256. The resulting ordered row set is
hashed again and bound to the current overlay's keyset, identity-set, and
dependency-graph digests.

Old rebound identities and invalidated identities cannot enter the union: the
retained-base authority already excludes them, while the current `D/R`
authority accepts only current overlay identities. No count-only or status-only
shortcut can replace the set equality.

## Leaf authority replay

The coordinator acquires the existing machine lock order once and reconstructs
the current overlay computation. It deep-verifies the content-addressed overlay
object and singleton overlay event, then independently rebuilds the expected
payload for each leaf and requires both its exact proof and matching singleton
event:

- `all_current_effective_d_or_r_terminal=true` must have complete frontiers,
  equal required/covered counts, and no missing identities;
- `all_current_retained_base_items_terminal=true` must have an exact bijection
  between retained targets and terminal evidence rows, including the valid
  zero-row case;
- `current_source_ingest_parent_terminal=true` must refer to the same exact
  source parent and bind the same current `D/R` proof/event.

The retained proof's excluded source parent must equal the parent authority's
identity exactly. A leaf proof without its event, a status cache, a changed
overlay generation, missing leaf evidence, an orphan, or a branched namespace
never counts. Before this authority has published, a legitimate missing family
returns a machine waiting status. After publication, loss or drift of any exact
family fails closed.

## Durable publication and crash recovery

Once the exact union is complete, the coordinator writes one deterministic
content-addressed proof and then one singleton publication event. Only that
event publishes
`current_effective_workset_terminal_coverage=true`. A crash after proof
publication is recovered automatically: the next poll replays the same current
overlay and all three leaves, verifies the exact proof bytes, and appends only
the matching event. `status.json` is a replaceable diagnostic cache with
`cache_authority=false`; deleting it does not change proof/event authority.

The coordinator never calls the current-source selector, materializes an
outcome, mutates the live ledger, republishes a leaf, or writes outside its own
namespace.

## Explicit non-claims

The positive claim is scoped to one current source-derived overlay. It is not
`all_effective_items_terminal` across generations and is not a generic/full
workset terminal fact. It does not prove recovery-v6 or transitive transition
closure, all reserved successors or lanes, bounded recovery completion,
drain/lifecycle eligibility, old-epoch drained state, active switching,
rotation, trusted/E2 evidence, real activation, network action, or formal
warning output. The three leaf-specific positive claim names remain false in
this aggregate's own event so consumers cannot mistake it for a replacement
leaf authority.

The inherited current-`D/R` leaf deliberately requires a non-empty `D/R`
denominator. Therefore a future overlay with zero `D/R` rows remains a separate
unimplemented exact-vacuous edge; this v1 waits rather than inventing coverage.

## Verification

- Focused contracts: `3/3` passed in 14.731 seconds.
- Narrow current-effective/source-parent/retained-base/current-`D/R` family
  chain: `15/15` passed in 45.205 seconds.
- Focused coverage includes the complete exact three-family union and
  idempotent status rebuild, missing source-parent waiting without own
  authority, and proof-only crash forward adoption that appends only the
  matching event.
- Ruff `E7/E9/F`, Python compilation, strict default-profile loading, and diff
  checks passed.
- No training, full scientific pipeline, real-network action, manual freeze,
  manual approval, cleanup, force, or fabricated backfill was run.

Implementation, profile, focused-test, protected `main.py`, and ConvLSTM model
SHA-256 values are respectively
`80f84db4e88dc5fdcc8e768eda2ea11f93e89c89c12726522ed9efb015361ac3`,
`f82269a8d2c60d9c41bf0d48dab626444084aae2df481f58c19a12d056bfc1b7`,
`53b33573a0dceeaa775375a9edbf1dc1fac8c3e4f0f16c9d5147c0a7d28bab87`,
`02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`,
and `282c8f6f67c7676470d65653a5f21e2a2b27321aeedc6a44031d4bd6674ad858`.

## Next edge

The next narrow step should not relabel this current-overlay fact as drained.
It should first build a separately versioned bounded terminal-closure assessor
that proves the frozen reservation inventory, current effective identity union,
and source-derived successor inventory are complete under one immutable cut.
Only after that authority closes every reserved identity and successor may a
later drain/lifecycle assessor consume it. The zero-`D/R` exact-vacuous branch
should be handled explicitly in that progression rather than by a manual
exception.
