## Architecture

UI Capability Runtime separates model-assisted discovery from deterministic,
model-free execution. Python 3.12, strict Pydantic contracts and Playwright drive
real Chromium against a synthetic, server-rendered banking application. The
implemented task prepares a savings sub-account and stops at unsubmitted review;
it does not open an account. [README](README.md) provides cached setup, reviewer
commands and terminal handoff instructions. [Audit-closure verification](evidence/audit-closure/verification.json)
records checks collected before commit and publication; its pending status is
historical. [Publication preparation](evidence/audit-closure/publication-preparation.json)
identifies later documentation-only changes, and [checksums](evidence/audit-closure/checksums.json)
cover the current file inventory. The [Phase 5 manifest](evidence/phase5-submission/manifest.json)
and its verification/checksums retain the historical snapshot at **9496484**.

`discovery.py` offers an explicitly authorized text-only planner policy-filtered
observations and typed references. `compiler.py` records executed actions with
observed semantic checkpoints, matches reviewed target bindings, and discards
fixture step recipes before discovery. Contracts, completion criteria and error
detectors remain authored knowledge. `replay.py` consumes the resulting data
without importing discovery/providers. Both paths use `session.py` for ownership,
freshness, action authorization and execution. The Playwright surface uses visible
UI only; sandbox business-state oracle access belongs exclusively to external
tests/probes.

Natural-language intent is `CapabilityArtifact.goal.goal_template`; typed inputs
are separate, and `goal.binding` names app/version/exact origin/entry route.
`Discovery(template, profile, session, planner, caller_permissions).run(inputs)`
accepts reviewed contracts and a policy-bound session, not arbitrary prose/URLs.
The CLI supplies the banking template and an isolated sandbox; it has no generic
goal/target discovery flags. `Policy._validate_terminal` is demo-specific.
Existing-origin `replay --origin` explicitly rebinds a compatible demo artifact,
not a new application's workflow. These limits keep authority reviewable.

The [genuine historical discovery summary](evidence/phase3-live/fff6f1953ef04b81bfc567ce9c486628/summary.json)
records `gpt-4.1-2025-04-14`, validated response/request metadata, nine provider
calls and eight actions in 16.526 seconds. Its [fresh replay](evidence/phase3-live/fff6f1953ef04b81bfc567ce9c486628/fresh-replay/a535f375c5244e18bf5198efb0026577/summary.json)
succeeded in 2.104 seconds with changed member/product/nickname and zero provider
calls. These are single-run timings, not latency benchmarks or generalization
measurements. Exact historical source-revision metadata was not recorded.

## Artifact schema

[`contracts.py`](src/ui_capability/contracts.py) defines a versioned, strict,
extra-field-rejecting data language, not executable browser code. A capability
contains its identifier/version, goal template, classified typed inputs/outputs,
application/version/origin/entry binding, budgets, surface requirements, semantic
targets, ordered actions, pre/postconditions, identity and terminal predicates,
extractions, output matches, business outcomes, recovery references, permissions,
redaction declarations and provenance.

Targets combine a main/named frame, optional section and role, label or table
caption locator, requiring exactly one match. Values are tagged input, secret,
local or public-literal references. Leading-zero identifiers stay strings; money
is parsed into integer minor units. Unresolved references and secrets fail closed.
The [reviewer artifact](evidence/phase4-adversarial/revised.capability.json) takes
`member_id`, `product_code` and `nickname`; outputs also include monthly fee,
currency and `submitted=false`.

Packaged JSON is explicitly `test_fixture` / `hand_authored_test`; scripted test
planners are not LLM evidence. The retained live artifact is `live_discovery` /
`observed_live`. Reviewer version 2 is a **reviewed checkpoint revision**, not new
discovery. [Derivation](evidence/phase4-adversarial/derivation.json) links canonical
hashes: 16 redundant route predicates were removed while preserving all eight
observed actions, semantic checkpoints, typed references, identity/output checks,
recovery and original provenance. Original evidence is unchanged.

## Determinism & error handling

Replay follows saved actions and predicates rather than asking a model to
reinterpret each screen. One executor lock, ownership epochs, observation
freshness, target re-resolution and exact uniqueness reject stale or ambiguous
proposals. Trusted profile/policy rules cannot be widened by an artifact. Current
route authorization remains mandatory before each action, including after
recovery; removing route-pinned checkpoints did not remove this gate.

Policy, identity and contradictory-state checks prevent a plausible-looking
review from becoming success for the wrong member. Declared absence or business
validation is distinct from failure: CLI exits are 0 for verified success, 3 for
a business outcome and 2 for failure/aborted intervention. Bounded settling,
reviewed recovery and retry rules do not authorize repetition of unknown or
irreversible effects. Cancellation is not rollback; unsettled actions retain
their gate. Unknown dialogs and browser interruption fail closed.

Failures include the step and bounded diagnostic stage/expected/observed
categories, counts and predicate positions rather than raw exception text.
Action intent, execution and rejection records combine fixed kind/purpose/effect
codes with numeric indexes, explaining replay, recovery or discovery intent
without retaining model reasoning. Target/output indexes address sorted artifact
keys; guard indexes address profile tuple positions. Nested predicate paths index
checkpoint tuples; profile terminal predicates precede artifact final checks.

The retained eleven-case adversarial matrix includes the original route failure.
Historical hardening recorded 221 passing tests; current local verification is
linked above, not a new live-model claim. No new provider run followed hardening.

## Heterogeneity & multi-tenant

The implemented sandbox exercises named frames, generated IDs, table-labelled
controls, scoped duplicate labels, slowness and expiry. Semantic scopes avoid
saved coordinates and generated-ID dependencies. This is one reviewed legacy web
application, not arbitrary-site portability. Each demo isolates the application
and browser context; origin rebinding preserves app/version/entry compatibility.
Locale is `en_US`; desktop and multi-tenant deployment are not implemented.

**Future design, not implemented:** following the existing
[design seam](INTERFACE_AI_DESIGN.md#12-heterogeneity-and-tenant-reuse), keep
observe, resolve target, perform typed action, evaluate bounded predicate, safe
capture and handoff hooks behind the surface adapter. Flow/control logic must
not depend on Playwright `Page`, CSS or cookies. Browser targets remain
frame-aware semantic/relational bindings; a desktop adapter needs a distinct
target family using OS accessibility or reviewed visual anchors. Declare surface
requirements and reject unsupported families before execution rather than
pretending a browser locator translates to desktop.

Separate a **base capability** (steps, typed I/O, invariants, outcomes), **product
profile** (error predicates, effects, version compatibility, target conventions),
and versioned **tenant binding** (origin, entry route, frame labels, locale,
narrowly reviewed label overrides). **Runtime policy** remains the authority
ceiling: overrides cannot widen permissions, effects or allowed destinations.
Resolve and hash the base/profile/binding separately and retain the installed
snapshot, so institutions can share logic without hiding specializations.
Check declared product versions and required target structure at installation
and runtime checkpoints. Unknown versions, missing/ambiguous targets or
incompatible structure invalidate execution; require reviewed rebinding and
revalidation, never silent model “healing.” This is a compatibility design, not
evidence of cross-tenant reuse.

## Escalation & handoff

Reviewed intervention guards (such as expiry) and unknown dialogs route a
session/step/reason-bearing request to the terminal operator; installed contracts
and the safe structural snapshot provide goal/state context. Ordinary hard
failures stop rather than implicitly granting human or model authority.
Replay transitions from AUTOMATION through PAUSING and AWAITING_OPERATOR to HUMAN.
The terminal's `takeover` transfers control; the operator fixes the existing
browser and requests `resume`. Resume validates that same context, allowed
location, identity, saved step and checkpoint before restoring automation.
Invalid resume retains HUMAN ownership, and automation is rejected while a human
owns the session. `capture` and `abort` are available; EOF or timeout aborts.

The [historical manual summary](evidence/phase2-fixtures/manual/54c4cbabdeae477d824e8b2eb5591620/summary.json)
records a successful user-guided same-session reauthentication, terminal control
transfer, `human_intervened=true`, zero provider calls and 57.043 elapsed seconds.
This is retained human-handoff evidence, not a newly repeated demonstration or a
human-identity attestation. Automated integration-test operators are labelled
separately. Discovery stops on intervention instead of supporting human resume.
The process must remain alive; durable crash recovery is absent.

## Safety

Prepare-only policy denies the final **Open account** action. Context request
guards and a mandatory loopback HTTP proxy independently enforce exact
method/origin/path allowlists; external tests/probes check receiving-server and
ledger counters. The sandbox mutation is real, avoiding a no-op safety claim.
Redirects, CONNECT, websocket upgrades, service workers and unsupported uploads
are denied. Response-header checks occur only after contacting an allowed
endpoint. The proxy supports explicit loopback HTTP, not opaque HTTPS tunnels;
ordinary unguarded browsers are outside this boundary.

Provider requests exclude restricted values, screenshots and hidden state.
Persisted diagnostics contain only fixed categories, indexes, counts and flags:
no runtime values/names, UI text, URLs, exception messages or model explanations.
Approved goal/artifact/profile snapshots supply the index mapping. A bounded
semantic failure snapshot records up to 100 sorted targets' match counts,
missing/unique/ambiguous or unavailable state, control/value-type counts and
presence flags, plus an omitted count. Stale observations do not reuse target
details. Reviewers can locate a mismatch without field contents or locators.
Screenshots remain entirely opaque with decoded-pixel verification, or are
withheld; the structural sidecar supplies the useful richer failure signal.
Historical captures retain their original workspace-only guarantee. Raw model
transcripts, keys, traces and rich output values are not public evidence.
Configuration must be trusted public text. This is neither OS egress isolation
nor atomic protection against hostile DOM mutation or a production attestation.

## Cuts

The deliberate cut is one bounded, prepare-only workflow: no arbitrary-site
compilation, irreversible submission, model-driven recovery, discovery-time
resume, durable workflow service, desktop implementation or tenant platform.
These avoid unreviewable authority and infrastructure before the core is proven.
Next priorities, in order:

1. Exercise the documented base/profile/binding design on a second synthetic
   product skin, including deliberate version/label drift, to test reuse and
   fail-closed invalidation before claiming it across institutions.
2. Extend same-session ownership/checkpoint handoff to discovery, with reviewed
   continuation rules, to close the current stop-on-intervention asymmetry.
3. Prototype one desktop accessibility target family and its safe capture/
   ownership boundary, rejecting unsupported semantics before broadening scope.

Historical revision gaps and missing per-run hardening diffs cannot be repaired
retroactively. Optional API discovery requires explicit cost authorization;
call/token limits are not a billing cap. No new paid-model run or real-human
handoff was performed for this closure. Verification records predate publication;
email submission is a separate step.
