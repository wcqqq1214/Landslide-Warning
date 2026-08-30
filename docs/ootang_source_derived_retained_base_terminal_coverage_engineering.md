# Ootang source-derived retained-base terminal coverage v1

## Scope

This authority proves terminal coverage only for frozen-base identities that
survive the current source-derived effective-workset overlay unchanged. It does
not terminalize the source-ingest parent, current `D/R` rows, recovery v6 as a
whole, or the whole effective workset. It writes only to

`workset_recovery_v1/source_derived_retained_base_terminal_coverage_v1/`.

The ConvLSTM/v4 model, frozen splits, metrics, thresholds, parameters, and
scientific conclusions are outside this leaf and remain unchanged.

## Exact denominator

Let:

- `B` be the independently replayed frozen-base item set;
- `E` be the independently rebuilt current effective item set;
- `P` be the unique frozen `outcome_revision/source_snapshot_ingested`
  source parent matched by the cross-freeze authority;
- `Rold` be frozen identities replaced by current rebound rows;
- `I` be frozen identities removed by current invalidation tombstones.

Identity always means the complete tuple
`(key_id, natural_key, namespace_digest)`. The retained denominator is

`Qretained = {b in B | identity(b) in E} - {P} - Rold - I`.

The implementation also checks the stronger partition invariant:

`E = {P} disjoint-union Qretained disjoint-union D disjoint-union Rnew`.

Consequently:

- an old `R` identity cannot survive under its former key or namespace;
- an `I` identity cannot remain effective;
- a current replacement `Rnew` and additions `D` are not frozen retained rows;
- the source parent remains a separately assessed family;
- every otherwise unclassified frozen identity must survive exactly or the poll
  fails closed.

An empty `Qretained` is a meaningful exact result. The authority publishes a
zero-row proof instead of requiring a human exception or inventing a synthetic
item.

## Per-item terminal evidence

The whole frozen-manifest coverage boolean is never reused as blanket current
coverage. Each member of `Qretained` must be covered by exactly one of two
deeply replayed evidence types:

1. a current recovery-v6 terminal chain tip plus its exact published recovery
   event, where the receipt identity, canonical step id/index, transition-plan
   digest, terminal action, and `next_actions` all still match the frozen item;
2. a published source-terminal aggregate proof/event for that same exact
   identity, obtained by replaying the existing manifest-coverage assessment
   machinery but selecting only the required per-item row.

The two provenance sets must be disjoint. Missing receipts, receipt-only crash
frontiers, nonterminal tips, aggregate proofs without events, old rebound
identities, invalidated identities, and status files never count. Optional
source-terminal state is bound only when a selected retained identity uses it;
later unrelated aggregate events therefore cannot invalidate an already
published retained proof.

## Durable publication and recovery

Under the existing manager/cycle/replay/shadow lock order, the coordinator:

1. deep-replays the current overlay computation and requires its exact
   content-addressed object plus singleton publication event;
2. reconstructs the frozen/current partition and the exact retained
   denominator;
3. deep-replays the complete recovery receipt/event prefix when the denominator
   is non-empty and selects exact per-item evidence;
4. writes one deterministic content-addressed proof;
5. appends one singleton event that alone publishes
   `all_current_retained_base_items_terminal=true`.

If the process crashes after proof publication, the next machine poll verifies
that exact proof and appends only its matching event. Event-without-proof,
multiple proofs/events, changed current overlay, lost terminal evidence, or a
changed denominator fails closed. `status.json` is a replaceable diagnostic
cache with `cache_authority=false`; deleting it never changes proof or event
authority.

## Explicit non-claims

This leaf does not claim frozen-manifest coverage, source-parent terminality,
current `D/R` coverage, whole-effective terminality, recovery/transitive or
transition closure, all-lane support, drain/lifecycle/activation authority,
trusted/E2 evidence, network action, or formal warning output. It mutates none
of the manifest, recovery, overlay, sidecar, or source-terminal namespaces.

## Verification

- Focused contracts: `6/6` passed in 7.243 seconds.
- Direct dependency regression covering this authority, current overlay,
  frozen-manifest terminal assessment, and step-dependency authority: `20/20`
  passed in 12.641 seconds.
- Focused coverage includes exact empty-subset publication and idempotent status
  rebuild, old-`R` exclusion, `I` exclusion, missing retained evidence waiting,
  non-empty recovery terminal evidence, proof-only crash forward adoption, and
  published source-terminal per-identity evidence.
- Ruff formatting, Ruff `E7/E9/F`, Python compilation, strict default-profile
  loading, and diff checks passed.
- No training, full scientific pipeline, or real-network action was run.

Implementation, profile, focused-test, protected `main.py`, and ConvLSTM model
SHA-256 values are respectively
`2db6b5bf204990aa3879cf90d8492a57c04e098e5ee3d8ede824af973e1308c8`,
`8db880133d09d785784aec029f7b84e4410b042af39a50c2eda7635e74d11456`,
`0b8485a7c1043d4d62cd051bf0f06276aeb9c8fd0224dbede1116d3afccabd14`,
`02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`,
and `282c8f6f67c7676470d65653a5f21e2a2b27321aeedc6a44031d4bd6674ad858`.

## Next edge

The next narrow authority may combine three already separated published
families under one current-overlay identity partition:

1. this retained-base exact-subset event;
2. the current source-ingest parent terminal aggregate event;
3. the current effective `D/R` terminal-coverage event.

That later union may prove current-effective-workset terminal coverage, but it
must still keep generic recovery/transitive closure and lifecycle/drain claims
separate.
