## Architecture

UI Capability Runtime separates model-assisted discovery from deterministic,
model-free execution. Python 3.12, strict Pydantic contracts and Playwright drive
real Chromium against a synthetic, server-rendered banking application. The
implemented task prepares a savings sub-account and stops at unsubmitted review;
it does not open an account. [README](README.md) provides cached setup, reviewer
commands and terminal handoff instructions. The [submission manifest](evidence/phase5-submission/manifest.json)
and [verification record](evidence/phase5-submission/verification.json) identify
the public snapshot and fresh local evidence, not remote publication.

`discovery.py` offers an explicitly authorized text-only planner policy-filtered
observations and typed references. `compiler.py` records executed actions with
observed semantic checkpoints, matches reviewed target bindings, and discards
fixture step recipes before discovery. Contracts, completion criteria and error
detectors remain authored knowledge. `replay.py` consumes the resulting data
without importing discovery/providers. Both paths use `session.py` for ownership,
freshness, action authorization and execution. The Playwright surface uses visible
UI only; sandbox business-state oracle access belongs exclusively to external
tests/probes.

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

The retained adversarial matrix covers eleven CLI cases and preserves the original
route-checkpoint failure. Historical hardening verification recorded 221 passing
tests. Local tests include scripted discovery, transport denial, concurrency and
crash cases; they are not paid provider runs. No new provider run followed
hardening, so changed discovery/provider behavior has local regression evidence,
not renewed live validation.

## Heterogeneity & multi-tenant

The sandbox exercises named frames, generated element IDs, table-labelled legacy
controls, duplicate labels in separate sections, slow rendering and session
expiry. Semantic scopes avoid saved coordinates, ordinal selectors and generated
IDs. These demonstrate variation within one reviewed web application, not
arbitrary-site portability. New compiler output requires reviewed target bindings
and caller-supplied goal/output contracts and recovery knowledge.

Each demo creates an isolated application and fresh browser context. Installation
binding changes only the explicit origin; application, version and entry-route
compatibility remain checked. Ephemeral ports therefore change installed hashes.
Separate sessions and exact origins are useful boundaries, but no tenant
provisioning, cross-tenant authorization service, native desktop adapter or
production deployment is implemented. Locale is restricted to `en_US`.

## Escalation & handoff

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

Provider requests exclude raw restricted inputs, screenshots and hidden state.
Evidence excludes raw prompts/responses, keys, rich outputs and exception details.
Current screenshots mask the entire page and verify opaque pixels before writing,
or are withheld. This deliberately sacrifices visual usefulness; structural
checkpoints/events provide execution detail. Historical workspace-masked captures
retain their original, narrower guarantee. Trusted configuration must contain
public text. These controls are not OS egress isolation, atomic protection from
hostile DOM mutation, or a production security/privacy attestation.

## Cuts

Scope favors one bounded end-to-end workflow over arbitrary applications,
unreviewed compilation, model-driven recovery or irreversible submission.
There is no discovery-time resume, persistent workflow service, desktop support
or multi-tenant deployment. API access requires explicit authorization and may
cost money; call/token limits are not a billing cap. Historical run revision gaps
cannot be repaired retroactively, and hardening's CLI matrix lacks exact per-run
source diffs. Fresh local snapshot verification is documented separately from
those runs. No new paid discovery, real-human handoff, commit, push, publication
or email is claimed by submission packaging.
