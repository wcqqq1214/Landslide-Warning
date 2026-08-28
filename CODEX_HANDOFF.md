# Codex handoff: Ootang machine prequential track and prior v5 work

**Prepared:** 2026-08-29
**Repository:** `/Users/wcqqq1214/Project/Landslide-Warning`
**Branch:** `main`
**Committed baseline before this increment:** `c54c279 feat: consume settled outcome revisions`
**State:** R1/R2a/R2b/R2b-2a/R2b-2b-1/R2b-2b-2a/R2b-2b-2b/R2b-2b-2c
expected-pre-head CAS、单事件 machine-only `anchor_request_recorded` adapter 与
`anchor_result_recorded` request intent/四锁外 response observation、四锁内 result CAS、自动 retry
loop、manifest-reserved outcome settlement adoption 与 published outstanding 43-event
writer/crash-forward adoption、receipt-tip repair/machine-selected outcome materialization、下一
poll 的 outstanding consumption 桥、revision predecessor authority、settled-date canonical
16-event revision consumption 与 crash-forward adoption 已提交；当前工作树实现 backfill
revision 的 canonical 8-event 自动消费、fresh CAS 与 post-CAS receipt adoption。
它仍不是完整 recovery、terminal/transitive closure、泛化 admission fence 或 DRAINING lifecycle
authority；不声明 remote exactly-once、drained、active switch、rotation、trusted anchor、E2
evidence、activation 或 formal warning。

## 2026-08-29 backfill outcome revision consumption

The recovery coordinator now dispatches a materialized `selection_kind=revision` tip by replaying
the current live projection. The target must belong exclusively to either `backfill_events` or
`settled_events`; both-present and both-absent states fail closed. This prevents a backfill revision
from entering the settled writer while preserving the existing settled branch.

The backfill path uses the independent persisted schema
`ootang_live_backfill_revision_consumption_action_contract_v1`. It binds the exact pre-head,
original `backfill_not_blind` entry, immutable predecessor revision/outcome pair, materializer
artifacts, online-state digest, last-finalized/outstanding issue and seal, projection counts, and
the exact 8-event transaction. Existing outstanding v2 and settled revision v1 contracts and
receipts remain compatible and are dispatched through their original schemas.

The canonical live-core `_append_backfill_revision` writer emits one `outcome_revision` for each
of the eight stations and no rescore. Postconditions require the original backfill, online states,
last-finalized/outstanding authority, issue/seal state, and blind-settled, engineering-candidate and
backfill counts to remain unchanged. The target's revision registry and latest actual are updated.
If the target is the last finalized date and there is no outstanding target, the revised actuals
also become the next-day persistence baseline automatically; otherwise that baseline is unchanged.

Fresh work appends only through CAS at the exact expected pre-head. If CAS commits before the
terminal recovery receipt is durable, the next poll rebuilds the canonical EventSpecs and adopts
only the exact contiguous 8-event slice without a second CAS. Partial, displaced and mismatched
transactions fail closed.

Results are targeted `2/2` (0.836 s), full recovery `55/55` (4.715 s), and adjacent
recovery/materializer/live-ledger/CAS/epoch-gates/main `219/219` (12.984 s). Ruff format/check,
Python compile, strict profile load and diff check pass. Two independent read-only reviews report
P0=0/P1=0. A temporary consumed-at-freeze fixture also verified automatic rev1-pointer repair to
ledger-consumed rev2 followed by zero-CAS `preexisting_backfill_revision_adoption`.

Current recovery module/profile/test, increment document and protected `main.py` SHA-256 values are
`e4b949664a7cb8cbb07936fa047bc157d6a648a2108adf19de93280a843afceb`,
`9c9a1dc4404a51b5a5a13721729cdc3306395d2bd69564e13e2c39c5f125f116`,
`ae558660ee058822d23573327f3139d7f673fbdf7234676d4d8aa24580466f7e`,
`15b1352743c808dd472dfce7465f44a6e82ffdf8e675f3ea7c5e6d5a8ec46242` and
`02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`.
No training or real network work is part of this increment. ConvLSTM, v4, frozen splits, metrics,
thresholds, model parameters and scientific conclusions are unchanged. Detailed authority and
crash semantics are in `docs/ootang_backfill_revision_consumption_engineering.md`.

The next narrow increment is the first-backfill writer. It must create the initial canonical
`backfill_not_blind` transaction from immutable materialized-outcome authority while retaining the
expected-pre-head CAS/crash-forward model. It remains separate from this revision contract.

## 2026-08-29 settled-date outcome revision consumption

The recovery coordinator now consumes a terminal materialized
`selection_kind=revision` tip through the live core's canonical `_append_revision` writer. It
replays the immutable materializer receipt, exact outcome and source input manifest, then requires
their `previous_revision_id`/`previous_outcome_sha256` pair to equal the live ledger's latest
registered revision for that settled date. The original settlement entry, issue/seal and frozen
expected pre-head are bound before any mutation; mutable outcome pointers or current-source
selection are not used as consumption authority.

The writer emits the exact station-interleaved transaction
`(outcome_revision, revision_rescore_recorded) x 8`, for 16 live-ledger events. Postconditions prove
that the original settlement, all online states, `last_finalized_date` and
`outstanding_target_date` remain unchanged. The new actual is registered only as a revised
retrospective view: revision events do not rewrite online state, and revision rescores are not blind
metric eligible.

Fresh work appends only at the exact expected pre-head through CAS. If CAS commits before the
recovery receipt is durable, the next poll rebuilds the 16 EventSpecs from the persisted contract
and adopts only an exact contiguous slice, without calling CAS again. Partial, displaced or
mismatched slices fail closed. Completed revision receipts are verified from immutable history, so
a later legal live suffix does not replay the writer or mutate ledger/recovery artifacts.

Revision consumption uses the separate persisted schema
`ootang_live_settled_revision_consumption_action_contract_v1`. The existing outstanding
`ootang_live_outcome_consumption_action_contract_v2` path and its historical contracts/receipts are
preserved unchanged. Recovery profile `2.1.0-settled-revision-consumption` exposes only the new true
capability `live_settled_revision_consumption_adapter_implemented`; backfill revision and
first-backfill writers remain unsupported.

Targeted coverage includes the real rev1 outstanding -> rev2 materialization -> canonical 16-event
revision chain, CAS-commit-before-receipt exact adoption, and read-only verification after a legal
live suffix. Results are targeted `2/2` (1.901 s), full recovery `53/53` (4.734 s), and adjacent
recovery/materializer/live-ledger/CAS/epoch-gates/main `217/217` (12.694 s). Ruff format/check,
Python compile, strict profile load and diff check pass. Two independent read-only reviews report
P0=0/P1=0; extra temporary-runtime checks covered consumed-at-freeze zero-event revision adoption,
backfill fail-wait and outstanding-v2 AST/contract compatibility.

Current recovery module/profile/test, increment document and protected `main.py` SHA-256 values are
`99884ad28de5ab851da4e2401e67ec90d81258f014971c54711c1ba7376e492c`,
`ef601148860ef5d4781e934f5cafb046eb82e0bac447c3d3f41ae05c19613c37`,
`688b395b07f11b7892e2f67918cfda3e3a253532797c479de80cb44ce8c71da6`,
`7e40c68270c7a78dc50bc18ad66532e7dc8c068418aefae4eb286197df293d76` and
`02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`.
No training or real network work is part of this increment. ConvLSTM, v4, frozen splits, metrics,
thresholds, model parameters and scientific conclusions are unchanged. Detailed authority and
crash semantics are in `docs/ootang_settled_revision_consumption_engineering.md`.

The next narrow increment is backfill revision consumption, reusing immutable predecessor
authority, the canonical writer and expected-pre-head CAS/crash-forward adoption. First-backfill
follows as a separate writer. Cross-freeze derived-work/step-level dependency reservation remains a
higher-level closure increment.

## 2026-08-29 outcome materialization and outstanding-consumption bridge

The recovery coordinator now implements two exact manifest-bound
`outcome_revision -> outcome_materialized` authority branches. An unpublished
`outcome_receipt_chain` tip is forward-reconciled from its immutable receipt, exact outcome and
input manifest. A `machine_selected_source_outcome` is rebuilt from the frozen current-source
snapshot, source record, live epoch/seal and selection authority, then published with the canonical
materializer primitives. The latter authority now includes an exact predecessor revision/outcome
pair for revisions; first outstanding/backfill candidates require both predecessor fields to be
null. Materialization writes no live-ledger events and performs no network action.

The nonterminal materialization recovery receipt is now the only authority for the next poll. Its
materializer receipt, exact object and input manifest are confined to the active runtime and replayed
immutably before the existing outstanding writer can construct or adopt the canonical 43-event live
transaction. No mutable pointer or current-source reselection is used for this bridge. A real
end-to-end fixture verifies machine-selected outstanding materialization on one poll and exact
43-event consumption on the next.

Commit-before-recovery-receipt recovery distinguishes the expected receipt as the current tip, a
legal historical predecessor, or absent. Only the current tip is reconciled against pointer/inbox;
a historical receipt is adopted without rolling back a newer publication, while only the
machine-selected branch may fresh-publish an absent receipt. A frozen tip already present in the
live ledger uses the explicit `preexisting_consumed_adoption` branch and adopts its canonical
43-event slice with zero CAS writes.

Inventory now tracks the pending receipt tip per date. If current source has a newer revision, the
candidate is frozen as `selection_kind=revision`, binds `previous_revision_id` and
`previous_outcome_sha256`, and depends on the pending tip. This also handles ledger-known rev1 plus
pending rev2 plus current rev3. Recovery checks the same predecessor fields against the source
selector and the immutable materializer chain. A real chain test consumes pending rev1 and then
materializes current rev2 as receipt sequence 2 without additional live events.

Manifest profile `1.3.0-revision-predecessor-authority` and recovery profile
`2.0.0-outcome-materialization-chain` expose only these implemented capabilities. Revision,
backfill and first-backfill live-ledger consumption writers remain unsupported. Full workset,
all-successor, derived-reservation, terminal closure, lifecycle, trusted-anchor, E2 and formal
claims remain false.

Focused inventory+recovery tests pass `58/58` in 3.71 seconds. The bounded adjacent suite, including
the outcome materializer plus recovery, live-ledger/CAS, inventory/manifest, admission,
eligibility, drain and main, passes `192/192` in 11.06 seconds. Ruff check/format, compile, strict
profile loading and diff checks pass. Final independent read-only review reports P0/P1=0. No
training, real network, long concurrency/capacity matrix or unrelated edge suite was
run. ConvLSTM, v4, frozen splits, metrics, thresholds, parameters and conclusions are unchanged.
Detailed authority and crash semantics are in
`docs/ootang_outcome_materialization_recovery_engineering.md`.

Current inventory/manifest/recovery module hashes are
`2b56de3f36da3a08d34bf3a9509b0492c5d989cd1f391491c0574bed2723bd52`,
`0ca331c6827cd896b4c3261792eed5f933e104d4c40c4fbf3b7e416e9b1fd424` and
`6b7cf96e376293ee6b06501f363a3b9e84844e0671084e2a8269bb6c5307bc7a`.
Manifest/recovery profile hashes are
`857ae1ff031289d51c0a2947beeb2e47ceb9d48a3769db707c8f7f75750d48dc` and
`243d43b2b9444d0daaa217eac2695d3754686249777eaa0dadd11dd31ea735a2`.
Inventory/recovery test hashes are
`cf542fa0ca5901f9eb93448fc5581b983bd057750b371d6126bfc20e72f3e844` and
`2db4f4eef07fe5234c843faac7a0fb7c56790b0dae98b3b775b0c8d3b2d2ab9d`; the increment
engineering document is
`8a53cf5eb33c669d5a427f9e7fdb73bebcbcadc1382dda54ca40e911a6bb5855`.
Protected `main.py` remains
`02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`.

The settled-date revision writer planned by this historical entry is now implemented in the
successor increment above. The current next narrow increment is backfill revision consumption;
first-backfill follows separately. Cross-freeze derived-work/step-level dependency reservation
remains a higher-level closure increment.

## 2026-08-28 published outstanding outcome consumption

The recovery coordinator now implements the manifest-bound
`outcome_revision -> outcome_or_revision_consumed` writer for `selection_kind=outstanding`. It
reconstructs authority from the immutable materializer receipt, exact outcome object, source
manifest and frozen item contract, then invokes the frozen live canonical writer to produce the
exact 43-event transaction. It does not fabricate actuals, mutate research thresholds, or add any
manual freeze, cleanup, approval, force or backdate path.

On a fresh action, the frozen receipt chain and current publication must still identify the same
predecessor. The transaction is then appended through expected-pre-head CAS. Crash recovery uses a
three-state ledger classification: absent permits fresh CAS, exact-complete adopts the transaction
and writes only the terminal recovery receipt, and partial/displaced fails closed. Exact-complete
adoption is derived solely from the persisted contract and immutable EventSpecs; it never re-reads
the mutable outcome pointer/inbox or calls CAS again. Historical completed-receipt verification is
also pure/read-only, so later legal source revisions and ledger suffixes do not invalidate or replay
the consumed batch.

Inventory construction now gives an already-confirmed frozen live settlement item the unique
same-date, same-source-revision outcome dependency, preferring the exact receipt-chain dependency.
This closes the real manifest path only when confirmation/dependency already exists at freeze time.
If confirmation occurs after manifest freeze, the immutable DAG has no reserved outcome edge and
the live key remains machine-waiting. This limitation is explicit: derived future-work or step-level
dependency reservation still needs a versioned design, and terminal/transitive closure is not
claimed.

Recovery profile `1.8.0-outstanding-outcome-consumption` and manifest profile
`1.2.0-outcome-settlement-dependency` expose only the implemented capabilities. Revision,
backfill, first-backfill and `outcome_materialized` writers remain unsupported, as do full-workset,
all-successor, derived-reservation, lifecycle, trusted-anchor, E2 and formal-warning claims.

Focused recovery tests pass `46/46` in 2.03 seconds. The bounded adjacent recovery,
live-ledger/CAS, inventory/manifest, admission, eligibility, drain and main suite passed `154/154`
in 8.52 seconds before the final formatting-only pass. Ruff check/format, compile, strict profile
loading and diff checks pass after formatting. Independent final review reports P0/P1=0. No
training, real network, long concurrency/capacity matrix or unrelated edge suite was run; ConvLSTM,
v4, frozen splits, metrics, thresholds, parameters and conclusions are unchanged. Full authority
and crash semantics are documented in
`docs/ootang_outstanding_outcome_consumption_engineering.md`.

Current inventory/manifest/recovery implementation SHA-256 values are
`64e7feaf295689e444803fd20eda2d3a754b2ec75932e85987fcba603281bb01`,
`5284018ade9160a80b78487470d3457a059490fb9062d965fc59048c7acb9c2d` and
`ee31538f5f089b7c49a62e342c32243ba66fdb7bc3eec5efe0dc108795a84340`.
Manifest/recovery profile hashes are
`338b8e4c90bf1bf241a255148c4352b3a9fa197658dd08d1dd745ada604dd39e` and
`3983d790b23d8d4730bddde96070cac16717c29abe35e1e85abe4b5828893629`.

The next narrow slice is `outcome_materialized -> outcome_or_revision_consumed`, reusing the same
immutable-authority, expected-pre-head CAS and three-state crash-forward framework. Revision and
backfill writers follow separately. Cross-freeze derived dependency reservation is a higher-level
closure increment and must not be conflated with those writers.

## 2026-08-28 manifest-reserved outcome settlement adoption

The recovery coordinator now supports `live_outstanding -> outcome_batch_settled` only as a
receipt-only adoption of an already-existing canonical live transaction. It does not read the
mutable outcome inbox, select an outcome from current source, fabricate actuals or append live-ledger
events. If the transaction is absent, the machine waits before creating a settlement item intent.

The live item must name a manifest-reserved `outcome_revision` sibling dependency with matching old
epoch, target and source revision. The existing dependency gate must first deep-verify that sibling's
terminal `outcome_or_revision_consumed` receipt. A dynamically confirmed item whose frozen manifest
contains no outcome dependency therefore remains machine-waiting; recovery never edits the frozen
DAG after the fact.

Confirmation authority may come from a frozen `anchor_confirmed_event` anywhere inside the frozen
prefix or from the exact preceding candidate-confirmed result receipt. Confirmation is not assumed
to be the settlement pre-head: canonical earlier-date outcome revisions may legally appear before
the outstanding outcome. The adapter locates the unique settlement matching the reserved outcome,
takes its real predecessor as pre-head, and verifies the contiguous 43-event transaction:
1 opened, 8 reveals, 32 score/expert/conformal/drift updates, 1 site score and 1 settlement. Frozen
live replay must validate both the pre-settlement outstanding projection and the settled projection.

The create-only action contract/receipt bind the manifest sibling, confirmation, real transaction
pre-head, ordered-entry digest, first/terminal events, outcome/source/revision/input-manifest
identity and state hashes. The receipt is terminal only for this live key. Network, trusted-anchor,
E2 and formal-warning claims remain false; full workset/derived-work/terminal closure and lifecycle
authority remain false.

Focused recovery tests pass `41/41` in under one second, including a real eight-station canonical
chain with 8 legal revision events between confirmation and the 43-event batch. The bounded adjacent
recovery/live-ledger/CAS/inventory/manifest/admission/eligibility/drain/main suite passes `148/148`
in about 8.13 seconds. No training, model rerun, real network call or long edge/capacity matrix was
run. ConvLSTM, v4, frozen splits, metrics, thresholds and conclusions remain unchanged. Initial
independent review found two P1 authority/order defects; both were fixed, and final review reports
P0/P1=0. Detailed authority and crash boundaries are in
`docs/ootang_outcome_settlement_adoption_engineering.md`.

Current recovery module/profile/test, `main.py` and increment engineering-document SHA-256 values
are `89ada0819ed1d81cafe2e854cc86c54f3c57bbe2ef3a36a170327d5fcc7291d5`,
`b114f8bdc646961a97808ce370db38727ef30f572a0a7262d06b31ed718343be`,
`2dc8200396310995d9011d881bd35ab2a662fec3d8361f951e27184a8db6c499`,
`02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898` and
`a1ec3784e3d3d10acec03c76bc03ae51b4d87d3adc8617c1d362404508d8b269`.
The 97-path protected aggregate remains
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`.

The next narrow slice is the fresh manifest-bound
`outcome_revision -> outcome_or_revision_consumed` adapter. It must consume immutable materializer
receipt/exact-object/source-manifest authority, reuse the frozen canonical writer to generate the
complete EventSpecs, and append/adopt them through expected-pre-head CAS. `outcome_materialized`
and future-version derived-work reservation remain separate unsupported work.

## 2026-08-28 locked anchor-result ledger-adapter and retry continuation

The coordinator consumes an existing exact response link/object only after reacquiring the
manager→cycle→replay→shadow locks. It never reads the bearer token or calls transport. Immediately
before ledger mutation it performs one nonblocking external-dispatch-lock durability fence: if the
publisher still owns the lock, the machine waits with zero ledger mutation; otherwise it exact
re-adopts/fsyncs object then link, releases that lock and continues. An empty per-step object
directory is harmless mkdir crash residue, while orphan links, unknown files and branched objects
still fail closed. Object-only crash adoption uses the same order outside the four locks: it durably
re-adopts the exact object before publishing the missing link, preventing a second crash from
leaving a durable orphan link.

The adapter rebuilds one frozen-writer-shaped EventSpec from the request action contract and the
deep-verified observation. `candidate_confirmed` maps to `anchor_confirmed` with the normalized
candidate. `deterministic_failure` maps to `anchor_failed` with stable
`reason_code=request_or_receipt_validation_failed`, recovery-specific
`error_type=AnchorResultProtocolFailure`, and `retry_policy=automatic_next_poll`; observation
stage/code remain receipt evidence rather than extra live payload fields.

The recovery-only expected-pre-head CAS appends only while the request is still the exact terminal
head. A retry adopts only the identical result at the fixed position immediately after that request,
and may tolerate a valid later suffix. CAS disposition is not persisted. Action semantics bind the
exact response object/link, request/result identity and EventSpec digest with per-consumption
`network_action_performed=false`; remote exactly-once and trusted/E2 remain false. The live ledger is
the source of truth, so this narrow slice does not add the redundant anchor receipt cache file.

The result receipt is nonterminal and narrows the graph using reviewed evidence:
confirmed→`outcome_batch_settled`, deterministic failure→`anchor_request_recorded`. Historical
receipt replay rebuilds the same observation/spec/event at its fixed ledger position. A crash after
CAS but before receipt exact-adopts the result; a crash after receipt but before recovery event only
adds the missing previous-hash event. Permanent observation artifacts are now valid for both pending
and completed result steps.

The deterministic-failure branch is now fully machine-driven. The next request contract must bind
the exact failed-result receipt and `anchor_failed` ledger row, uses that row as expected pre-head,
and builds only `attempt + 1`. Its CAS receipt then authorizes the next result intent, whose contract
must bind the exact request receipt/event. Historical replay accepts the legitimate later suffix but
cannot lose either predecessor. This loop never freezes, cleans up, approves, forces or backdates by
hand.

The confirmed branch still waits for a reviewed `outcome_batch_settled` recovery adapter; full
workset/network recovery and other families remain deferred. Focused recovery tests pass 36/36 in
about 0.45 seconds. The adjacent recovery, live-ledger/CAS, inventory, manifest, admission-cut,
eligibility, drain-v2 and main suite passes 143/143 in about 7.67 seconds. No training, model rerun,
real TSA/HTTP request, long concurrency or capacity matrix was run. ConvLSTM, v4, frozen splits,
metrics, thresholds and conclusions remain unchanged. Full detail is in
`docs/ootang_anchor_result_ledger_adapter_engineering.md`.

Current recovery module/profile/test and `main.py` SHA-256 values are
`51aa8c0eda8b4561a5873fceb3a36570c8e79e6eda3d33761b947153f00bb008`,
`3157abe52b5357b565366e2a3026a53b087e40a19f01615ff274b9e3e68074da`,
`407e10e9aa5a951305dd35a6074ddc463a80f10c54f5f08a1885b29921df92fc` and
`02cda8f065949c96654f11329eb150cd8d54fec22f59c05fe61416f93df02898`.
The 97-path protected aggregate remains
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`.
Final independent read-only review reports P0/P1=0. Its only P2 is intentionally bounded test
coverage: the permanent suite stops after preparing the second-attempt result plan instead of
repeating the already-covered response-consumption/CAS path for attempt 2.

## 2026-08-28 unlocked anchor-result response-observation continuation (committed `507b5a5`)

The coordinator now returns an `AnchorResultDispatchPlan` from either the newly prepared or the
existing pending result-intent branch. Its existing `finally` releases the surviving
manager→cycle→replay→shadow locks before the public coordinator invokes transport. A separate
`external_anchor_dispatch.lock` is acquired only outside those four locks, so DNS, TLS, socket I/O
and the total response deadline cannot hold or invert the coordinator lock order. Under that lock,
the machine re-reads the exact item-intent snapshot and adopts any existing response artifact
before reading credentials or invoking the network.

The default transport performs a no-redirect HTTPS `POST` using only the frozen endpoint,
canonical request body, timeout, 1 MiB response limit and stable `Idempotency-Key`. It sends fixed
JSON/identity headers and a total `SIGALRM` deadline. The bearer token is read from the frozen
environment-variable name only at dispatch time; missing or invalid token bytes produce machine
waiting with zero network. The value is not persisted, hashed or returned. A response that reflects
the exact token bytes is blocked before artifact publication.

HTTP 408/425/429/5xx and URL/timeout/OSError delivery ambiguity create no observation and retain the
same intent/key for automatic retry. A complete deterministic response is classified as either
`candidate_confirmed` through the frozen `live._anchor_response` interface or a bounded
`deterministic_failure`. The machine durably publishes the content-addressed object first at
`external_anchor_response_objects/<step_id>/<sha256>.json`, then the unique link at
`external_anchor_response_links/<step_id>.json`. A crash after the object but before the link is
forward-adopted on the next poll with zero network; an existing exact link is also zero-network and
waits for the result adapter. Multiple objects, orphan links, foreign step IDs, content/address or
request-identity drift fail closed.

Observation/link records bind the profile, item intent, request event/body/endpoint/idempotency
identity, bounded raw response bytes, outcome and normalized candidate/failure. They explicitly
keep `remote_exactly_once`, trusted-anchor/E2, live-ledger-result and recovery-receipt claims false.
The profile uses v5 intent/item-intent/receipt/status authority and adds only
`live_anchor_result_response_observation_implemented=true`; `network_action_performed` is now a
per-poll occurrence rather than a static capability. No real network call or live-ledger mutation
was executed in this increment.

Focused fake-transport recovery tests pass 30/30 in about 0.18 seconds. The adjacent recovery,
live-ledger/CAS, inventory, manifest, admission-cut, eligibility, drain-v2 and main suite passes
137/137 in about 7.50 seconds. No training, model rerun, real TSA/HTTP request, long concurrency or
capacity matrix was run. ConvLSTM, v4, frozen splits, metrics, thresholds and conclusions remain
unchanged. Full detail is in
`docs/ootang_anchor_result_response_observation_engineering.md`.

Recovery module/profile/test and `main.py` SHA-256 values are
`e7fe4011c2f00bbf09d22c3e451db66e3aa62ab1333ebb8b92b8ccd7ba1f634b`,
`2fd37e48a5b3eeb8a321b559f9a4e162f0abb9de32f5e930bff9956b7e488177`,
`dc68b54ab8b99813704903a3d81ae39d7bb33f692ce87a66895c09d2a66d31a0` and
`4da6b9f69069c6ef980e927961d557951ab4fd4fae3e0992e0d59c8e238f9a4d`.
The 97-path protected aggregate remains
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`.
Independent review's initial two P1 and two P2 findings were fixed; final P0/P1 is zero.

The next slice must not redispatch. It should reacquire the four coordinator locks, deep-verify the
existing response link/object and exact result pre-head, build the unique `anchor_confirmed` or
`anchor_failed` EventSpec, append/adopt it through the recovery-only expected-pre-head CAS, and only
then publish the branch-selected recovery receipt/event. Provider idempotent POST or query-by-key
behavior remains unproven, so remote exactly-once must stay false.

## 2026-08-28 anchor-result request-intent continuation (committed `e9120d9`)

The current increment implements only the locked preparation boundary for
`anchor_result_recorded`; the result action itself remains unimplemented. The coordinator accepts
only a unique canonical `anchor_requested` event at the current live-ledger tip. For a manifest
whose initial action is the result, that request must be the frozen tip itself. For a request just
created by recovery, it must be the single event immediately after the frozen tip and remain bound
to the preceding step receipt. Aggregate request/result counts are not sufficient pairing
authority.

Before any future external action, a create-only item-intent action contract freezes the exact
request event identity, canonical POST body and digest, expected result pre-head, normalized HTTPS
endpoint, timeout/response limit, and a stable idempotency key derived from the request identity.
The bearer-token value is never persisted or hashed. A missing, malformed, non-HTTPS or
out-of-allowlist endpoint produces machine waiting without creating an immutable intent.

Once this result intent exists without a receipt, it globally fences the coordinator. Later polls
deep-verify the same intent and return `waiting_for_external_anchor_dispatch`; they perform no
network call, ledger mutation, receipt/event publication or unrelated-key progress. This avoids
placing HTTP/DNS/provider latency inside the surviving manager→cycle→replay→shadow lock scope.

The profile uses v4 intent/item-intent/receipt/status authority and states only
`live_anchor_result_request_intent_implemented=true`. It deliberately keeps
`live_anchor_result_adapter_implemented=false`, `network_recovery_implemented=false` and
`network_action_performed=false`, along with all full-recovery/lifecycle/trusted/E2/formal claims.
ConvLSTM, v4, frozen splits, metrics, thresholds and conclusions are untouched. Focused tests pass
23/23 and the seven adjacent modules pass 117/117; Ruff, formatting, compile, strict profile load
and diff checks pass.

The next slice is an unlocked bounded HTTPS dispatcher plus a create-only content-addressed response
observation. Only a subsequent locked phase may replay all authority and commit/adopt the exact
`anchor_failed` or `anchor_confirmed` event, followed by a branch-selected receipt. Until the
provider contract proves idempotent POST or query-by-key behavior, external exactly-once must not be
claimed. Full detail is in `docs/ootang_anchor_result_request_intent_engineering.md`.

## 2026-08-28 single-event anchor request recovery continuation

The recovery coordinator now supports exactly one additional transition action:
`live_outstanding -> anchor_request_recorded`. It reconstructs the unique outstanding seal,
attempt number, event key and complete canonical `EventSpec` only from the manifest-bound frozen
live prefix. The current full ledger is independently replayed and may contain a valid suffix, but
the adapter never derives a new attempt from that mutable suffix. A create-only step intent binds a
versioned action contract containing the exact expected pre-head, attempt, full EventSpec and its
canonical digest before any ledger mutation.

The adapter then calls the recovery-only transaction-internal CAS for one `anchor_requested`
event. Fresh execution requires the terminal head to equal the frozen pre-head; a crash-forward
retry adopts only the identical event at `frozen_event_count + 1`, with the frozen tip as its
predecessor. SQLite busy/locked is transient machine busy. Wrong epoch/head/position, changed
content, schema drift or chain-integrity failure blocks closed; the machine never changes the head
or attempt to make a conflict pass.

Its ActionOutput is a ledger-event fact rather than a filesystem reference, so
`action_output=null`. Receipt semantics bind event key/type/sequence, predecessor/entry hash,
epoch, seal, attempt and EventSpec digest, with `live_ledger_event_recorded=true` and
`network_action_performed=false`. The transient CAS `created/adopted` branch is deliberately not
part of receipt authority: a commit-before-receipt retry must reproduce the same receipt. Historical
receipt verification replays the immutable frozen action contract and exact ledger position while
allowing later valid suffixes; receipt-before-recovery-event crashes therefore append only the
missing recovery event.

The profile may now state `live_anchor_request_adapter_implemented=true`,
`ledger_mutation_recovery_implemented=true` and
`live_ledger_expected_pre_head_cas_implemented=true`. This is narrow implementation coverage, not a
claim that all ledger transitions or reserved keys are recovered. The misleading global occurrence
claim `live_ledger_mutated=false` is removed; the durable effect is recorded per action in receipt
semantics. Full workset recovery, all-transition support, derived-work closure, network recovery,
shadow mutation, drained/active/rotation/trusted/E2/formal authority remain false.

The necessary intent/receipt/status authority changes use schema v3 while retaining the
`workset_recovery_v1` runtime namespace. This is not an in-place migration: if immutable v2
authority already exists in that namespace, the new profile fails closed and does not overwrite,
convert or reinterpret those bytes.

No TSA/HTTP endpoint is read or called, no response/receipt is fabricated, and no manual fallback
is added. ConvLSTM, v4, frozen splits, metrics, thresholds, training conclusions and all frozen live
writer/ledger bytes remain unchanged. The default chain remains exactly
`features -> convlstm -> ootang-operational-v4`. Recovery profile/module/test SHA-256 values are
`beb5ff9c3e34f60451ee933bfd3dbcc3dcb5d398f575a24cfbd0ea811ac4a3f2`,
`3f7ccfd8bcfd85046b91ca4a210b9511ee8718e504eda939c2fcef63a26ce566` and
`6e6bc2ca87da55f4ddf923dacc9239618f469bda4bff992d656564c14898a507`;
CAS module/test values are
`b443da5fd92eb2e48e33918c3e6090be0e53fe2182584f6bb0ccee050dfb327e` and
`e040528d021e15d2b4e5dd5f41ce75a1a0027b8b3db244d5fce91106b501931d`.
The focused adapter/CAS suite passes 26/26 in 0.178 s, and the adjacent
CAS/live-ledger/recovery/manifest/admission-cut/drain-v2/main regression passes 101/101 in 1.098 s.
Ruff/format, strict JSON 35/35, the 35-stage list and default/explicit recovery dry-runs pass. The
97-path protected aggregate remains
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`, frozen writers match
11/11, and independent audit found P0/P1 0. A real live fixture also verifies `attempt=2`, exactly
one new `anchor_requested`, and successful complete scientific replay after the append. The detailed contract is
`docs/ootang_anchor_request_recovery_engineering.md`.

The next narrow transition is `anchor_result_recorded`. It requires a separately reviewed external
request-intent and lock-release fence, idempotent response-object adoption, response verification and
request/result pairing. Until that exists, the key remains machine-waiting after its nonterminal
request receipt; no human completion path is permitted. Broad issue/outcome/shadow recovery remains
deferred.

## 2026-08-28 live-ledger expected-pre-head CAS v1 continuation

The additive recovery-only module is
`code/monitoring/ootang_live_ledger_cas_v1.py`. It leaves the frozen live writer and ledger
bytes unchanged and executes schema validation, full-chain replay, frozen epoch/position/hash
verification, append or adoption, and final chain verification inside the same SQLite
`BEGIN IMMEDIATE` transaction. A fresh append is allowed only when the terminal head exactly
equals the expected frozen pre-head. A crash-forward retry is adopted only when every requested
event already exists with identical stable fields as the exact contiguous slice immediately
after that pre-head.

Storage-level adoption may accept a valid later suffix after that exact slice. This proves the
stored event's identity and chain position only; a future recovery adapter must separately replay
manifest authority, the item transition plan, locks and suffix semantics before publishing a
step receipt. CAS success is never by itself terminal transition evidence. Partial batches,
changed content/order/predecessor, wrong epoch/position, stale fresh append, schema drift or an
invalid chain fail closed and add no row.

The recovery profile binds the frozen ledger implementation and this new CAS module. Its only
new true capability is `live_ledger_expected_pre_head_cas_implemented=true`.
`ledger_mutation_recovery_implemented`, `live_ledger_mutated`, all-transition/full-recovery and
lifecycle claims remain false. No real recovery adapter calls the primitive yet; no runtime
ledger or network action was performed. ConvLSTM, v4, frozen splits, metrics, thresholds and the
frozen writer/ledger implementations were not modified, and no manual freeze, cleanup, approval,
force or backdate path was added. Shadow CAS is deliberately deferred.

Current SHA-256 values are
`23ca29356ef23483a0846e701376850745400082e607a16e2479c1057b6befa4` for the CAS module and
`246dbf18bbc24cdecb7c85d289cdf4edd069fe5e47cbcbbbfb9e27e438a4ee04` for the recovery profile.
The standalone CAS suite passes 6/6. The focused CAS, frozen live ledger, recovery, manifest,
admission-cut, drain-v2 and main suite passes 93/93 in unittest's reported 1.018 seconds;
Ruff/format/compile, strict JSON 34/34, the 35-stage list and default/explicit dry-runs pass.
The 97-path protected aggregate remains
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`, all 11 frozen writer
hashes match, and independent CAS review found P0/P1 0. This does not overwrite the committed
transition-chain 75/75 record below. The detailed contract is
`docs/ootang_live_ledger_cas_v1_engineering.md`.

The single-event machine `live_outstanding -> anchor_request_recorded` adapter described here is
implemented by the continuation above. This paragraph remains the historical pre-adapter boundary
of the committed CAS increment.

## 2026-08-28 manifest-keyed deterministic local recovery R2b-2b-2c continuation

The new explicit-only stage is `ootang-epoch-workset-recovery`. `main.py` exposes 35
selectable stages; the no-argument chain remains exactly
`features -> convlstm -> ootang-operational-v4`. Its only declared mutable output is
`runtime/ootang_epoch_registry_v1/workset_recovery_v1/status.json`; global intent, per-key
intents, receipts and previous-hash-linked events are immutable authority beneath the same
namespace. In this correction those per-key records become create-only per-step records.

Every poll acquires only the surviving ordered locks
`manager -> cycle -> replay -> shadow`, exact-replays the physical admission cut plus the
singleton reservation event/content-addressed manifest, and never re-runs inventory or opens
the sealed deploy/runner locks. A canonical DAG and global intent bind the frozen keyset,
dependency graph, initial-step adapter coverage, publisher provenance and item-specific transition
plans. Each plan binds its initial action, allowed edges, terminal actions, mutation
lanes, frozen read set and whether derived-work closure is resolved. The machine advances at
most one dependency-ready supported transition step per poll, using a SHA-256 step id,
create-only step intent, exact per-key predecessor CAS, deterministic action, create-only step
receipt and one append-only step event. A receipt with `terminal_for_key=false` can only lead to
the next allowed action for that same key; only `terminal_for_key=true` may satisfy another
key's dependency.

The existing adapter set is unchanged and remains deliberately local and deterministic:
reconstruct the exact RFC 3161 request DER from the reserved request/nonce/imprint without
network or a new nonce; reconstruct the legacy anchor receipt from the frozen verified ledger's
unique seal/confirmation; and emit a recovery-only guard supersession receipt only when the
frozen ledger contains unique durable backfill/settlement evidence. DER repair is now explicitly
nonterminal: it must wait for a reviewed machine
`trusted_time_response_link_recorded` adapter before the same key can continue toward
`trusted_time_receipt_verified`. A guard item selected by clock expiry alone remains
unsupported/waiting and never masquerades as backfill. Guard recovery never writes a legacy
completion.

Crash-forward replay covers global/item intent adoption, exact output after mutation-before-
receipt, and receipt-before-event. Every existing item intent is fully revalidated against the
current global intent, manifest, dependency receipts and implementation provenance. A missing
receipt causes the action-specific create-only adapter to probe/adopt its exact output; an existing
historical receipt is instead checked with a read-only immutable postcondition verifier, so later
valid transitions do not re-run obsolete preconditions. Canonical DER/anchor output path/hash/size,
recorded semantics, receipt chain and event reference are verified; tamper or drift fails closed.
Core/parser errors are normalized to machine-readable recovery integrity failures, and
integrity failures refresh only a non-authoritative blocked status cache.

The manifest is complete only for the six-family workset visible at the single frozen
observation and stores transition seeds; it does not claim terminal or transitive transition
closure and does not reserve keys derived by future transitions. Issue consumption that would
derive unreserved live work, source ingestion that may derive content-dependent outcomes, and
shadow genesis/rotation/classification that may derive unreserved work therefore cannot count
as terminal. The profile explicitly leaves full workset recovery, all-transition support,
derived-future-work reservation, network recovery, ledger mutation, generic/direct-filesystem
fencing, anti-rollback, external implementation trust anchor, lifecycle/transition,
drained/active/rotation/trusted/E2/formal authority false.
The first-use implementation review root remains the versioned Git checkout; once global intent
exists, its implementation hash prevents silent code drift. ConvLSTM, v4, frozen splits,
metrics, thresholds and all 11 frozen writer/orchestrator modules were not modified.

Committed baseline `8f6a9f7` had the previously recorded 76/76 focused result. For this
transition-plan correction, manifest profile/module/test SHA-256 values are
`ca3f24c91492d4cbd415331c1abf1e90bd2b971aac8016e69183652e3fb850dc`,
`8868075715188effe708fe353bf18cbefdee9060abd90f92ede8120cf5313ef2` and
`6155d557055d6492287b89d269680ad6910f1f4afe4a4cf927be3e6f23320442`;
recovery profile/module/test values are
`2b956d96d3aa3049a7901e21a250743aaa90701e41a814fe98e5c91936fdcf5a`,
`d2492750062ce0151f05856fbff4af9ca53b357279a3141679e90cc75af18911` and
`caa2b61943227046e0359a11a8cd6fb9d54c59bab69c5103a4af675db34e30cb`.
The transition-contract SHA-256 is
`c7ecf9e5e553d54b90017f32aeab1546a7ee7f5d42dbc54e263fc0be1cd152d0`.
The focused recovery/manifest/admission-cut/drain-v2/main suite passes 75/75 in 1.015 s.
Ruff/format/compile, strict JSON, diff check, stage list and default/explicit dry-runs pass;
the default remains exactly `features -> convlstm -> ootang-operational-v4`. The 97-path
protected aggregate remains
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`, all 11 frozen
writer/orchestrator hashes match, and final independent review found no remaining P0/P1.
No model training, TSA network, real runtime mutation, network/ledger recovery action, full
repository suite, capacity run or exhaustive filesystem/crash matrix was run.

The expected-pre-head CAS primitive described as this correction's next slice is now implemented
in the continuation above without invoking a real adapter. The current next slice is only the
single-event machine `anchor_request_recorded` adapter; broad issue/outcome/shadow or network
recovery remains deferred. Only terminal receipts plus explicitly resolved derived work may
eventually let an independent post-recovery assessor consider current quiescence; it still cannot
infer active transition directly.

## 2026-08-27 closed-workset manifest reservation R2b-2b-2b continuation

The new explicit-only stage is `ootang-epoch-workset-manifest`. `main.py` exposes 34
selectable stages; the no-argument chain remains exactly
`features -> convlstm -> ootang-operational-v4`. The stage follows
`ootang-epoch-admission-cut` and publishes only its mutable status cache at
`runtime/ootang_epoch_registry_v1/workset_manifest_v1/status.json`; immutable authority is
the content-addressed manifest plus singleton reservation event.

The machine first exact-replays the admission-cut prepare, intent, previous-hash-linked
attempt chain, singleton event and both physical deny-write sentinels. It restores the
frozen R1/R2a candidate/slot, old epoch and live upper tip from the terminal attempt. Since
the deploy/runner canonical paths are already sealed regular files, this stage never calls
their old public polls or attempts to acquire them. It takes only the still-open ordered
locks `manager -> cycle -> replay -> shadow` before capturing the stable workset.

The manifest always contains all six family descriptors: `issue_route_replay`,
`live_outstanding`, `outcome_revision`, `guard`, `trusted_time` and `shadow`. Every item is
deterministically ordered and binds an exact natural key, allowed successor, dependency
keys, contained path/hash/size artifact references and a namespace digest. A family may be
empty, but no descriptor may be omitted. Unknown/orphan/duplicate/branch/overflow,
unresolved dependency, path escape or reference mismatch fails the entire capture closed;
the publisher never truncates or emits a partial manifest.

The manifest is canonical and content-addressed; poll time is excluded. The create-only
singleton event binds its exact path/hash/size plus the admission-cut event, terminal
attempt, authority context and publisher implementation provenance. Same-state repolls are
byte-idempotent. Before the event exists, a changed/orphan enumeration blocks publication;
after publication, replay verifies only immutable event/manifest bytes and internal digests,
not mutable predecessor paths. Each future adapter must exact-CAS its own reserved key before
acting, so completing item 1 cannot invalidate the authority needed for item 2. A second
event or manifest/event/reference tamper still blocks. Status is cache only; waiting/blocked
status never reports current enumeration/reservation as complete.

The inventory is read-only and covers terminal plus pending namespaces. Live/shadow SQLite
authority is the replayed logical chain rather than WAL-sensitive database-file bytes. Shared
objects and TSR objects are scanned for exact reachability; missing/extra anchors, orphan
objects, partial guard boundaries and shadow coverage gaps fail closed. A request-only TSA
crash is reserved as deterministic DER repair with the same nonce/imprint. A guard intent
whose target became historical or whose outcome arrived before open/seal is reserved as
`superseded_by_backfill`, so neither case needs manual cleanup.

This event sets only complete enumeration and bounded reservation capabilities true. It
does not execute action adapters, create outcomes, mint a new TSA nonce/DER, or claim
generic/direct-filesystem admission fencing, recovery, anti-rollback, lifecycle,
transition, drained, active, trusted, E2 or formal-warning authority.

Final SHA-256 values for profile, inventory helper, manifest publisher, inventory test and
manifest test are respectively `19ddf6091ecf348bc609796a672734b67b91d1e4d8b6275604a03c7fba2a56b7`,
`cb4ce5c3e734aca6da1fccf8c07fca6e313cece177a096232626fea15011c982`,
`5e1e170473189d03d951b43a0e3258f4cf7d3cdff33c3dcb807206d1612d5e37`,
`37538d26f9db5f78ed40ae839dca7c9546d8340df0a5817eb8df9aa39e184e5a` and
`bee588aa54d881da1a97a0fbf4329770a1e0cc8ef44156b496979ed3c1b14a3b`.
The focused inventory/manifest plus adjacent admission-cut/drain-v2/main suite passes
`66/66`; Ruff/format/compile, JSON, dry-runs and protected/frozen-writer checks are the only
additional verification. Model training, TSA network, full repository, full capacity and
exhaustive filesystem/crash matrices are intentionally not rerun. Final independent short
audit reports no remaining P0/P1.

The immediate next slice is manifest-keyed machine recovery. An adapter may act only on
one exact reserved natural key and its declared successor, publishing a step receipt after
replaying the same fence generation and dependency closure. Network trusted-time recovery
must reuse the reserved request/nonce/DER (or deterministically repair its reserved DER) and
exact-CAS after reacquiring the recovery lock.
Only after every reserved item is settled may an independent assessor evaluate quiescence.

## 2026-08-27 official-writer lock-path admission cut R2b-2b-2a continuation

The new explicit-only stage is `ootang-epoch-admission-cut`. `main.py` exposes 33
selectable stages; the no-argument chain remains exactly
`features -> convlstm -> ootang-operational-v4`. The stage follows
`ootang-epoch-drain-v2-workset` and publishes only the mutable cache
`runtime/ootang_epoch_registry_v1/admission_cut_v1/status.json`.

The implementation deliberately does not patch the 11 frozen writer/orchestrator files.
Those files are self-bound by the live ledger, verified-live intents, replay receipts,
trusted-time/shadow records and producer provenance, so changing them would make the old
epoch being drained unverifiable. The reviewed profile binds and rechecks every frozen
SHA-256 before doing anything.

Instead, the machine takes the globally ordered locks
`manager -> cycle -> deploy -> runner -> replay -> shadow`, prepares two regular-file
sentinels with exact mode `0444` and the reviewed `everyone deny write` ACL, then records
create-only prepare, inode-bound intent and previous-hash-linked machine-current attempts.
Darwin `renameatx_np(RENAME_SWAP)` exchanges canonical `deploy_cycle.lock` first and
`runner.lock` second with their sentinels; ordinary rename has no fallback. The first cut
closes source/issue/outcome admission, and the second closes live/guard/replay/trusted-time/
shadow admission for the frozen official entrypoints.

Recovery is forward-only. Before the first physical cut, any v1 authority wins and the
transaction remains inert. After a deploy-only crash, the next poll never acquires/flocks
the sealed deploy path: it only performs a denial probe whose success is an integrity error,
acquires the remaining locks, and captures a fresh monotone context in a
second attempt, cuts runner and publishes the singleton event. After runner is cut, only
manager/cycle are needed to exact-replay and finish the event. There is no restore,
unfence, cleanup, force, approval, target date or backdate interface.

The event proves only that the two official writer lock pathnames are physically cut.
Direct filesystem bypass and unknown writers are outside this slice; complete enumeration,
reservation/recovery, generic old-work admission fence, v1/v2 mutual exclusion,
anti-rollback, lifecycle/transition, drained/active/trusted/E2/formal claims all remain
false. Mutable status is cache only.

Frozen candidate profile/module/test SHA-256 values are respectively
`fe4e91768c8558d887a34465fa6c9f4e8c05f8c1a7cf07e061bc602733136c1c`,
`95b675b132c5051cbbc4d34041b9686d122a64c6368f04c0bff8d6dddf1effcf` and
`a26c7ebc476d0931ed0373f2b2fd5e6bc8255ed19ee9c3276a545a2a834ed8b3`.

Fast Darwin tests cover real ACL/regular-file exchange and byte-idempotence, all seven
transaction crash points, deploy-only context extension, manager/runner busy zero-write,
v1 precedence, prepare/sentinel/intent/attempt/event tamper and the machine-only CLI.
The scoped admission-cut plus main suite passes `45/45` in about one second; no R2b,
NGBoost, real non-clean chain or repository-wide long suite was rerun.
Ruff/format/compile, strict JSON `32/32`, both lock checks, the 33-stage list and
default/explicit dry-runs pass. The 97-path protected aggregate remains
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`; the shared v4
Bai--Perron source and all 11 frozen writer/orchestrator files have no diff.
Final independent authority/standards audit is P0/P1/P2 `0/0/0`.

The content-addressed manifest described as this historical slice's next step is now
implemented in the continuation above. The corrected scope is complete enumeration of the six
families visible at one frozen observation plus transition seeds under the stable
official-writer boundary; it is not a transitive or terminal closure. The recovery-only
expected-pre-head ledger CAS is now implemented without a real adapter. The current next step is
the narrow machine-only `anchor_request_recorded` adapter. No recovery may precede the singleton
manifest reservation event or bypass an item transition plan.

## 2026-08-27 drain v2 first-blocker observation R2b-2b-1 continuation

The new explicit-only stage is `ootang-epoch-drain-v2-workset`. `main.py` exposes 32
selectable stages; the no-argument chain remains exactly
`features -> convlstm -> ootang-operational-v4`. The stage is ordered after
`ootang-epoch-drain-eligibility` and before operational v4, but is never selected by
default. Its mutable liveness output is
`runtime/ootang_epoch_registry_v1/drain_v2/status.json`.

This increment corrects an unreachable roadmap assumption. V1 R2b publishes
`epoch_drain_started` only from a full-clean start, while R2b-2a requires that unique v1
authority. Non-clean R2b-2b therefore cannot be a normal linear successor of R2b-2a.
The v2 foundation instead observes the first pending family returned by the frozen v1
clean-state gate under the same nonblocking lock order
`manager -> cycle -> deploy -> runner -> replay -> shadow`.

The create-only, content-addressed observation binds the exact R1 and R2a event tips,
candidate/slot, old live epoch id, live event count and live ledger terminal. It recognizes
six closed family names (`issue_route_replay`, `live_outstanding`, `outcome_revision`,
`guard`, `trusted_time`, `shadow`) but the production inspector records only the first v1
gate blocker. Replay exact-validates the singleton event and dereferences the observation
path/hash/size/schema/context/items before any v1-precedence decision. Same context/blocker
repolls are byte-idempotent; a changed blocker/context makes the immutable observation
stale and inert rather than silently reusing it.

Any v1 fence/intent/WAL/armed marker/event/eligibility/capsule or v1-specific drain object
prevents v2 adoption. If v1 authority appears after the observation, v1 has precedence and
the v2 observation becomes inert. This is deliberately not mutual exclusion: frozen v1
does not read the v2 namespace. Lock busy writes no v2 event/object/head/status. Head/status
are repairable non-authority caches and provide no anti-rollback guarantee; deleting both
durable event/object is outside this slice's detection boundary.

Capabilities therefore state `first_blocker_observation_implemented=true` and bind the
R1/R2a plus old-ledger context, while bounded workset reservation/recovery, complete
enumeration, old-work admission fence, v1/v2 mutual exclusion, anti-rollback authority,
`epoch_drain_started`, canonical route fence, drained/active/trusted/E2/formal claims all
remain false. No public producer/recovery poll or network call is executed, and the CLI
has no date/freeze/approval/cleanup/force/backdate control.

Frozen profile/module/test SHA-256 values are respectively
`aa12082e32b9b94fc4ad4b08232ed586b49c8c1bf1d2ccdaf3587b096c047d17`,
`93a6463f514d73c4809287e1bbc8033984c82f550d5c55ce84c209475d7b604b` and
`d315d394b536eec4b1ea09c7a9683cfcbc0ebadc58a98758268f32d3329ed488`.
Targeted v2+main validation passes `45/45`; no R2b or repository-wide long suite was run.
Ruff/format/compile/strict JSON, both lockfiles and default/explicit dry-runs pass; the
97-path protected aggregate remains
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3` and the shared v4
Bai--Perron source has no diff.
The next implementation step is a shared machine-enforced old-work admission cut plus
complete manifest enumeration. Only then can a new version honestly reserve a closed
workset and add manifest-keyed guard/trusted-time/outcome/live/shadow action adapters.

## 2026-08-27 drain eligibility observer R2b-2a continuation

The new explicit-only stage is:

```text
ootang-epoch-drain-eligibility
```

`main.py` now exposes 31 selectable stages. The no-argument chain remains exactly
`features -> convlstm -> ootang-operational-v4`; the new stage is immediately after
`ootang-epoch-drain` and before operational v4, but is never selected by default. Its
mutable machine-readable result is
`runtime/ootang_epoch_registry_v1/drain_eligibility_status.json`.

The frozen profile/module/test SHA-256 values are respectively
`2dbda86a747ef486bc06d5c4901e3356404c0079ec2b8aecf91eb61afc91413a`,
`46b036aa5e530d0dce50e87b6e4988d67eae1ba4ca67a2d4a09e990f4eaa29bc` and
`4e247497ca78a4449ae00769ad1c383c5b5279195c357edcf84e29af4e5754a4`.
Recompute these after any code/test edit before treating them as frozen.

R2b-2a restores the historical R1/R2a selector from the persisted unique R2b event; it
never calls the R2b start/swap path or substitutes the current registry tip. Under the
same nonblocking six-lock order `manager -> cycle -> deploy -> runner -> replay ->
shadow`, it fully replays the event, intent, capsule, exact pre-swap boundary,
exchange-attempt WAL terminal, armed marker, canonical fence and archived old-runtime
state. It rejects a post-fence outstanding issue and requires the archived issue
inventory to remain exact. Every historical clean observation also dereferences and
revalidates its frozen semantic/activation/snapshot source objects; self-consistent
metadata alone is insufficient.

The observer publishes a deterministic, content-addressed clean-state observation and
then a previous-hash-linked eligibility event. Observation bytes contain neither poll
time nor staged next-epoch incoming. Same-state repolls are byte-idempotent; a strict
settled extension is double-captured and appends one new event; pending work or a
prospective observation above 64 MiB produces machine waiting/current=false without an
event. Event time cannot precede the R2b event or the prior eligibility tip. Crash temps
are cleaned only after all locks; unknown temp entries fail closed.

Head/status are repairable, explicitly non-authoritative caches. On integrity failure
the machine best-effort writes `blocked_integrity/current=false`; a fully valid cache
strictly ahead of the replayed chain is retained as a rollback witness, while any cache
at or behind the replayed count must match the real chain entry exactly. Thus a forged
same-count status tip is not promoted into a persistent authority. Simultaneously
deleting an event suffix and every mutable ahead witness remains an explicitly
documented v1 detection limit, not a solved property.

Observation/event explicitly state `observation_authority_only=true`,
`lifecycle_authority=false` and `transition_authority=false`. All old-drained,
activation-selection, active-switch, rotation, trusted-anchor, E2, real-activation and
formal-warning claims remain false. The event records only that the clean DRAINING state
was current at publication; every future assessor or transition must reacquire the locks
and exactly recheck machine-current state.

Verification passes public eligibility `20/20`, full eligibility including real R2b
authority integration `21/21` and main `35/35`. Do **not** rerun the whole repository for
this scoped increment: committed baseline `2eefceb` already has `809/809`; an additional
full-discovery run was intentionally interrupted after `4184.67` seconds while executing
an unrelated NGBoost horizon-sensitivity test, with no failure/error reported before the
interrupt. It is not a completed full-suite result and must not be cited as one.
Remaining engineering debt includes per-lock/per-fsync
fault matrices, inherited private R2b API coupling, full-chain O(K^2) replay, and the
mutable-witness deletion boundary above.

The next slice described at this R2b-2a point started as the R2b-2b-1 observation section
above. R2b-2b-2a later completed the frozen official-writer lock-path cut; R2b-2b-2b then
reserved the six-family frozen observation and transition seeds, and R2b-2b-2c added the first
keyed local adapters plus the current step-chain correction. The recovery-only expected-pre-head
ledger CAS is now implemented without a real adapter; the current next step is the narrow
machine-only `anchor_request_recorded` adapter. These later versions must not overwrite or reinterpret any
v1 R2b or R2b-2a bytes. Only after terminal closure and derived work are explicitly resolved may
an independent drain assessor run; an authoritative atomic `SEALED(old)+ACTIVE(new)` transition
comes later still.

## 2026-08-27 epoch drain-start barrier R2b first slice

The new explicit-only stage is:

```text
ootang-epoch-drain
```

At this historical R2b baseline, `main.py` exposed 30 selectable stages. The no-argument chain remained exactly
`features -> convlstm -> ootang-operational-v4`. The frozen R2b profile, module and test
SHA-256 values are respectively
`1da0056c8cbdc0fe30b8adca5b72cf52e211b679aae8b8981216e44c0f16d105`,
`c4c226da087d354195fc3955ff60ff3b12af13f111d03cb59c5d64d0195a1602` and
`b79d132562891a3134d55073a282b351533e525b0661cd47914698e323612d68`.

This first R2b slice owns a separate drain lifecycle namespace: drain events, replayed
head/status caches, create-only singleton fence prepares, intents, capsules, append-only
`drain_exchange_attempts` WAL and an overlay, plus content-addressed full intent-prefix,
staged-feed and full-clean boundary objects in the shared object store. It references and revalidates the immutable R1
registry and R2a preparation tips but does not rewrite their events, capsule, objects,
materialized tree or smoke receipts. Mutable drain head/status are cache only. A drain
intent is a durable transaction reservation/lower-bound; `candidate_at_intent` records
which R1/R2a candidate that transaction bound and is explicitly not activation
selection. The WAL, armed marker and boundary have recovery authority only; an orphan
fence prepare, intent-prefix, intent, capsule, staged-feed, attempt or boundary object
has no lifecycle authority. The sole DRAINING lifecycle authority remains the
`epoch_drain_started` event.

The fencing critical section acquires every participating lock in one fixed order:

```text
manager -> cycle -> deploy -> runner -> replay -> shadow
```

This prevents the known producer, cycle, live runner, checkpoint replay and calibration
shadow entrypoints from crossing the route transition. Failure to acquire any lock is a
busy outcome, never a partially acquired drain transition.

Version one deliberately accepts only a clean start. There must be no outstanding old-
epoch issue and no pending guard, trusted-time or shadow work. If any work remains, the
machine waits without changing the canonical route. Old-epoch work may continue between
scheduler polls: the next poll still waits if it remains pending, while a legal append-
only settled extension is replayed and automatically included in the next clean-state
boundary. There is no operator target date, freeze, approval, force, backdate, fabricated
outcome or manual cleanup path.

After the clean-start checks, the machine captures the exact unfenced old-route identity
and, before any tombstone `mkdir`, create-only publishes the unique
`drain_fence_prepares/<candidate_id>.json`. This permanent marker binds the historical
R1/R2a entries, drain capsule and its full intent-prefix, old-route identity, tombstone
path, ACL digest/mode and mandatory swap policy. After a crash, or if the current R1/R2a
tip advances, the machine must resume the same historical transaction from this marker;
the marker itself is not lifecycle authority and is never activation selection.

Only after that marker is durable does the machine prepare an empty mode-`0755`
tombstone, install and exactly read back a Darwin extended ACL denying `everyone` write,
and prove the fence with a real add-file denial probe. It then persists the bound drain
intent and, under the complete lock set, revalidates the epoch/R1/R2a/route/empty-state/
ACL facts. After the capacity, exact-boundary, WAL and armed-marker sequence described
below, macOS `renameatx_np(RENAME_SWAP)` atomically exchanges
that inode with the canonical old-epoch `issue_inbox` across their parent directories, so
the ACL follows the inode and fences the canonical route immediately. The machine then
hardens that route in place to exact mode `0555` and rechecks both ACL and denial probe.
A crash between swap and chmod can only resume forward hardening and must never swap the
directories back. There is no fallback to ordinary renames, move/copy/delete or any
non-atomic/unfenced emulation.

R2b keeps two boundaries distinct. The physical issue-admission boundary is the instant
Darwin `RENAME_SWAP` succeeds and moves the deny-write inode onto canonical
`issue_inbox`. The authoritative state-snapshot boundary is the content-addressed object
created from a full-clean pre-swap replay under all six locks and then referenced by the unique
`epoch_drain_started` event. It binds the live ledger, issue route/producer receipts,
verified guard, trusted-time, outcome registry, calibration shadow, current source
authority and staged next-epoch incoming inventories. Staged incoming is observed but is
not old-epoch source authority; an unreferenced boundary object is only an orphan.

The capsule references a content-addressed full intent-prefix manifest containing every
live/shadow entry hash and the issue/outcome/guard/trusted-time/source inventories. Each
poll proves that current state is an append-only extension of that start prefix; legal
settled extensions are included in the final boundary. Manifest reads have an explicit
64 MiB maximum and staged next-epoch feeds an explicit 16 MiB maximum. A staged feed is
its own content-addressed object and the boundary stores only its reference, so a legal
large feed cannot self-lock event replay behind the smaller control-object limit. Before
event publication, the machine fully self-replays the boundary and all references and
requires an exact reconstruction of the state being committed.

Before swap, a worst-case capacity preflight serializes the full boundary with a
maximum-16-MiB staged-feed CAS reference. If that boundary exceeds 64 MiB, the machine
returns `waiting_for_drain_boundary_capacity`: canonical `issue_inbox` remains on its
original inode, no event is written and no manual cleanup is requested. After that
preflight, the irreversible tail is exactly final fence verification → stable two-read
capture/CAS of the actual staged queue → actual boundary-capacity check → publish the
exact pre-swap full boundary → append/replay its `drain_exchange_attempts` WAL terminal →
write `.epoch-drain-armed-attempt.v1.json` inside the fence operand to bind that terminal
and boundary → immediate Darwin swap. The marker moves atomically with the fence inode.
The ordinary same-poll post-swap logical clean state must exactly equal the
pre-swap state, and the post-swap publisher/self-replay repeats the checks. Only recovery
from an already-exchanged crash may accept a proven append-only extension from the
immutable start prefix to recovery-current.

Every exchange attempt is previous-hash-linked and references one exact pre-swap full
boundary; only the unique WAL terminal may be armed. A prepared retry can automatically
recover the strictly recognized single marker-temp and exact/missing ACL crash states,
without operator cleanup. After exchange, recovery must select the terminal from the
marker that moved with the fence and reuse that terminal's **old boundary**. The current
clean state is only an append-only-extension gate; recovery must not rebuild or replace
the boundary. WAL suffix rollback, branch, sequence gap, extra entry, symlink, nonterminal
marker or unknown temp/ACL state fails closed. Growth beyond the 64 MiB v1 boundary limit
waits before swap; any historical chunk/Merkle scheme belongs to a future R2b-2b v2 and
must not reinterpret v1 bytes.

The resulting lifecycle state is only `DRAINING`. `activation_candidate_selected`, all
drained/active/rotation/trusted-anchor/E2/real-activation/formal-warning claims remain
false. The event does not claim `SEALED(old)` or `ACTIVE(new)`.

The real-default R2a attempt already recorded below remains a required negative result.
Five full checkpoints were built, but R1 correctly rejected the historical one-record
feed at its causal publication gate with `Bundle was not durable before the first target
natural day`. The repository lacks a finalized feed continuous from 2020-07-01 through
the machine-current date, so no R1 candidate and no real R2a/R2b end-to-end PASS exists.
Do not change the clock, synthesize dates, manually backdate or use a test override to
turn that evidence into a green run.

Final R2b verification is: focused `12/12` in 1008.486 seconds (1025.68 wall),
R1+R2a+R2b+main `116/116` in 2361.831 seconds (2393.77 wall), and the full repository
`809/809` in 5568.360 seconds (5642.18 wall), all with zero failures/errors. Main is
`34/34`; the frozen v5 preflight is `23/23` and remains G0 PASS, G1--G4 BLOCKED,
G5a not evaluated/authorized and formal warning false. Ruff, scoped format, compileall,
diff-check, strict JSON `30/30`, both uv lock checks, the 30-stage list and default/
explicit dry-runs pass. The 97 protected paths retain aggregate
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`, and the shared
v4 Bai--Perron source has no diff. Final independent standards/specification review is
P0/P1/P2 `0/0/0`; the remaining per-object publication-fsync fault matrix is coverage
debt rather than a known implementation defect.

The next slice from this historical R2b baseline was R2b-2a clean-start eligibility
observation/stale detection; it is implemented in the continuation section above.
The original roadmap next called for a new R2b-2b v2 recovery schema. The continuation
above records the corrected first step: a non-authoritative first-blocker observation.
The admission cut is now implemented in R2b-2b-2a; complete enumeration and keyed recovery
remain. V2 must not
reinterpret, add fields to or overwrite published v1 fence-prepare/
intent-prefix/capsule/intent/exchange-attempt/armed-marker/boundary/event bytes. Only a later drain assessor and
authoritative transition may seal the old epoch and activate a candidate. Cycle v4,
scheduler authorization and O(N^2)
long-chain scan optimization remain later gates.

## 2026-08-27 epoch executable preparation R2a continuation

The new explicit-only stage is:

```text
ootang-epoch-preparation
```

At the R2a baseline, `main.py` exposed 29 selectable stages. The no-argument chain remained exactly
`features -> convlstm -> ootang-operational-v4`. The R2a profile and implementation
SHA-256 values are respectively
`c6ec0b1f340effd9e3fd5cd1a0ee67ebca9ffa4dc743a9cb36d701850dc875f9` and
`b03182accc3e8d482683eda29c7c07bdb66f7a316dfa99a41f29e1b99b3fe209`.

R2a consumes only the replayed immutable R1 tip. It resolves the exact static local
import closure rooted at cycle-v3 and trusted-time shadow/core. The closure must be
exactly 22 reviewed `convlstm`/`monitoring` modules. The only files it may capture
from the current project because they were absent from the R1 capsule are the pinned
`code/convlstm/__init__.py` and `code/monitoring/__init__.py` augmentations. Dynamic
or wildcard local imports, a third augmentation, a changed module count/order, or a
self-consistent capsule that drops a root module fail closed. The exact 22-module
list is recorded in `docs/ootang_epoch_preparation_engineering.md`.

The executable capsule binds the R1 profile/event/candidate/slot/live-epoch identity,
the exact closure/resource tree and every logical artifact's SHA/size/source/role.
R2a also captures its own profile bytes and implementation bytes into the shared
content-addressed object store; capsule, smoke receipt and preparation event cross-bind
those path/SHA/size references. Historical events continue to replay against their
captured implementation object after a coordinator upgrade. The upgraded coordinator
must rerun closure resolution, materialization and smoke for the same R1 candidate and
append `candidate_revalidated`; it never overwrites `candidate_prepared`.

This is deliberately a same-origin preflight. Existing source/model manifests bind
the final absolute `slots/<slot-id>/live` path, so both canonical project root and
canonical slot root are mandatory. The capsule says `relocatable=false` and
`portable_offline_runtime=false`. Its exact materialized logical tree contains no
`.venv`, CPython/uv binary, OS or wheel cache; it is not an air-gapped bundle or a
cross-machine recovery image.

The production smoke runner pins uv 0.12.5 at
`/opt/homebrew/Cellar/uv/0.12.5/bin/uv` with SHA-256
`debc68c21b3bb1086e20d9889b53ff5ccf9ef343fda9a57dc2022212e3511125`,
CPython 3.10.20 with executable SHA-256
`694bcacb03f978975c57396caaec10a42d3fec62a789f82f8661197c9dd17a2e`,
SOABI `cpython-310-darwin` and platform `macOS-26.5.1-arm64-arm-64bit`.
It runs two separate `uv --no-config run --isolated --frozen ... python -I -B`
domains: the root 43-distribution inventory
`007dc4fbca73360ff0ca20b744509d2a3b0fbd44711234e64c35715032ebc34e`
and the trusted-time five-distribution inventory
`622737b4a53f420c3e895e4d74205b456fd7efa15b0e4a9250e760c7c24264c5`.
Both must match the pinned Python hash, SOABI and platform.

The root smoke compiles all 22 Python files, imports cycle-v3/trusted-time shadow from
the materialized tree, reloads live/source/model prerequisites from the canonical slot,
and rederives the candidate live epoch id. It then calls the reloaded bundle's
`predict_p50()` for seeds 0..4 and compares every value with training-manifest
`reload_replay`: exact seed order, exact finite station set/order, `rtol=0`,
`atol=1e-6 mm`. The second frozen domain imports trusted-time core from that same tree.

Every current repoll revalidates the R1 receipt/current artifacts/empty future
namespaces, capsule and exact tree, reruns both current smoke domains, and compares the
result with the immutable smoke receipt. A receipt orphaned by a crash before event
append is not promoted directly: current smoke is rerun and compared first; environment
drift blocks recovery. Immediately before event publication R2a again verifies current
R1/R2a profile and implementation bindings, R1 candidate, capsule, tree and smoke.
It then replays the appended chain, requires the new event to be the unique tip, and
rebuilds mutable head/status caches. The canonical config file itself and every existing
parent from project root must be non-symlink even when aliased bytes would hash equally.

R1/R2a share a non-blocking manager lock. Busy maps to exit 3. Contract/integrity
failures inside the normalized R2a scope map to exit 2; a documented subset before
lock/profile normalization can still traceback/exit 1 while failing closed. The production API
accepts only reviewed config, and the CLI has no
runtime/candidate/date/freeze/approve/force/backdate/smoke override. All of these remain
false in capsule, receipt, event and status:

```text
old_epoch_drain_implemented
active_epoch_switch_implemented
automatic_epoch_rotation_implemented
trusted_anchor_receipt_verified
e2_live_evidence_eligible
real_activation_ready
formal_warning_output
```

Final verification is: epoch-preparation `30/30` in 590.587 seconds; preparation +
pipeline `63/63` in 597.651 seconds; and full repository `796/796` in 3047.808 seconds,
all with zero failures/errors. Ruff, compileall, R2a scoped format, diff-check, strict
JSON `28/28`, both frozen lock checks, default/explicit dry-runs and the 29-stage list
pass. Formal-v5 preflight is `23/23` and remains G0 PASS, G1--G4 BLOCKED, G5a not
evaluated/unauthorized and formal warning false. All 97 protected paths have no diff
against HEAD, and their measured aggregate remains
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`.

A separate isolated run used the real R1 default prebuilder with all five seeds and
120 epochs, without a clock override or test-epoch shortcut. All five content-addressed
checkpoints completed, after which R1 correctly failed closed before candidate publication:
`Bundle was not durable before the first target natural day`. The repository does not
contain a machine-finalized daily feed continuous from 2020-07-01 through 2026-08-27,
so the one-record target was already historical. No R1 candidate or R2a default smoke was
therefore published. This is not reported as an end-to-end pass and must not be bypassed
with a changed system clock, synthetic date fill, manual backdating or a test-epoch override;
the real default-smoke gap remains a documented P2 pending genuinely current feed.

Current capsule/lifecycle review found no new P0/P1. Documented non-blocking P2
boundaries remain: the finite AST denylist needs a stronger future allowlist; only the
replayed event chain (never an orphan capsule/tree) is authority; root smoke compiles
all 22 modules but imports only closure roots, while trusted smoke imports core rather
than the launcher or an independent crypto self-test; same-UID noncooperating writers,
path TOCTOU and full-history rewrites are not completely defeated by the manager lock
and local hash chain; distribution inventories bind name/version rather than extension
or system-library bytes; an exception may leave a stale status cache, so freshness
requires this poll to exit successfully plus event replay; and some pre-lock/profile
`RegistryError` paths are not yet normalized to R2a CLI blocked. These limitations do
not change authority, `trusted_anchor_receipt_verified=false`, or
`portable_offline_runtime=false`; details are in the R2a engineering document.

The subsequent R2b first slice now implements the clean-start canonical route fence and
`epoch_drain_started`, but deliberately stops at DRAINING. Non-clean historical trusted-
time/guard/shadow recovery and the drain assessor remain before any atomic active
transition. There is no human date, freeze, approval or force path.

## 2026-08-26--27 immutable epoch registry R1 continuation

The new explicit-only stage is:

```text
ootang-epoch-registry
```

At the R1 baseline, `main.py` exposed 28 selectable stages; the default chain remained exactly
`features -> convlstm -> ootang-operational-v4`. R1 derives a stable slot from the
fixed candidate feed and reviewed contract. Before any long build it independently
checks the complete frozen Ootang feed/record schema, finalized/time/natural-day/
contiguous-extension/numeric/eight-station contract and appends a create-only feed
observation chain with a recoverable tip witness. Each observation event embeds and
hashes the exact raw feed bytes, making the watermark a single-file durable commit;
the content-addressed feed object is a recoverable derivative. Thus a newer feed remains the
anti-rollback watermark even if its build waits, its receipt is orphaned, or the process
crashes; invalid/future feeds never enter the chain. R1 then copies the feed create-only
into the final slot, materializes the source and five-seed model bundle there, reloads
the public source/model/prerequisite contracts, and only then records a candidate-ready
receipt. There is no date/freeze/approve/force/backdate/manual-signature interface.

The current live-v1 ledger cannot host a second genesis, and existing artifact
manifests contain final absolute paths. Candidate build therefore uses a stable
`slots/<slot-id>/live` namespace and is never moved. R1 adds no candidate ledger,
issues or outcomes. Its content-addressed archival byte capsule binds the fixed
code/config allowlist, root and isolated dependency locks and trusted-time trust
material; the candidate receipt jointly binds source/model artifacts and the rederived
live epoch identity. Feed and every declared candidate artifact are copied into the
registry object store, so later legal slot mutation does not invalidate historical
replay. The capsule is an explicit archival provenance allowlist, not a transitive or
materialized executable old-epoch tree. Candidate-ready events form a strict create-only
N-to-N+1 previous-hash chain; mutable heads/status are recoverable caches. The event is
named `candidate_ready`, while status deliberately says
`immutable_candidate_record_ready` rather than claiming current-slot activation
readiness. Implementation/profile SHA-256 values are
`1418b754012b71b296c200539efa846cea63e5a4374e44d216bd656f8b047b9c` and
`c56004649689ee8aa4beeca6a7c61bbdd5529ec62706880ea8eac65a8ca18edd`.

Production `poll_epoch_registry()`/CLI accepts no runtime/feed/prebuilder override. The
private test entry rejects the project production runtime tree. Candidate commit
rechecks current slot feed/artifacts and empty future namespaces immediately before
event publication. Startup under the manager lock removes only exact regular crash-temp
names (including hard-link aliases) and fsyncs `.tmp`; unknown entries fail closed.
Historical capsule verification accepts the implementation object captured at that
time, so a future registry implementation update cannot self-lock old events.

All `automatic_epoch_rotation_implemented`, trusted-anchor, E2 evidence, real
activation and formal-warning claims remain false. Historical replay verifies immutable
feed/artifact/capsule objects, candidate/live identities and the two hash chains; it is
not a persisted public-loader executable closure and does not rerun training or every
scientific forward. Final verification is registry `40/40`, registry+pipeline `72/72`,
and full repository `765/765` in 344.829 seconds with zero failures/errors. Ruff,
scoped format, compileall, strict JSON `27/27`, root/isolated lock checks and diff check
pass. v5 preflight is `23/23` and remains G0 PASS, G1--G4 BLOCKED and G5a unauthorized.
The 97 protected paths have no diff and retain aggregate
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`.
Two independent read-only R1 audits ended at P0/P1 `0/0`; trusted-writer/power-loss,
cumulative O(N^2) replay and the then-missing executable closure were explicit P2
scope. R2a now closes the exact same-origin closure/materialization/smoke slice without
changing the R1 chain; its remaining P2 boundaries are recorded in the R2a engineering
document.

R2a and the subsequent R2b clean-start route fence now close the executable-preparation
and drain-start slices without changing this R1 history. R2b still needs non-clean
trusted-time/guard/shadow recovery and an assessor for existing transactions. Missing
outcomes remain machine waiting; no forced rotation or fabricated settlement is
allowed. Only a later transition slice may commit `SEALED(old)+ACTIVE(new)` and allow
cycle v4 to consume the registry/preparation tip.

## 2026-08-26 RFC 3161 trusted-time shadow continuation

One explicit-only stage was added without changing the default pipeline:

```text
ootang-trusted-time-shadow
```

`main.py` now exposes 27 selectable stages; its no-argument chain remains exactly
`features -> convlstm -> ootang-operational-v4`. The stage timestamps only a
replay-gated verified-live completion and its exact live issue seal. It is standalone,
additive, absent from cycle v3, reads no outcome, and has no operator date/freeze/
approval/backdate/signing interface.

The v1 protocol fixes the Sigstore production RFC 3161 endpoint, policy OID
`1.3.6.1.4.1.57264.2`, SHA-256 message imprint, a machine-generated 256-bit nonce,
`certReq=true`, exact GRANTED status, mandatory accuracy at most one second, and a
versioned `Asia/Shanghai = UTC+08:00` target boundary. Trust is pinned to
`sigstore/root-signing@ba3066c420970c13772ba0625f09f1ec97193116`. Manifest, leaf-DER
and root-DER hashes are respectively `33d22cc6dbdf8bf016b0cb96e291ca4538109ffb5d22a639edb34d2f42c80eef`,
`85f927bc07ab62cac3b44356c10efc81b2c6883fda7ab9e6d870d9d13acd05b7`, and
`2aca8fea5d3ce48b01cc77076293c280e6c23ffe44034757ee7833ca9f45d633`. Core constants
reject a coordinated config/manifest/self-signed trust swap.

The historical root `pyproject.toml` and `uv.lock` remain byte-identical at
`bcc6b1e10534d0f2ed2c5e7510ee1761c7be4ca7a52fc743f4b266afedcf15f0` and
`f1d880ae806b501cd946f0c7564a552e288c7f3b2833a1801132675f5ec8841c`. A stdlib
launcher verifies its core/subproject/lock bootstrap hashes, checks `uv 0.12.5`, clears
`PYTHON*`, `UV_*` and `VIRTUAL_ENV`, and executes an `--isolated --frozen` environment
under exact CPython 3.10.20 with `python -I`. Launcher/core/subproject/sub-lock hashes
are `92a0a755881f549b272bfbd09d11b2590e06e9ecb06409421f6bd18e261b1f1b`,
`797cedbc1e24fac6e4cbf042f48981786b662ce8b0fa913b988bce12818023c7`,
`236606b46ed945fbbce46868a1a8ab5aac9a1131f39352f324e95a6001b59625`, and
`aebfc5d498735f694572ee8b53c328da5fa66a84da05d202605a2500e8b78f93`.

Request JSON/TSQ, content-addressed raw TSR, target link and receipt are create-only.
Every public reload reconstructs the target envelope from the fully replayed live ledger
and verified-live intent/completion history before redoing message, nonce, policy,
pinned leaf, leaf/root signature relation, TSA DirectoryName, CMS signature/chain,
accuracy and time-boundary checks. It does not trust receipt booleans. Canonical-byte,
symlink, inode replacement, nonregular lock, relocated object, fabricated envelope,
coordinated trust swap, dependency/module injection and crash-recovery tests fail closed.
HTTP 408/425/429/5xx are retryable waiting; deterministic protocol/integrity drift is
exit 2; lock contention is exit 3. The full runtime namespace and shared lock path are
core constants. A process-level total transport deadline prevents slow-drip HTTP reads
from holding the global lock indefinitely; an unavailable signal-mask inspection or an
inherited blocked `SIGALRM` fails closed before any request is sent.

The versioned public dummy production probe has TSQ/TSR SHA-256
`bdc94a42cd34ba1a947c521b19553edea9a7699c621c8ff66ba4458382acbfa3` and
`4535d7ddc291159db625a9b68b403d5db14a8544c3be102604377a4a7d317afc`; it is
interoperability evidence, not Ootang science. Focused verification is currently core
`22/22` and launcher+pipeline `39/39`. The final full repository is `724/724` in
345.293 seconds (0 failure / 0 error). Ruff, format, compileall, strict JSON `3/3`,
diff-check, root/isolated lock checks and the formal-v5 `23/23` preflight pass; G0
remains PASS, G1--G4 BLOCKED and G5a unauthorized. All 97 protected paths remain
unchanged at aggregate
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`.
Independent read-only review is P0/P1/P2 `0/0/1`; the sole P2 is the documented
ESSCertIDv2 limitation.

`trusted_anchor_receipt_verified`, `e2_live_evidence_eligible`,
`real_activation_ready`, and `formal_warning_output` remain false even for a valid
receipt. A CMS signature proves the pinned TSA's signed time claim, not objective UTC
correctness. The implementation does not separately parse the RFC 5816 ESSCertIDv2
signed attribute; exact embedded-leaf pinning and CMS verification narrow that residual
risk, which remains documented rather than overstated.

The next machine-only gate is an immutable epoch registry with bundle prebuild and safe
automatic rotation. Then add cycle v4 trusted-time qualification and scheduler entry
authorization; later optimize O(N^2) full-chain scans. Never replace these gates with
manual dates, freezes, approvals, signatures or fabricated backfill.

## 2026-08-26 designated replay gate and cycle v3 continuation

Three explicit-only stages were added without changing the default pipeline:

```text
ootang-issue-replay
ootang-verified-live
ootang-prequential-cycle-v3
```

`main.py` now exposes 26 selectable stages; its no-argument chain remains exactly
`features -> convlstm -> ootang-operational-v4`. Replay profile SHA-256 is
`c42a56a547691654f9281f44b94e5d79ef66a8ff0064b255a59d67c6939e6fd5`, verified-live
is `081af2dfd4b95f28b750d915a2ff74d508381e62f5d539aaaaa62b8add44992b`, and cycle-v3
is `6852876db121027e82aedfb2b65c9cb1d9b40106b19c7068ba8764b317e1db24`. The old
live/cycle v1/v2 contracts and default stages are unchanged.

The independent verifier recursively reconstructs both current and activation source
lineage through the shared source authority, then independently implements all five
normalization arrays, IDW, the seven-channel input, ConvLSTM cell/head forward,
station readout and inverse normalization. Eight persistence values must be exact;
all 40 five-seed P50 values use `rtol=0, atol=1e-6 mm`. The public receipt loader
does the same true five-checkpoint forward again, so a self-consistent forged receipt
or comparison digest is not accepted.

The verified-live wrapper owns the original live-v1 runner lock and commits only
`replay receipt -> pre-seal intent -> live issue transaction -> completion`. Crash
recovery always restores completion before outcome/revision reads. Pre-existing direct
v1 seals without an intent are never retroactively authorized. Machine-time fences
cover replay publication, pre-intent, pre-append and post-append completion; rollback
cannot leave a create-only invalid completion. Runtime final components use stable
regular-file snapshots with `O_NOFOLLOW` and pathname-inode checks, while strict
machine cleanup handles only exact crash-temp names and rejects lookalike pollution.

Cycle v3 runs exactly 13 stages: replay before source; the four cycle-v2 shadow
barriers; source, bundle, outcome and issue producers; three verified-live polls; and
replay again after issue production. Raw ledgers/records are still fully verified, but
the progress token projects timestamp/path/sequence/entry-hash-independent science and
filters anchor-only bookkeeping. Legitimate time, storage-path and failed-anchor churn
therefore converges; changed issue/seal science still changes the token. Exact status
keysets and claims, final symlinks, pathname swaps, oscillation, continuation, crash
windows and clock causality fail closed.

Final replay/verified-live/cycle-v3 tests are `36/27/16`, combined `79/79`; with
pipeline they are `109/109`. The full repository is `715/715` in 727.081 seconds.
Ruff, compileall, strict JSON validation and diff-check pass. Formal-v5 preflight is
`23/23` with G0 PASS, G1--G4 BLOCKED and G5a unauthorized; the 97-path aggregate remains
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`. A real empty
runtime executed all 13 stages once and returned `converged_waiting`, with formal
warning, E2 evidence, real activation and promotion false. Independent final audit is
P0/P1 `0/0`.

This increment does not retrain, tune, select a seed, change a calibration threshold,
or re-estimate any predictive metric. It improves provenance and causal integrity
only. Formal warning, E2 evidence, real activation, selection and promotion remain
false.

The next machine-only gate is a pinned-provider cryptographically verified time
receipt. After that: immutable epoch registry/prebuild/automatic rotation, scheduler
entry authorization that disables bypass through old live/cycle CLIs, and removal of
O(N^2) repeated full-chain scans. The local ledger remains a trusted-writer chain and
intermediate-directory replacement by a malicious local writer remains outside this
increment's trust boundary. Do not replace any remaining gate with manual dates,
freezing, approval, signing or fabricated backfill.
Additional audited P2s—narrow SIGKILL commit windows, future code/model epoch
incompatibility, checkpoint expansion memory pressure, and a short unlocked guard
progress staleness window while bypass CLIs exist—are recorded in section 8 of
`docs/ootang_checkpoint_input_replay_engineering.md`.

## 2026-08-26 E2 calibration shadow and cycle v2 continuation

Two explicit-only stages were added:

```text
ootang-prequential-calibration-shadow
ootang-prequential-cycle-v2
```

`main.py` now exposes 23 selectable stages; the no-argument chain remains exactly
`features -> convlstm -> ootang-operational-v4`. The shadow stage is a separate
runtime, configuration, runner and SQLite application id. It consumes only the fully
verified E2-A live projection and never edits live/deploy/cycle v1, the fixed
calibration core, E1 outputs, v4/v5 or Vajont.

The fixed candidates are `aci_v1_control`, `agaci_ewa_variant_v1` and
`spci_qrf_v1`, with eight independent station states each. For every target, one
transaction durably records opened + 24 candidate issues + sealed before any matching
actual can be read. A verified live settlement then produces opened + 24 reveals +
24 state updates + settled. Activation-outstanding issues are explicitly not future
support; missed settlements become ineligible backfills without fabricated issues or
state changes; revisions append 24 retrospective rescores with identical before/after
state. Live drift resets all three methods automatically.

The independent ledger uses SQLite STRICT, WAL/FULL, canonical finite JSON, exact
schema, transaction digests, a global SHA chain, an issue-only scientific chain,
update/delete triggers and a conflicting insert/replace guard. Public reads and writes
verify all history and mathematically replay every candidate state from genesis.
Exact retries keep the first event/time; partial retries, changed semantics, chain or
state drift, hostile schema, foreign databases and deep/non-finite payloads fail closed.
Shadow epoch changes close and cold-start automatically only when no issue is
outstanding.

Backlog actions are merged by live source sequence across issue seals, settlements,
backfills and revisions. This closed an independently reproduced cursor defect where a
later backfill could otherwise skip an earlier revision. One call handles at most 512
actions and returns `work_remaining/exit 0` for scheduler continuation. The deterministic
progress token excludes status/poll times, ledger recorded times, raw SQLite bytes and
anchor-only churn. Two runs with different shadow timestamps produced identical tokens
after genesis, issue, settlement and revision.

Cycle v2 binds the unchanged cycle-v1 profile and adds four shadow barriers around the
existing source/outcome/issue transitions, giving this exact 11-stage order:

```text
shadow_before_source -> source_ingest -> bundle_ensure
-> live_reconcile_before_outcome -> shadow_after_live_before_outcome
-> outcome_materialize -> live_reconcile_after_outcome -> shadow_after_outcome
-> issue_produce -> live_seal_issue -> shadow_after_issue
```

It reuses the v1 outer lock, keeps live/shadow runtime roots separate, fences a shadow
snapshot with base-token before/after reads, and treats `work_remaining` plus an
unchanged scientific token as an integrity contradiction rather than convergence.
Busy remains exit 3; integrity/config errors remain exit 2. No operator date, freeze,
approve, backdate, force or promotion control was introduced.

The predeclared evaluation contract requires common support, at least 180 joint future
target dates and 180 observations per station/method, a 30-day rolling window,
absolute coverage gap <=0.05, challenger-vs-ACI coverage-gap margin <=0.02, interval
score ratio <=1.0, availability >=0.95, and all stations passing. It computes only
engineering readiness. Selection, promotion, E2 live evidence, real activation and
formal warning are hard false even if a gate later passes.

Configuration SHA-256 values:

- calibration shadow: `28c02510f81e1832220913d4bfde69bc8abe9a2269aa8279aabc297f60113857`;
- cycle v2: `875daa95416e58e3c80a4a68f59874047daa1bbb5b395878f6a9265605279242`;
- unchanged base cycle v1: `2e4a0da22034a3063f612a723f007bf20b600c1dbdb7c62761368aa7a37810ef`.

Final verification is ledger/runner/cycle-v2 14/13/23 (50/50), pipeline 29/29,
combined prequential/deployment/shadow 269/269, and full repository 635/635 in
330.833 seconds. Ruff, compileall, JSON validation and diff-check passed. A real empty
runtime completed one exact 11-stage iteration as `converged_waiting`; shadow status was
`waiting_for_live_prerequisites`, with evidence and promotion false. Formal-v5
preflight remains 23/23 with G0 PASS, G1--G4 BLOCKED and G5a unauthorized. The eight E1
artifact hashes and 97-path aggregate remain unchanged. Final independent review found
P0/P1/P2 = 0/0/0.

At the end of that historical increment, the next machine-only gate was
runner-independent checkpoint/input replay; the designated-entrypoint version is now
implemented in the section above. Trusted cryptographic time, immutable automatic
epoch registry/rotation, scheduling entrypoint authorization and O(N^2) long-chain scan
removal remain. The existing live
v1 ledger is still a trusted-writer hash chain, not externally authenticated storage;
do not claim tamper-proof E2 evidence. Never substitute manual dates, freezing,
approval, signatures or fabricated backfill. The user-owned untracked
`data/vajont_fig5a_curves_2_3_4_5_58_mm_velocity.xlsx` and `review.md` were not read,
modified or staged.

## 2026-08-26 E1 calibration bakeoff continuation

An additional explicit-only stage is now implemented:

```text
ootang-prequential-calibration-bakeoff
```

`main.py` exposes 21 selectable stages; the no-argument chain remains exactly
`features -> convlstm -> ootang-operational-v4`. The new stage reads only the protected
E1 monitor profile and its station/site/metrics/manifest bundle. All three methods
reuse every E1 point forecast exactly and follow the same fold/drift reset schedule:

- `aci_v1_control`: exact E1 alpha, interval, and warm-up parity;
- `agaci_ewa_variant_v1`: seven fixed ACI gamma experts with separate lower/upper
  exponential weighting on past normalized pinball loss; explicitly not the BOA plus
  gradient-trick algorithm in the AgACI paper;
- `spci_qrf_v1`: signed residuals, lag 10 ordered most-recent-to-oldest, 60 minimum
  QRF pairs, fixed 180-row window and deterministic shallow ten-tree forest.

For each fold-date, the runner constructs and hashes all 24 method/station issues before
it reads any same-date outcome. A synthetic counterfactual changes one first-day actual
by 50 mm: all 24 first-day issues and the candidate batch hash remain exact, while the
three next-day state hashes and next batch hash change. Candidate state, settings, the
source E1 issue hash, config, implementation, dependencies, inputs, and outputs are all
content-bound. Staged `%.17g` CSV bytes are reloaded, metrics and pairwise tables are
recomputed, and the entire candidate timeline is deterministically replayed from source
by the same versioned runner/core before per-file atomic replacement. This is not an
independent implementation replay or a bundle-wide SIGKILL-safe transaction.

Overall fold 1/2/3 coverage is:

```text
ACI control        0.791192 / 0.695061 / 0.630746
AgACI-EWA variant  0.848140 / 0.745884 / 0.657121
SPCI-QRF           0.721186 / 0.622433 / 0.498736
```

The EWA variant narrows intervals and reduces interval score in all folds, and improves
absolute coverage gap in folds 2/3, but worsens fold-1 coverage gap through overcoverage.
The fixed SPCI configuration undercovers all folds. These are retrospective descriptive
signals only: output schemas prohibit winner/rank/selection/promotion, and all E2,
confirmatory, independent-label, real-activation and formal-warning claims remain false.

Materialized outputs under `figures/prequential_calibration_bakeoff_ootang_v1/` are:

- timeline: 20,664 rows, SHA-256
  `8857e77a96cba8ad2ae011a822759c0c08cc65e0c6fdab684b3e3fc0334df91e`;
- metrics: 108 rows, SHA-256
  `dfc314041a2982456428b51dd4cd08c7b232706dea77ff77c200e3e76e620168`;
- pairwise: 144 rows, SHA-256
  `4e13a366bfe37b58d9692bdbefd23f4fa686ee731657460b21f04964662eccf8`;
- manifest: SHA-256
  `229a26f5ec2c5a7082b14d18b8af7d21d44ba42f3b5b5d365b765f6fead4422f`.

Two complete final runs produced byte-identical hashes. The next research step is a new
versioned E2 calibration shadow protocol with predeclared coverage/score/availability/
stability and minimum-support gates. It must issue all shadow candidates before the
outcome seal and update them only after machine materialization. It must not manually
freeze dates, rewrite E1/live v1, or promote a method from this viewed replay. Full
details are in `docs/ootang_prequential_calibration_bakeoff.md`.
That next step is now implemented by the E2 shadow/cycle-v2 section above; this
paragraph is retained as the historical E1 decision boundary.

Final independent P0/P1 review closed two defects before those hashes were recorded:
the issue phase now materializes an issue-only column whitelist and constructs the
reveal lookup only after all 24 issues plus their batch hash; QRF weighted quantiles now
drop zero-weight extremes, renormalize finite positive support, and handle probabilities
zero and one on that conditional support. The latest review reports no open P0/P1.

Final verification is calibration core 13/13, bakeoff runner 4/4, pipeline 28/28,
and full repository 584/584 in 575.016 seconds with zero failures/errors. Ruff,
compileall, and diff-check passed. The v5 preflight remains 23/23 with G0 PASS,
G1--G4 BLOCKED and G5a unauthorized. E1 hashes and the 97-path aggregate
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`
remain unchanged.

## 2026-08-26 E2-B2 machine outcome and fixed-point continuation

Two explicit-only stages have been added without changing the three-stage default chain:

```text
ootang-outcome-materializer
ootang-prequential-cycle
```

`main.py` now exposes 20 selectable stages. With no arguments it still runs exactly
`features -> convlstm -> ootang-operational-v4`. The cycle is the machine scheduling
entry point and runs this fixed internal order:

```text
source ingest
  -> bundle ensure
  -> live reconcile
  -> outcome materialize
  -> live reconcile
  -> issue produce
  -> live seal
  -> repeat until the scientific progress token is stable
```

The source commit protocol is also hardened. `source_current.json` is now
`ootang_source_current_pointer_v2`; per-day revisions form immutable predecessor/
sequence receipt chains, and every whole-source snapshot is registered in a separate
content-addressed global `ootang_source_snapshot_receipt_v1` chain. The current
pointer must match the verified unique tip. A missing or stale pointer may be rebuilt
only from that tip; rollback (including r1 -> r2 -> r1), branch, orphan, duplicate
sequence, receipt/object tampering, or a non-tip binding fails closed. Legacy pointer
v1 bytes do not migrate implicitly; a future automatic epoch protocol must establish
the replacement epoch explicitly.

The outcome materializer consumes only finalized daily records recursively verified
through immutable current-source provenance. Candidate order is fixed: a newer source
revision already relevant to the ledger, then a sealed outstanding issue, then the
next contiguous backfill. It never derives truth from predictions, ledger scores,
wall-clock date selection, or a human-maintained table. Each target's revisions form
an immutable sequence/predecessor receipt chain with one tip, an active-receipt
pointer, an exact content object, and an inbox publication. Receipt -> pointer ->
inbox crash windows recover from the verified committed tip; branches, rollback,
same-revision semantic changes, and poisoned active bytes block. A revision at or
before the activation watermark returns the durable machine wait
`waiting_epoch_rotation_required`; it does not mutate the old epoch or request a
human freeze. One invocation-scoped monotonic clock covers initial reads, multi-chain
receipt recovery, publication, and blocked status. The adversarial 10 -> 11 -> 9 -> 10
sequence now rolls back every public pointer/inbox and records the block at the last
successful observation, 11.

The cycle holds a non-blocking outer `cycle_lock`. It does not pre-hold child locks
while calling stages; a scientific snapshot briefly acquires deploy -> runner locks
to avoid a torn cross-producer view. Its progress token covers the source pointer,
model manifest, verified scientific ledger projection, issue/outcome receipt tips,
active bindings, and inbox bytes. Poll timestamps, mutable status bytes, raw ledger
head/event count, and repeated failed-anchor bookkeeping are excluded, so an anchor
endpoint that stays unavailable does not prevent convergence. Empty input converges
to `converged_waiting` in one iteration. Recoverable zero-byte or valid-schema/no-
genesis ledger crash states are represented as pre-genesis and allowed to reach E2-A
initialization; structurally damaged ledgers still block.

A call processes at most 64 distinct-progress iterations. Legitimate monotonic
backlog then returns `work_remaining/exit 0` for the scheduler to continue, rather
than misreporting an integrity failure. Continuation token history is persisted in
cycle status, so a later invocation that returns to any prior scientific token
(including a 65-state ring) is detected as oscillation and blocked. Runtime paths
reject both root escapes and root-internal symlink aliases; receipt/inbox trees reject
symbolic entries; every substage result, status path, schema, provenance binding, and
status is revalidated against a strict allowlist. Production dependency/token
injection is rejected unless the test-only
`OOTANG_E2B_ALLOW_TEST_CYCLE_OVERRIDE=1` switch is explicitly set.

There is no operator-supplied target-date, freeze, approve, force, backdate, or
manual-signature CLI/control field in this path. Missing source/model/outcome remains
a successful machine waiting fixed point; lock contention is `busy/exit 3`, and integrity conflicts are
`blocked_integrity/exit 2`. All evidence and real-activation flags remain false.

Final evidence is outcome-materializer 31/31, cycle 23/23, cycle/main 50/50,
the combined E2-B2 set 197/197, and the full repository 566/566. Ruff, compileall,
diff-check, and an actual empty-runtime seven-stage one-iteration
`converged_waiting` exercise passed. The v5 preflight remains 23/23 with G0 PASS,
G1--G4 BLOCKED and G5a unauthorized. E1's four hashes and the 97-path aggregate
remain unchanged. Independent final adversarial review found no remaining P0/P1.

Current bound configuration SHA-256 values are:

- cycle: `2e4a0da22034a3063f612a723f007bf20b600c1dbdb7c62761368aa7a37810ef`;
- deploy: `60f17602998e976f06d590b7611dfb4480505c21d41a9b05420bd93cf831f940`;
- live: `bf7c60a19e26e9a54fc4e1980b3556d6e6d1e3fec4b3a3a7f3de0dbb9b83cf00`.

At that historical point the remaining gates included runner-independent replay; the
designated-entrypoint version is now implemented at the top of this handoff. Trusted
cryptographic time verification, an immutable automatic epoch registry/rotation,
scheduler authorization, and removal of repeated receipt/ledger full scans that can
grow as O(N^2) remain. None may
be replaced by manual date selection, freezing, approval, signing, or fabricated
backfill. The detailed contract is in
`docs/ootang_prequential_cycle_engineering.md`.

## 2026-08-26 E2-B1 machine deployment continuation

Three explicit-only stages now precede E2-A:

```text
ootang-live-source
  -> ootang-production-bundle
  -> ootang-issue-producer
  -> ootang-prequential-live
```

They implement the first fully machine-operated source-to-issue path without daily
human date selection, manual freezing, best-seed selection, or manual issuance:

- strict daily finalized JSON ingest after 2020-06-30, trusted derivation of
  `RWL_rate` and 7/15/30-day rainfall sums, content-addressed objects, advancing
  current source, and one-time immutable activation source;
- seeds 0--4 all-as-of fixed-120-epoch CPU training from activation source only,
  tensor/primitive checkpoints loaded with `weights_only=True`, recursive source/
  preprocessing/state validation, and implementation/dependency-lock provenance;
- internal five-checkpoint P50 replay from the last seven as-of rows, explicit
  model/live station mapping, ledger-aware next-target/no-skip gating,
  content-addressed input semantics, atomic issue publication, and stable-semantics
  idempotency.

The no-skip gate holds E2-A's runner lock and uses the public read-only verified
projection API to validate/replay the actual SQLite ledger before comparing every
scientific status field. First issue bytes are registered in a content-addressed
exact object plus an atomic no-replace producer receipt before inbox publication;
later mutation of a time field, receipt, object, or inbox is blocked. Unexpected
source I/O failures replace stale ready state with best-effort
`blocked_integrity`, and bundle lock contention is non-blocking `busy/exit 3`.
The input scientific digest also binds the deploy profile, issue-producer bytes,
dependency locks, and Python/NumPy/pandas/PyTorch versions.

The final adversarial audit additionally closed runtime-root/symlink traversal,
forged activation-pointer injection, checkpoint hash/load TOCTOU, stale entry-clock
publication, same-watermark revision/persistence divergence, and the receipt-to-inbox
crash window. Checkpoints are hashed and safely loaded from the same bounded byte
snapshot; bundle/issue commit barriers resample UTC before and after persistence;
the issue producer requires current displacement to equal the verified ledger (or
pre-genesis activation) state before inference. A crossed-boundary issue is removed
from the inbox while the runner lock is still held, and a committed receipt restores
its registered first bytes before a changed retry is reported as a conflict. Bundle
training does not hold the runner lock, but the final write/post-check/revoke window
does, so a crossed-boundary manifest is never visible to E2-A.

The profile distinguishes an implemented capability from an operation exercised in
the current poll. Waiting statuses therefore report source semantics, safe loading,
and checkpoint replay as false until they actually happen. The profile and all three
loaders reject attempts to claim runner-independent replay, trusted anchor receipt,
automatic epoch rotation, E2 evidence, or real activation.

The real four-stage no-feed poll returned, in order,
`waiting_for_daily_finalized_feed`,
`waiting_for_semantically_validated_source`, `waiting_for_source_or_model`, and
`waiting_for_production_bundle_or_source_snapshot`. It created no current/activation
source, model manifest, issue, or ledger. No real 120-epoch model was trained without
an activation source. Focused source/bundle/issue/E2-A/main tests pass 136/136 and
the full repository passes 505/505; Ruff, compileall, diff-check, and the 23/23
formal-v5 fail-closed tests pass. The real waiting statuses bind deploy profile SHA
`60f17602998e976f06d590b7611dfb4480505c21d41a9b05420bd93cf831f940`.
E1 output hashes and the 97-path protected aggregate remain unchanged; exact values
are recorded in `docs/progress.md` and
`docs/ootang_prequential_deploy_engineering.md`.

The subsequent E2-B2 increment now supplies the machine-only outcome materializer,
receipt/pointer/inbox crash recovery, source pointer v2, and fixed-point cycle described
above. At that point runner-independent replay was still pending; the designated
version is now implemented at the top. Pinned cryptographic time, immutable automatic
epoch registry/rotation, scheduler authorization, and O(N^2) long-chain scan
optimization remain separate gates. Do not substitute manual freezes or signatures.

## 2026-08-26 E2-A append-only live engineering continuation

The explicit-only `ootang-prequential-live` stage now performs one fully automatic
machine poll and exits. It uses a separate cold-start namespace, strict issue and
outcome inboxes, a SQLite WAL append-only hash chain, atomic multi-event issue and
outcome transactions, complete mathematical replay, revision-only retrospective
rescoring, lock-based single-writer recovery, and automatic waiting states. No
daily human date selection, threshold selection, or manual freeze is part of the
runner.

The runner is intentionally fail-closed and engineering-only. With no activation
artifacts it writes
`waiting_for_production_bundle_or_source_snapshot` and creates no ledger. A
historical complete outcome is appended as `backfill_not_blind` without inventing
an issue or changing online model state. A target on or before the current local
day cannot be retroactively issued. Outcome files are not loaded until all eight
station issues are durably sealed and an automatic anchor attempt has been
recorded.

E2-A itself does **not** independently generate the five predictions from checkpoint
bytes or validate the semantic contents of the issue input manifest. E2-B1 now does
both in the producer, but the runner-side independent replay gate remains false.
E2-A also does not cryptographically verify a pinned time-stamp provider or rotate
immutable epochs automatically. Consequently an arbitrary HTTPS JSON receipt can be,
at most, an
`engineering_blind_time_order_candidate`; v1 hard-codes
`trusted_anchor_receipt_verified=false`, `e2_live_evidence_eligible=false`, and
`real_activation_ready=false`.

Primary E2-A files:

- `config/ootang_prequential_live.v1.json`;
- `code/monitoring/prequential_core.py`;
- `code/monitoring/ootang_live_ledger.py`;
- `code/monitoring/ootang_prequential_live.py`;
- `tests/test_prequential_core.py`;
- `tests/test_ootang_live_ledger.py`;
- `tests/test_ootang_prequential_live.py`;
- `docs/ootang_prequential_live_engineering.md`.

The next implementation target is not manual live-data freezing. E2-B1 supplied the
content-addressed five-seed deployment/issue producer and E2-B2 has now supplied its
machine outcome/cycle counterpart. The designated replay target is now implemented as
described at the top; remaining targets are an immutable per-epoch registry with safe
automatic rotation, trusted cryptographic time verification, scheduler authorization,
and O(N^2) scan removal. Until those exist, the correct runtime
state is an automatic wait or fail-closed block, not fabricated live evidence.

Final E2-A verification: 64 targeted tests, 23 frozen gate tests, and 414/414
full-suite tests passed. Ruff and compileall passed; the real missing-prerequisite
poll returned the automatic waiting state without creating a ledger. All 18
register-referenced frozen files match, the four E1 output hashes are unchanged,
and the 97-path protected aggregate remains
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`.
The final suite exposed a pre-existing Markdown hard-break normalization in
`docs/v5_g1_g4_preflight.md` and `docs/v5_v0_numerical_audit.md`; their exact
registered bytes were restored without changing the frozen register. The five
resulting trailing-space lines are intentional hash-locked snapshot bytes.

## 2026-08-26 machine-only continuation note

The user explicitly rejected per-run manual freezing and daily human operation.
The current continuation therefore adds a separate machine research track; it
does not alter the formal-v5 gate snapshot or pretend that internal residuals
are independent disaster truth.

Implemented explicit-only stage:

```bash
uv run python main.py \
  --stage ootang-prequential-monitor \
  --manifest /tmp/ootang-prequential-monitor-v1-run.json
```

The source is the fixed 5-seed, 3-fold strict-temporal OOF bundle at
`figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/seed_stability_0_4/seed_stability_predictions.csv`
(34,440 rows). It is not the in-fit `figures/convlstm/forecast_predictions.csv`.
For each of 861 fold-dates the runner issues all eight stations from prior
state, computes and chains the issue-only batch hash in retrospective replay,
then reveals all eight outcomes and updates future state. Fold changes reset
online state; all three folds remain in one run-wide retrospective audit chain.

The persistence contract does not trust the prediction CSV by itself. The
runner retrieves `data/features.csv` from the source manifest commit
`1e06629119e08b33ded2540a435e726c2d2da97a`, verifies blob SHA-256
`366188ba1f55fd56b6606f5333e34566fca26c4771dd9745eebfabfcbd4286d1`
and size 1,424,208 bytes, then exactly checks all 6,888 target actuals and all
6,888 previous-natural-day persistence values.

The algorithm is fully machine-operated within E1:

- persistence + seed0--4 P50 online experts with scale-free exponential weights;
- symmetric absolute-residual conformal interval with ACI;
- one-sided underprediction residual p-value and continuous anomaly score;
- ADWIN-inspired bounded Hoeffding drift detector with automatic reset/rewarm;
- automatic abstain when history or spatial coverage is inadequate;
- O1/O2/O3 block max and cross-block min continuous aggregation;
- atomic bundle promotion, source/config/code/project/dependency-lock hashes,
  runtime version records, a recomputed run-wide issue chain, and cross-date
  station state-hash continuity checks;
- exact full-profile config validation and staged `%.17g` CSV round-trip reload,
  followed by a full replay of station state math, ACI, anomaly, drift, site
  aggregation, and metrics before promotion.

Materialized outputs are under `figures/prequential_anomaly_ootang_v1/`:

- `station_timeline.csv`: 6,888 rows, SHA-256
  `805951dcf77aa19e7d5021fa53a51bfa2067663b7fda0e5dd0fcc483ad2a7bfe`;
- `site_timeline.csv`: 861 rows, SHA-256
  `d35822d7dc198f859308b1d46071d8df128e9bff4203458ccadfd1aa86e3a6fd`;
- `prequential_metrics.csv`: 27 rows, SHA-256
  `9d790ecb4550ee849001cf6e21873b3047598212508c1c86c6fc6c188e4eab96`;
- `manifest.json`: SHA-256
  `2e680d06a6e04e02562bb31ec53b885acecafc068015417525dee115de97f253`.

The same explicit stage was run twice after final validator hardening; all four
hashes were identical. Final verification is 19 monitor tests, 91 combined
targeted tests, and 373 full tests, plus Ruff, compileall, diff-check, and both
dry-runs. The 97-path protected aggregate remains
`6ec304b153b2c31e54d631abc25b450033393b73b052b12464b24418ac4cd6d3`.

Scientific result: MAE skill versus persistence is positive in all three folds,
but RMSE skill is not stable. Target-0.8 interval coverage falls from 0.791 to
0.695 and 0.631, while abstention is 0.426/0.392/0.422. Do not promote v1 to a
production warning product. The maximum anomaly score is the finite empirical
p-value floor, not a disaster label.

Durable machine-track sources:

- `docs/ootang_autonomous_research_protocol.md` -- E0--E3 evidence ladder,
  no-daily-manual-operation contract, E1/E2 distinction, and literature basis;
- `docs/ootang_prequential_monitor_results.md` -- exact metrics, counts, hashes,
  verification, interpretation, and reproduce commands;
- `config/ootang_prequential_monitor.v1.json` -- versioned E1 algorithm/source
  contract;
- `code/monitoring/ootang_prequential_monitor.py` -- runner and fail-closed
  validators;
- `tests/test_ootang_prequential_monitor.py` -- causality, tamper, determinism,
  provenance, and boundary tests;
- `figures/prequential_anomaly_ootang_v1/manifest.json` -- machine-readable run
  record.

The next engineering step is E2, not another retrospective threshold search:
implement automatic live ingest, real issue/outcome separation, append-only
event ledger, idempotent recovery, outcome revisions, and an automatic external
time anchor. With no new data the runner should stay in
`waiting_for_new_data`; no human should select dates. SPCI/AgACI or other
calibration challengers may follow under a new predeclared version, because the
current three-fold results have already been viewed. E3 remains blocked until
an independent, machine-readable outcome source with availability timestamps
exists; model residuals cannot generate their own disaster truth.

The untracked Vajont workbook and root `review.md` were not read or modified.

## 2026-08-20 current continuation note

The original candidate-display work below was subsequently committed and
pushed as `07ad977 feat: add automatic v0 candidate display`. A follow-up
numerical audit and a separate G1--G4 evidence/preflight pass have now been
completed in the working tree but are **not yet committed or pushed**.

The follow-up resolved ATU3/MJ9 `negative_segment_sse` as catastrophic
float64 cancellation in full-history prefix raw moments. The automatic-V0
wrapper now uses uniform per-segment-start local sufficient statistics with the
same minimum segment length, dynamic program, BIC, and scientific selection
rule. The shared historical v4 Bai--Perron source remains byte-for-byte
unchanged.

Post-audit outcomes:

- MJ1/MJ3 remain the only candidates, with exactly the same materialized V0;
- ATU1--ATU5 are unavailable because the first break is not accelerating;
- MJ9 is unavailable because the initial segment slope is nonpositive;
- all eight stations now retain six auditable selected segments (48 rows);
- the v5 display remains 4,112 rows, 1,028 available and 3,084 not applicable;
- the earlier numerical-audit run passed 48 targeted/330 full tests; the final
  combined tree now passes 71 targeted/353 full tests;
- 97 protected v4/ConvLSTM/NGBoost/model files are unchanged.

The subsequent G1--G4 inventory did not invent missing scientific evidence or
promote exploratory outputs:

- G0 is `PASS`; G1, G2, G3, and G4 are `BLOCKED`;
- there is no independent field/event label file or label manifest;
- the current Ootang range has already been exposed through fit, calibration,
  historical test, rolling validation, V0 selection, and review, so it contains
  no current unseen confirmation block;
- model fit starts on 2016-08-06, while the automatic-V0 runner intentionally
  uses the separate fit-end-bounded kinematics window 2016-07-01 through
  2019-02-02 (947 rows per station); do not collapse those two boundaries;
- automatic-V0 availability is 2/8 stations and 1,028/4,112 station-days
  (25%); the two stations are both in O1 and formal site output remains 0/514;
- event-recall, FAR, coverage, fusion-increment, and minimum-support numeric
  thresholds remain null and unapproved;
- no new NGBoost training or inference, fusion, warning color, or formal output
  was run or created.

`config/ootang_v5_gate_register.v1.json` is the machine-authoritative frozen
blocked snapshot for G0--G4. It must not be edited in place to turn G1--G4 into
PASS. Unblocking requires a new schema/version and corresponding manifest,
cross-hashed evidence validator, review, and an explicit switch of the
production default source. The production guard and CLI deliberately do not
accept an alternate register path or root.

Passing G0--G4 would be necessary but insufficient for G5a. A future G5a
candidate runner still needs a separate versioned run contract and guard that
freeze features, search space, seeds, calibration and failure rules,
input/implementation hashes, and a one-shot output namespace. That contract
and guard do not exist, so G5a remains unevaluated and unauthorized.

Durable continuation sources:

- `docs/v5_v0_numerical_audit.md` -- closed numerical investigation, fix,
  hashes, commands, and outcome;
- `docs/v5_validation_protocol.md` -- formal-v5 decision gates; G0 passes, G1
  through G4 are blocked, while G5a is separately unevaluated/unauthorized;
- `docs/v5_g1_g4_preflight.md` -- label/time/coverage/metric inventory and the
  exact unresolved decisions;
- `config/ootang_v5_gate_register.v1.json` -- immutable G0--G4 blocked
  snapshot with evidence hashes;
- `code/warning/ootang_v5_gate_preflight.py` -- fail-closed validator,
  read-only report CLI, and fixed-default G0--G4 production guard;
- `docs/progress.md` -- current engineering summary.

Do not start formal NGBoost/fusion work from this note. The next work requires
new independent labels, a genuinely unseen confirmation time block, and human
approval of the V0-unavailable deployment policy, coverage denominator,
numeric metric thresholds, confidence-interval method, and minimum support.
The Vajont workbook and `review.md` remain outside scope and were not
inspected.

## 1. Read this first

This work deliberately stops at a **candidate-display boundary**. It does **not** create a formal v5 warning system.

Non-negotiable scientific and implementation boundaries:

1. Automatic V0 selection is Ootang-only and fit-only. It uses raw cumulative displacement, actual elapsed time, wrapper-local per-segment-start OLS/DP, the frozen shared `_bic_for_segment_count` definition, and point velocities from the accepted first segment. The shared v4 file is unchanged; its DP/cost-matrix path is not called, while that BIC helper and constants are deliberately reused.
2. Only MJ1 and MJ3 pass the automatic V0 gate:
   - MJ1: `0.25034204499655494 mm/day`
   - MJ3: `0.24813126112408163 mm/day`
3. ATU1, ATU2, ATU3, ATU4, ATU5, and MJ9 remain explicitly `unavailable`. There is no manual date range, KMeans fallback, hidden V0 imputation, or borrowed v4 comparator.
4. Unavailable automatic-V0 rows now leave `candidate_v_mm_per_day`, selected-velocity mean/sigma, and `candidate_v0_mm_per_day` null. Segment slopes remain only in the segmentation audit table.
5. The v5 bundle is display-only:
   - `candidate_display_only=true`
   - `candidate_warning_color_output=false`
   - `ngboost_inference_output=false`
   - `v5_fusion_output=false`
   - `formal_warning_output=false`
   - `vajont_used=false`
6. The candidate timeline has no unqualified candidate color column. Raw interval state is retained as `interval_level`; existing v4 colors are historical references only and use the `v4_reference_*` prefix.
7. The historical v4 implementation, thresholds, fusion, outputs, ConvLSTM artifacts, and all NGBoost experiment artifacts remain unchanged.
8. Do not run, adapt, inspect, or stage the untracked Vajont workbook without fresh explicit user authorization.
9. Do not edit the v1 gate register to simulate progress. A valid future PASS
   state must arrive as a reviewed new schema/version with its own cross-hashed
   manifest and validator; G5a still requires a second, model-specific run
   contract and guard.

The approved plan is at:

`/Users/wcqqq1214/.claude/plans/cozy-sleeping-swan.md`

## 2. What is implemented

### 2.1 Automatic fit-only V0 diagnostic

Key files:

- `config/ootang_auto_v0_direct_bai_perron.v1.json`
- `code/warning/auto_v0_direct_bai_perron.py`
- `tests/test_auto_v0_direct_bai_perron.py`
- `figures/auto_v0_direct_bai_perron_ootang_v1/`

Important entry points:

- `code/warning/auto_v0_direct_bai_perron.py` — per-segment-start OLS cost matrix and wrapper-local DP, using the frozen shared BIC definition;
- `select_direct_candidate` — station candidate selection and unavailable gate;
- `write_auto_v0_candidates` — atomic artifact writer.

Selection rule:

- BIC chooses the segment count.
- Accept the first segment when its slope is positive and the immediately following segment is faster.
- If BIC selects one positive full-fit segment, emit `stable_full_fit_baseline`.
- Otherwise emit `unavailable` with an auditable reason.
- Only accepted candidates receive V, selected-segment point-velocity sigma, and V0.

Materialized files:

- `candidates.csv`
- `segments.csv`
- `candidate_diagnostics.png`
- `candidate_diagnostics.svg`
- `manifest.json`

Current unavailable reasons after the closed numerical audit:

- ATU1--ATU5: `first_break_is_not_accelerating`
- MJ9: `nonpositive_initial_segment_slope`

### 2.2 V5 candidate-display bundle

Key files:

- `config/ootang_v5_candidate_display.v1.json`
- `code/warning/ootang_v5_candidate_display.py`
- `tests/test_ootang_v5_candidate_display.py`
- `figures/v5_candidate_display_ootang_v1/`

Important entry points:

- `build_candidate_display_timeline` — builds the full result grid and availability-gated fields;
- `write_candidate_display` — validates full automatic-V0 method/code provenance and writes the bundle atomically;
- `main.py` — explicit-only stage registration immediately after automatic V0.

Materialized outputs:

- `candidate_timeline.csv` — 4,112 rows = 514 dates × 8 stations
- `candidate_summary.csv` — 8 rows
- `candidate_display.png`
- `candidate_display.svg`
- `manifest.json`

Current counts:

- `candidate_available`: 1,028 rows (MJ1/MJ3)
- `not_applicable_v0_unavailable`: 3,084 rows (six stations)
- 514 rows per station and 8 stations per date
- no candidate warning color, no NGBoost probability, no station/site v5 grade

For MJ1/MJ3 the display includes:

- observed displacement
- raw point velocity and automatic V0
- raw ΔV and fit-only MAD near-zero tolerance
- velocity/V0 ratio in CSV
- continuous tangent angle
- raw interval level

For the six unavailable stations:

- raw displacement/velocity/ΔV/interval information remains visible in the table
- V0-dependent velocity ratio and tangent angle are null
- branch status is `not_applicable_v0_unavailable`

### 2.3 Pipeline integration

The default no-argument chain is unchanged:

```text
features -> convlstm -> ootang-operational-v4
```

The new stages are explicit-only:

```bash
uv run python main.py \
  --stage ootang-auto-v0-direct-bai-perron \
  --stage ootang-v5-candidate-display
```

Do not run the default pipeline merely to verify this work: it retrains ConvLSTM and can rewrite protected artifacts. Use the explicit command above or `--dry-run`.

### 2.4 G0--G4 frozen preflight snapshot

Key files:

- `config/ootang_v5_gate_register.v1.json`
- `code/warning/ootang_v5_gate_preflight.py`
- `tests/test_ootang_v5_gate_preflight.py`
- `docs/v5_g1_g4_preflight.md`

The register freezes the evidence-backed current state rather than offering an
editable workflow switch. Its evidence records are repository-relative and
SHA-256 locked; malformed JSON, duplicate or unknown keys, stale hashes,
out-of-scope paths, non-finite values, and attempts to mark an unsupported gate
PASS fail closed.

Read-only reporting:

```bash
uv run python code/warning/ootang_v5_gate_preflight.py --report
```

The default mode and `--require-g0-g4` validate the sole production-default
snapshot and exit blocked while G1--G4 remain blocked. The production
`require_g0_g4_preflight()` entry point likewise accepts no alternate path or
root. It must be called before any future runner reads label/training payloads
or creates outputs. It does not authorize G5a; the report keeps
`g5a_authorization_evaluated=false` and `g5a_authorized=false`.

Final register fingerprints:

- file SHA-256: `84b1e53c9eeb908b09bca6e3720c970b8ea716f8a959f295c919bac82077a7ef`;
- canonical content SHA-256: `d8bf7badcdec935ccb1f86439534a18a397a924b42eae6fda2fa38071b5c9821`.

### 2.5 Mentor-facing report and indexes

Updated:

- `paper/process_report.tex`
- `README.md`
- `docs/design.md`
- `docs/progress.md`

Report boundary section:

- `paper/process_report.tex` — historical v4 versus v5 candidate display, automatic V0 method/results, 2/8 availability, and the explicit no-inference/no-fusion/nonformal boundary

The candidate figure was changed to a portrait 5-row × 2-station layout so it remains legible on an A4 report page. The report compiles to 17 pages. PDFs/build intermediates are ignored; rebuild from source.

## 3. Final verification completed

The recorded results below cover the completed numerical-audit,
candidate-display, and final G0--G4 preflight tree. The earlier audit-only
counts remain in `docs/v5_v0_numerical_audit.md`; the current combined counts
below include the new preflight tests.

### Explicit stages

Final explicit run:

```bash
uv run python main.py \
  --stage ootang-auto-v0-direct-bai-perron \
  --stage ootang-v5-candidate-display \
  --manifest /tmp/ootang-v5-numerical-fix-run.json
```

Result: both stages completed with input/output contracts passed in 4.7 seconds. The `/tmp` manifest is session-local and should not be treated as a repository artifact.

### Tests and static checks

Final results:

- G0--G4 preflight module: **23 passed** in 0.199 seconds
- targeted automatic-V0 + v5 + preflight + pipeline tests: **71 passed** in 8.298 seconds
- full suite: **353 passed** in 274.412 seconds
- `uv run ruff check code tests main.py`: passed
- `uv run python -m compileall -q code main.py tests`: passed
- `git diff --check`: passed
- default dry-run: exactly `features -> convlstm -> ootang-operational-v4`
- explicit dry-run: exactly `ootang-auto-v0-direct-bai-perron -> ootang-v5-candidate-display`

The full unittest output includes expected argparse usage text and mocked pipeline-failure messages from negative tests; the suite still ends `OK`.

### Added regression coverage

- unavailable automatic-V0 records must not contain V/sigma/V0 estimates
- no unqualified color column may enter the v5 timeline
- only MJ1/MJ3 receive V0-dependent fields
- all 4,112 rows remain present
- no v4 fusion function is called
- writing a candidate bundle leaves v4 station/site/threshold/manifest hashes unchanged
- output CSV/PNG/SVG determinism
- source/output manifest hashes
- report source nonclaims
- real `latexmk`/XeLaTeX report build (skipped only when those tools are unavailable)

### Protected-state checks

- `code/warning/bai_perron_initial_slope.py` is restored exactly to `HEAD`; the new wrapper is local to `auto_v0_direct_bai_perron.py`.
- The versioned list `docs/v5_v0_protected_paths.txt` freezes 97 v4, ConvLSTM, NGBoost, and model paths; their before/after hashes match **97/97**, and the shared v4 Bai--Perron source is separately unchanged.
- The v5 manifest now records producer provenance (`git_commit`, dirty flag, and implementation-source SHA-256 records) in addition to input/output hashes.

### Report and figure checks

- report compiled successfully with XeLaTeX
- final log had no LaTeX errors, undefined references, or `Float too large`
- 17-page contact sheet was visually inspected; candidate figure is legible on page 16
- categorical figure colors `#2a78d6,#1baf7a,#eb6834,#4a3aa7` passed the dataviz palette validator; the green line had a contrast warning, mitigated by direct panel titles/axis labels and the CSV/table view

## 4. Historical git snapshot before the machine-prequential increments

The lists below are retained only as the original v5 handoff snapshot. They are not the
current working tree; the current increment and baseline are recorded at the top of
this document. No push was performed for the current continuation.

Tracked files modified:

- `README.md`
- `main.py`
- `code/warning/auto_v0_direct_bai_perron.py`
- `code/warning/ootang_v5_candidate_display.py`
- `config/ootang_auto_v0_direct_bai_perron.v1.json`
- `docs/design.md`
- `docs/progress.md`
- `figures/auto_v0_direct_bai_perron_ootang_v1/` candidate/segment/diagnostic/manifest artifacts
- `figures/v5_candidate_display_ootang_v1/manifest.json`
- `paper/process_report.tex`
- `tests/test_auto_v0_direct_bai_perron.py`
- `tests/test_main.py`
- `tests/test_ootang_v5_candidate_display.py`

New numerical-audit and G0--G4 preflight files:

- `CODEX_HANDOFF.md` (this document)
- `code/warning/ootang_v5_gate_preflight.py`
- `config/ootang_v5_gate_register.v1.json`
- `docs/v5_v0_numerical_audit.md`
- `docs/v5_v0_protected_paths.txt`
- `docs/v5_validation_protocol.md`
- `docs/v5_g1_g4_preflight.md`
- `tests/test_ootang_v5_gate_preflight.py`

New machine-prequential files and outputs:

- `code/monitoring/__init__.py`
- `code/monitoring/ootang_prequential_monitor.py`
- `config/ootang_prequential_monitor.v1.json`
- `tests/test_ootang_prequential_monitor.py`
- `docs/ootang_autonomous_research_protocol.md`
- `docs/ootang_prequential_monitor_results.md`
- `figures/prequential_anomaly_ootang_v1/`

E2-A/E2-B machine-live files:

- `config/ootang_prequential_live.v1.json`
- `config/ootang_prequential_deploy.v1.json`
- `config/ootang_prequential_cycle.v1.json`
- `code/monitoring/prequential_core.py`
- `code/monitoring/ootang_live_ledger.py`
- `code/monitoring/ootang_prequential_live.py`
- `code/monitoring/ootang_live_source.py`
- `code/convlstm/ootang_production_bundle.py`
- `code/monitoring/ootang_issue_producer.py`
- `code/monitoring/ootang_outcome_materializer.py`
- `code/monitoring/ootang_prequential_cycle.py`
- `tests/test_prequential_core.py`
- `tests/test_ootang_live_ledger.py`
- `tests/test_ootang_prequential_live.py`
- `tests/test_ootang_live_source.py`
- `tests/test_ootang_production_bundle.py`
- `tests/test_ootang_issue_producer.py`
- `tests/test_ootang_outcome_materializer.py`
- `tests/test_ootang_prequential_cycle.py`
- `docs/ootang_prequential_live_engineering.md`
- `docs/ootang_prequential_deploy_engineering.md`
- `docs/ootang_prequential_cycle_engineering.md`

Epoch registry/preparation/drain files:

- R1 is committed at `3d6ce8f` (`code/monitoring/ootang_epoch_registry.py`,
  `config/ootang_epoch_registry.v1.json`, its tests and engineering document);
- R2a is committed at `b53a238` (`code/monitoring/ootang_epoch_preparation.py`,
  `config/ootang_epoch_preparation.v1.json`, its tests and engineering document);
- `code/monitoring/ootang_epoch_drain.py`;
- `config/ootang_epoch_drain.v1.json`;
- `tests/test_ootang_epoch_drain.py`;
- `docs/ootang_epoch_drain_engineering.md`.
- R2b-2a is committed at `895844e`:
  `code/monitoring/ootang_epoch_drain_eligibility.py`,
  `config/ootang_epoch_drain_eligibility.v1.json`,
  `tests/test_ootang_epoch_drain_eligibility.py` and
  `docs/ootang_epoch_drain_eligibility_engineering.md`, plus the scoped
  `main.py`, `tests/test_main.py`, README and cross-document edits.
- Current R2b-2b-1 files are
  `code/monitoring/ootang_epoch_drain_v2.py`,
  `config/ootang_epoch_drain.v2.json`,
  `tests/test_ootang_epoch_drain_v2.py` and
  `docs/ootang_epoch_drain_v2_engineering.md`, plus the scoped pipeline and
  cross-document updates listed by `git diff`.

Pre-existing untracked files that are outside this task and must not be staged or modified without an explicit decision:

- `data/vajont_fig5a_curves_2_3_4_5_58_mm_velocity.xlsx`
- `review.md`

Before committing, use an explicit path list; do not use a blind `git add .`.

## 5. Recommended next Codex actions

1. Read this handoff, `AGENTS.md`,
   `docs/ootang_autonomous_research_protocol.md`,
   `docs/ootang_prequential_monitor_results.md`, the prequential config, and its
   output manifest before continuing the machine track.
2. Recheck the final four output hashes and the 97 protected paths. Also verify
   the shared v4 Bai–Perron module is still clean:

   ```bash
   git diff --exit-code -- code/warning/bai_perron_initial_slope.py
   xargs shasum -a 256 < docs/v5_v0_protected_paths.txt | shasum -a 256
   ```

3. Treat both the designated checkpoint/input replay gate and standalone RFC 3161
   shadow as implemented. Keep public live/guard-envelope reconstruction, true forward,
   pinned trust, isolated runtime, causal-time and adversarial tests intact. Do not
   weaken either gate into producer or receipt self-report.
4. Treat R1 registry, R2a same-origin executable preparation, R2b clean-start canonical
   route fence and R2b-2a observation/stale detection as implemented. Preserve the independent drain chain, fixed
   six-lock order, permanent pre-tombstone fence prepare, full intent-prefix and
   intent-as-lower-bound recovery, append-only `drain_exchange_attempts`, terminal
   armed-marker binding, content-addressed full-clean pre-swap boundary/pre-event replay,
   worst-case/actual pre-swap capacity checks, the exact final-fence→stable-CAS→capacity→
   boundary→WAL-terminal→armed-marker→immediate-swap tail, ordinary same-poll exact-state equality, explicit 64 MiB manifest
   and 16 MiB staged-feed limits, and mandatory
   `renameatx_np(RENAME_SWAP)` semantics; never downgrade the exchange to ordinary
   rename. Also preserve R2b-2a historical binding/full replay, deterministic
   observation CAS, append-only event chain, exact same-count cache validation, stronger
   strictly-ahead rollback witness, source-object revalidation, double capture and all
   false authority claims. Preserve the R2b-2b-1 v2 first-blocker observation above:
   exact R1/R2a/old-ledger context, object dereference, v1 precedence, no busy writes and
   all reservation/recovery/enumeration/fence/mutual-exclusion/anti-rollback claims false.
   Preserve the later R2b-2b-2a official-writer lock-path cut, the R2b-2b-2b six-family
   frozen-observation reservation/transition seeds, and the R2b-2b-2c item transition plans,
   create-only step chains and terminal-receipt dependency gate. Do not reinterpret the
   manifest as terminal/transitive closure, and do not count unresolved derived work as
   complete. Preserve the recovery-only expected-pre-head CAS and its storage/transition-authority
   boundary; next add only the narrow machine-only `anchor_request_recorded` adapter. V2 must not
   reinterpret or overwrite v1 fence-prepare/intent-prefix/capsule/intent/exchange-attempt/
   armed-marker/boundary/event bytes; any chunk/Merkle history representation belongs in
   that new version. Failure waits or
   blocks; never add human date selection, freezing, cleanup, approval, force or
   fabricated backfill.
5. Only after terminal receipts and explicit derived-work closure let a separate assessor prove
   the bounded old work is drained, add an
   authoritative active-transition slice, then cycle v4 with trusted-time qualification.
   Keep broader scheduler authorization unavoidable and optimize repeated receipt/ledger
   scans so long-lived operation does not grow as O(N^2). Do not use historical OOF rows
   as future predictions, select a best seed, or backdate a missed target.
6. If improving interval calibration, create a separately versioned,
   predeclared challenger such as SPCI/AgACI and compare it on future E2 data or
   a valid new evaluation protocol. Do not tune the current v1 from the already
   viewed replay and then report the same folds as confirmation.
7. Keep the formal-v5 path separate. Its read-only preflight must still show G0
   PASS, G1--G4 BLOCKED, and G5a unauthorized; do not edit the v1 gate register
   or run NGBoost/fusion to manufacture missing evidence. E3 requires an
   independent machine-readable outcome source, not monitor-derived labels.
8. If changing any current producer, regenerate its explicit outputs and rerun
   targeted tests, the full suite, static checks, deterministic replay, and the
   97-path comparison.
9. The user has authorized committing this increment. Keep implementation, tests and
   documentation in the same non-`test:` commit. Do not push unless separately asked,
   and do not create a pull request.

## 6. Known non-blocking follow-ups

These are not blockers for the completed candidate-display task:

- The automatic-V0 runner still requires ConvLSTM/v4 lineage artifacts for fit-boundary and provenance validation, even though no v4 value participates in V0 selection. A future refactor could separate boundary metadata from v4 lineage, but changing the frozen input contract should be an explicit decision.
- `ootang_v5_candidate_display.py` duplicates a few hashing/profile helper patterns used by the NGBoost pilot. Consolidation is optional and should not blur artifact-specific contracts.
- The automatic V0 module owns its numerically stable per-segment-start OLS cost matrix and dynamic program, but imports the frozen shared BIC definition/constants. Do not move the wrapper into or edit the shared v4 module without reopening G0 and the v4 freeze.
- No current `figures/pipeline/latest_run.json` exists because the default pipeline was intentionally not run.

## 7. User/project instruction snapshot

The following is included so Codex does not lose the session's persistent context. Verify live files before relying on any old memory.

### `/Users/wcqqq1214/.claude/projects/-Users-wcqqq1214-Project-Landslide-Warning/memory/MEMORY.md`

```markdown
# Memory Index

- [Git workflow rules](feedback_git_workflow.md) — no PRs, push to main directly, no Co-Authored-By, linear history, no superpowers docs
```

### `/Users/wcqqq1214/.claude/projects/-Users-wcqqq1214-Project-Landslide-Warning/memory/feedback_git_workflow.md`

```markdown
---
name: feedback_git_workflow
description: "Git workflow rules for this project — no PRs, push directly to main, no Co-Authored-By, linear history"
metadata:
  node_type: memory
  type: feedback
  originSessionId: f6e2f739-d868-47e0-893e-66a510f37d01
---

- Do NOT create pull requests. Push directly to main — personal repository.
- Do NOT add `Co-Authored-By` lines to commit messages.
- Commit format: `type: description` — English, lowercase, concise.
- Do NOT commit `docs/superpowers/` or any superpowers-generated docs.
- Keep linear history; force push when needed (e.g., after amend or filter-repo).

**Why:** Personal repo, no review process needed. User prefers clean, linear git history.
**How to apply:** On every commit/push operation in this project.
```

### `/Users/wcqqq1214/.claude/CLAUDE.md`

```markdown
# Git commits

- Unless explicitly requested otherwise, keep commit messages concise: use a short subject line and add a body only when it provides necessary context.
- Do not add `Co-Authored-By` or other attribution trailers unless explicitly requested.
```

### Repository `CLAUDE.md`

```markdown
@AGENTS.md
```

### Repository `AGENTS.md`

```markdown
# Interaction Principles

- You may challenge my views — I'm not always right. Maintain a critical mindset: question my instructions and opinions, point out flaws when you see them, and suggest better alternatives.

# Research Tooling

- `academic-research-suite` is available for literature review, manuscript structure, citation checks, revision, and peer-review simulation.
- Use it as an advisory research-writing tool only; do not let it modify frozen validation splits, metrics, thresholds, model structure, or experimental conclusions.
- All literature claims must be checked against source papers, versioned data, and reproducible project artifacts before being treated as final.

# Commit Guidelines

- Do NOT add `Co-Authored-By` lines to any commit messages.
- Format: `type: description` — English, lowercase, concise.

# Git Rules

- Do NOT commit files under `docs/superpowers/` or any superpowers-generated documentation into git.
- Do NOT create test-related commits (commits with `test:` prefix). Test changes should be squashed into or amended to the feature/fix commit they relate to.
- Do NOT create pull requests. Push directly to main — this is a personal repository.
```

## 8. Final review status

The original independent review found four blockers: estimates on unavailable stations, an unqualified interval color, modification of a shared v4 module, and missing automated v4/report checks. All four were fixed before the original candidate-display commit:

- unavailable estimates are now null
- `interval_color` is no longer materialized
- the shared v4 Bai–Perron module matches `HEAD`
- hash-immutability and real LaTeX-build tests were added

A second independent review of the numerical-audit follow-up found the numerical implementation sound, then identified integration blockers in provenance validation and the G5/G6 gate wording. The current working tree adds fail-closed drift tests for the full profile method, V0 formula, runner/shared-BIC records, complete kinematics/predictions/forecast/v4 lineage, and all station fit boundaries. The protocol now separates G5a/G6a generation authorization from G5b/G6b calibration acceptance and consistently reserves historical test/confirmation data from selection. The final read-only re-review reported no remaining blocker. Exact verification commands and the 97 protected paths are versioned in the audit documents; any later edit must repeat them before relying on this verdict.
