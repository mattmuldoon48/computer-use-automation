# Evidence index

Start with [the demo](../README.md) and
[the seven-section report](../REPORT.md). This index selects exact runs; do not
choose artifacts by “newest file.” Captures are intentionally opaque; use
value-free semantic snapshots and event/result diagnostics.
Resolve numeric target/output indexes against sorted names in the saved
artifact/profile/goal snapshots; guard indexes follow profile order and
predicate paths identify positional predicate clauses.

## Final publication review

[Post-review verification](final-review/publication-review/verification.json)
records the final compiler API hardening: every direct compiler call now requires
independent reviewed targets. The supported Discovery path already supplied that
map during all live runs. Those live receipts below retain their exact earlier
source digest; they are not relabelled as executions of this later source.
The final source has separate local regression and browser-smoke verification,
with no additional paid run.

## Current authorized live discovery

- [Discovery summary](final-review/live/5af33fc272cc42e49af6012e6a2b0ed9/summary.json):
  `gpt-4.1-2025-04-14`, nine completed/validated provider calls, eight automated actions.
- [Exact saved capability](final-review/live/5af33fc272cc42e49af6012e6a2b0ed9/candidate.capability.json)
  and [paired changed-input replay](final-review/live/5af33fc272cc42e49af6012e6a2b0ed9/fresh-replay/60fbe5f8de98457daf5ad3290d16a681/summary.json):
  both succeeded at unsubmitted review; distinct browser sessions, zero replay model calls.
- [Verification and usage](final-review/live/verification.json) and
  [exact selections](final-review/live/selected-runs.json) retain source, artifact and
  profile hashes, versions, actual provider metadata and the authorized command.

This run was unassisted. It verifies genuine discovery and fresh replay, **not
real-human discovery handoff**. The runtime digest matches the locally tested
closing pass; its base commit is recorded with `worktree_dirty=true`, not presented
as committed source. Prior local verification records below remain unchanged
snapshots from before authorization, including their then-pending live status.

## Current real-user discovery handoff — completed

- [Human-assisted discovery](final-review/live-handoff/ebf307649aec48cebc0e25a743c9d151/summary.json)
  succeeded with ten completed/validated model calls and eight compiled actions.
  The user restored expiry, then confirmed the unchanged preparation screen after
  a planner-requested pause. Both same-session resume validations succeeded.
- [Exact assisted candidate](final-review/live-handoff/ebf307649aec48cebc0e25a743c9d151/candidate.capability.json)
  and [paired changed-input replay](final-review/live-handoff/ebf307649aec48cebc0e25a743c9d151/fresh-replay/f6ccd2aa8a6d4df79db7c50c08fd0a5a/summary.json)
  share the same canonical artifact hash. The user separately restored replay
  expiry; replay succeeded with zero model calls. Each workflow issued eight
  automated actions, with no repeated preparation click or action while HUMAN-owned.
- [Selections](final-review/live-handoff/selected-runs.json) and
  [verification](final-review/live-handoff/verification.json) record the actual
  user participation, ownership transitions, provider usage and source fingerprint.

This is **human-assisted**, not autonomous. Candidate provenance retains
`same_session_operator_restoration` and immutable `candidate_unverified`;
the external summary records `validated_with_operator_dependency` after the exact
paired replay. Human browser actions were not compiled as arbitrary steps.

The earlier [interrupted attempt](final-review/live-handoff/interruption.json)
remains unchanged: the user accidentally closed the browser before resume,
so it was explicitly aborted after five calls, without a candidate or replay.
The successful fresh attempt above was separately authorized, not an automatic retry.
The [original bounded request](final-review/authorization-request.json) and all
earlier receipts retain their original scope and then-current status.

## Current closing pass — local only

- [Verification and actual failures/resolutions](final-review/verification.json),
  [pre-edit reproductions](final-review/baseline-reproductions.json),
  [selected runs](final-review/selected-runs.json) and
  [source snapshot](final-review/source-snapshot.json).
- [Short nickname discovery](final-review/local/privacy-a/ad4b79501409407daec28cdfd92f888d/summary.json),
  its [exact candidate](final-review/local/privacy-a/ad4b79501409407daec28cdfd92f888d/candidate.capability.json),
  and [paired changed-input replay](final-review/local/privacy-a/ad4b79501409407daec28cdfd92f888d/fresh-replay/5487eed9f040436d8a670c2e6d591ade/summary.json).
  The [public-label collision run](final-review/local/privacy-Review/e1872df10cd44314bf7fb234f951827a/summary.json)
  independently exercises nickname `Review`.
- [Discovery expiry handoff simulation](final-review/local/discovery-handoff-simulation/e166d93a13f1418a95cca59935096c02/summary.json),
  its [assisted candidate](final-review/local/discovery-handoff-simulation/e166d93a13f1418a95cca59935096c02/candidate.capability.json),
  and [paired changed-input replay](final-review/local/discovery-handoff-simulation/e166d93a13f1418a95cca59935096c02/fresh-replay/989bb81794904c93bf86082566e9283a/summary.json).
  Both browser sessions reject premature resume, then continue after restoration.
  One prepare request each; no repeated pending click, submission or ledger entry.
- [Current identity-denial snapshot](final-review/cli-wrong_member_review/cd2773c6e7c34827bdadc1ffe30e8da0/failure.snapshot.json).
  Selected runs also include success, reviewed recovery and member-not-found CLI exits.

These use real Chromium with **scripted planners and simulated operators**, not
paid models or real humans. A `terminal_control_transfer` event alone does not
identify the person driving it. [Local probe assertions](final-review/local-probes.json)
state actor identity explicitly. Assisted candidates retain their operator
restoration dependency; successful paired replay is recorded externally without
rewriting candidate provenance or claiming autonomy.


## Historical genuine discovery and real-user replay handoff

| Evidence | Exact selection | Interpretation |
| --- | --- | --- |
| Genuine model discovery | [Summary](phase3-live/fff6f1953ef04b81bfc567ce9c486628/summary.json) | `gpt-4.1-2025-04-14`, nine calls, eight compiled actions |
| Its exact artifact | [Candidate](phase3-live/fff6f1953ef04b81bfc567ce9c486628/candidate.capability.json) | Original observed version; canonical hash `8623528337ad214edae19c600614888293577922fbbbd93d3932f9fb04086ae9` |
| Paired changed-input replay | [Summary](phase3-live/fff6f1953ef04b81bfc567ce9c486628/fresh-replay/a535f375c5244e18bf5198efb0026577/summary.json) | Same canonical artifact hash, distinct session, zero provider calls |
| Actual user-operated replay handoff | [Summary](phase2-fixtures/manual/54c4cbabdeae477d824e8b2eb5591620/summary.json) | Hand-authored replay fixture; not discovery-time handoff |
| Discovery error/intervention | [Summary](phase3-live/d14473f60bc446ad8f0fbeef79034e84/summary.json) | Historical model-requested intervention aborted; not evidence of continuation |

[All original live attempts](phase3-live/selected-runs.json) preserve failures as
well as success and aggregate provider usage. Those runs predate the current
per-run source fingerprint; their exact executed revision cannot be retroactively
attested by today's commit. They are historical observations, not fresh
verification of this closing pass.

## Earlier review and diagnostic history

[Phase 4](phase4-adversarial/selected-runs.json) records adversarial cases and
[reviewed version 2](phase4-adversarial/revised.capability.json), a reviewed
adaptation rather than new model discovery. [Phase 5](phase5-submission/verification.json)
and [the preceding audit closure](audit-closure/verification.json) retain their
original test/build/source records. [Audit selections](audit-closure/selected-runs.json)
include malformed/missing fee diagnostics and explicit candidate-replay commands.
Their counts and source inventories belong to those revisions, not the current
working tree. Existing evidence and checksum inventories remain byte-for-byte
unchanged; the current pass has its own inventory.
