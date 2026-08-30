# Ootang V2 scoped bounded-drain completion v1

## Reachability correction

The previously recorded next step attempted to join the V1
`epoch_drain_started` route-fence transaction with the V2 admission-cut and
source-derived bounded closure. That state is not reachable under the reviewed
protocol. V2 is selected only after a non-clean first blocker, and both V2 and
the admission cut become inert when any durable V1 drain witness exists. The
V2 manifest and every downstream recovery/closure object therefore belong to a
sibling branch, not to the V1 DRAINING branch.

This increment corrects that assumption instead of manufacturing a synthetic
V1+V2 success path. It implements only the currently reachable and stable V2
completion. V1 remains a machine-current eligibility observation: because V1
does not cut the deploy/runner writer paths, a standalone durable drained event
could become stale after its six locks are released. V1 completion must later
be coupled to a writer cut or to the atomic active transition itself.

## One final V2 decision

The assessor has no tunable profile and introduces no intermediate proof,
head, WAL, or capability layer. Under the existing surviving lock order
`manager -> cycle -> replay -> shadow`, it:

1. deep-replays the existing source-derived bounded-closure proof and event;
2. obtains the exact R1/R2a candidate, slot, old epoch and frozen live tip from
   the closure's manifest reservation binding;
3. thereby replays the V2 first blocker and admission-cut prepare, intent,
   attempts, physical `both_cut` sentinels and event through the existing
   manifest loader;
4. reads the machine-current old live tip and proves that it is an append-only
   successor of the frozen admission-cut tip;
5. performs one fresh six-family read-only inventory at that current tip and
   requires zero actionable old-runtime items; and
6. publishes one immutable singleton decision event while the four locks are
   still held.

The settlement scheduler calls this assessor immediately after bounded closure
in the same bounded poll. A waiting closure or a non-empty fresh inventory
writes only a replaceable `cache_authority=false` status. Repeated polling is
machine-driven and an existing matching event is byte-idempotent.

Because the event is profile-free, its schema and namespace are the version
boundary: any future semantic change must use a new schema/namespace and must
not reinterpret existing v1 event bytes.

## Claim boundary

The event's positive claim is deliberately scoped:

`bounded_official_workset_drained=true`

Its scope is `official_machine_reserved_workset`: the exact V2 reserved
workset is terminal-or-superseded, the reviewed official deploy/runner writer
entrypoints remain physically cut, and the fresh six-family inventory has no
actionable item. This is the stable machine handoff needed by a later atomic
transition.

The event does not claim an unrestricted filesystem or lifecycle theorem.
`old_epoch_drained`, canonical issue-route fencing, direct-filesystem writer
fencing, and active switching remain false. Arbitrary same-UID filesystem
mutation was already outside the admission-cut threat model; this increment
does not silently redefine that model or turn a scoped fact into an
unqualified one. ConvLSTM, v4, frozen splits, metrics, thresholds, parameters,
and scientific conclusions are untouched.

## Proportional verification

The focused budget is intentionally small: successful publication plus
byte-idempotent replay, unresolved fresh inventory waiting without authority,
and refusal if actionable work reappears after publication. Existing upstream
tests own V1/V2 mutual exclusion, admission-cut, manifest, recovery, closure,
crash-window, and tamper matrices; they are not copied here. The
settlement-cycle tests verify only the new 17th call and the two aggregate
status flags.

The focused completion `3/3` and cycle/main `42/42` pass as `45/45` in 1.287
seconds. Ruff check/format, Python compilation, diff checks, the unchanged
97-path protected aggregate (`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`),
and protected-source diff checks pass. Implementation, focused test,
settlement adapter, settlement test, and `main.py` SHA-256 values are
`218cecd70bfeae7a12780f5a4024a322101548c07f05e73309ca1440c01e4731`,
`ed8ad7d984ce896246f94be8cc5d24f74e87acca47278204d458b72e94ae1126`,
`1cc35300bf6690b581659f6d4039831e50571b1b205e95d8514e8d7e2b179637`,
`8d2aed3102b74912de03a44fa106893bfdaa849ad03eb9cb355e9e5870685eef`,
and `93f7976924d59e84ea14ab964bbfcb64d93adcf2a7a7ea1822b06cff3618e913`.
No training, full scientific pipeline, historical multi-minute drain fixture,
real network action, manual freeze, approval, cleanup, force, or backdating ran.

## Next boundary

The next meaningful increment is the atomic lifecycle transition that consumes
this scoped V2 decision and commits `SEALED(old) + ACTIVE(new)` under the
appropriate machine locks. It must also define scheduler authorization for the
new epoch. It should not add another all-settled/all-successor/drain-ready
singleton before that transaction.
