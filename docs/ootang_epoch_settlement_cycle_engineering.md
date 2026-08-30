# Ootang epoch scope audit and lean settlement cycle v1

## Audit verdict

The scientific backbone has not been displaced: the default pipeline remains
`features -> convlstm -> ootang-operational-v4`, and this increment does not
change ConvLSTM, v4, frozen splits, metrics, thresholds, model parameters, or
scientific conclusions.

The engineering priority did drift locally. Before this increment, the epoch
subsystem contained 57,380 production lines and 17,907 test lines. The 35
epoch commits from candidate preparation through bounded closure created a
large chain of narrowly scoped authorities, while the newest four aggregate /
coverage modules had no CLI. In particular, bounded terminal closure had no
downstream production caller: its coordinator was called only by its focused
test. The work therefore remained directionally relevant to automatic epoch
rotation, but implementation effort had become concentrated on proof
construction rather than an executable machine path.

There is also direct evidence of local over-defensive composition. An
instrumented successful poll of the existing ready-state bounded-closure test
fixture took 0.623 seconds but invoked profile loaders 418 times and
`registry._read_regular` 3,535 times, covering 101 unique paths and about 246 MB
of cumulative reads. The recovery profile was loaded 116 times; the same
recovery config and implementation were read 359 and 243 times respectively.
This is a diagnostic of recursive revalidation, not a scientific benchmark.
Across the epoch modules, repeated canonical-JSON, status, event, private
cross-module helper, and false-capability shells further confirm that the same
immutable cut is often reconstructed at each layer.

The defensiveness is not uniformly wrong. Strict validation remains necessary
at external JSON/filesystem boundaries, append-only event and CAS commits,
cross-process locks, network/TSA operations, and crash-forward adoption. The
excess is revalidating already validated immutable state repeatedly inside one
poll and assigning a new durable proof/event/status namespace to every
intermediate Boolean fact.

Testing shows the same distinction. The recent 3--6 focused contracts per
increment are proportionate. The excessive cost came from repeatedly running
overlapping 15--30-test adjacent chains after every small layer and from older
multi-minute/full-suite gates. Existing fault and tamper tests protect real
durable boundaries and are retained; they do not need to be copied into every
aggregate.

## Corrective implementation

`ootang_epoch_settlement_cycle.py` is the first production consumer of the
post-manifest chain through bounded terminal closure. It exposes one CLI and
one explicit `main.py` stage, `ootang-epoch-settlement-cycle`.

After the existing workset-recovery stage has run once, one settlement poll
calls the following 17 public coordinators exactly once in topological order:

1. step-dependency reservation, overlay, source aggregate, and frozen-manifest
   coverage;
2. source-ingest derived reservation and cross-freeze adoption;
3. current source-derived overlay, source/dependent dispatch and consumption;
4. effective, parent, retained-base, and current-effective coverage;
5. bounded terminal closure; and
6. the V2 scoped bounded-drain completion assessor.

The single-pass bound is deliberate. Recovery can include a bounded external
transport, so an internal 17/68-call loop would multiply latency and network
attempts. A machine scheduler may invoke this endpoint again; no human
selection, freeze, approval, cleanup, force, or backdating is required.

The cycle does not duplicate upstream profile, implementation, proof, event,
or runtime validation. Each existing coordinator remains the owner of its own
contract. The cycle invokes a static table of the 17 public callables once and
atomically replaces a small status cache containing stage statuses, the
bounded-closure result, and the final scoped-drain result. The cache is
explicitly non-authoritative; the completion assessor owns its one immutable
decision event.

There is deliberately no settlement-cycle profile. The removed profile fixed
every value to a code constant, including the path and `passes_per_poll=1`, so
it provided no runtime choice. There are also no result/progress digests: no
consumer compared them or used them for continuation, and they hashed returned
dataclass summaries rather than the referenced artifact bytes. Drain,
lifecycle, active-switch, E2, and formal-warning authority remain absent by
construction and are documented at the owning stage boundary instead of being
copied into eight status fields.

Keeping the stage explicit-only is intentional: the default scientific
pipeline is a reproducible offline model run, while this stage mutates the
separate live epoch runtime. Explicit-only does not imply manual daily
operation; it is a scheduler-facing machine endpoint.

## Simplification receipt

This was a focused change-mode simplification of the settlement adapter only.
The reachability audit found no repository consumer for its profile, profile
hash, per-result hash, progress token, or duplicated capability claims. The
following ceremony was removed:

- the 21-line constant-only profile, its loader, exact-key validation, and
  `--config` interface;
- canonical reconstruction and hashing of trusted same-process return values;
- a stringly `importlib/getattr` registry and its dedicated resolver test;
- three exception layers and result fields with no consumer;
- duplicate Python inputs in `main.py`, because the pipeline's global
  `source_fingerprint()` already hashes every `code/**/*.py` file.

The `main.py` stage retains the transitive workset-recovery profile and the 16
settlement coordinator profiles as explicit inputs. The profile-free completion
module is already covered by the pipeline's global `code/**/*.py` source
fingerprint, so it is not duplicated as a stage input. The real filesystem
boundary also remains: cycle-status publication still uses a temporary file
plus `os.replace`, but intentionally adds no lock, `fsync`, replay log, or
symlink policy for this replaceable cache.

The implementation fell from 414 to 230 lines, the focused test from 107 to 89
lines, `main.py` lost 22 duplicate contract lines, and the 21-line profile was
deleted. Those four files remove 245 lines; including the one-line main
integration-test adjustment, code/config/tests/main remove 244 lines net. The
known compatibility risk is limited to an unknown out-of-repository caller of
the removed `--config` option or rich status fields; repository search found
no such consumer, and README usage goes through the argument-free main stage.

## Verification policy applied

The orchestration behavior has two focused tests:

- one ordered poll reaches the V2 scoped drain decision and preserves both
  upstream closure and non-authoritative cycle-cache state;
- a nonterminal poll calls every coordinator once and yields cleanly to the
  next scheduler invocation.

`tests/test_main.py` adds one integration contract for stage ordering,
explicit-only scope, inputs, output, and its argument-free invocation. Importing
the cycle resolves all 17 static public callables, so a separate dynamic
registry test would duplicate module-import coverage. This is the complete test
budget for this non-authoritative adapter. The main/cycle set passes in well
under one second; Ruff, Python compilation, dry-run routing, residue search,
and diff checks are also used. No model training, full scientific pipeline,
historical exhaustive fault matrix, real network, or live epoch mutation is
run for this increment.

## Next boundary

The V1 drain-start and V2 bounded-closure branches are mutually exclusive, so
the earlier plan to consume both was unreachable. The V2 scoped completion now
uses the stable admission cut, existing bounded closure, and one fresh
four-lock inventory without adding another intermediate authority. Its
downstream atomic `SEALED(old) + ACTIVE(new)` transition and no-argument
authorized cycle-v4 adapter are now implemented as separate explicit machine
stages. The transition consumes the scoped decision, binds the prepared epoch
and frozen cycle-v3 bytes, and grants only the scoped official scheduler
entrypoint; it does not turn this settlement cache into authority.

V1 completion still must be coupled to its own writer cut or a separately
specified atomic transition; it must not be promoted from a staleable
standalone observation. The next operational boundary is an isolated real
machine run through settlement, transition and cycle-v4/genesis. External
trusted-time/anti-rollback qualification, E2/formal promotion, and a
multi-generation continuous rotation controller remain separate later work.
