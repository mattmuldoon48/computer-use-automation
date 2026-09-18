# UI Capability Runtime — Discovery and Model-Free Replay

A Python 3.12 UI runtime with a synthetic legacy banking application, real
Chromium execution, prepare-only policy, and same-session replay operator controls.
Phase 3 adds opt-in structured OpenAI discovery and deterministic compilation.
**Genuine model-driven discovery and fresh model-free replay have succeeded.**
The selected run is
`evidence/phase3-live/fff6f1953ef04b81bfc567ce9c486628/`.
Local tests still use explicitly scripted actors, not an LLM. Packaged browser
fixtures remain hand-authored; the selected live capability is separate. The
original planning documents are unchanged.

## Setup and checks

Python 3.12 and uv are required. Installation needs package-index/browser-download
access unless cached; obtain authorization first. Dependencies and tools are
pinned in `pyproject.toml` and `uv.lock`.

```sh
uv sync --locked
uv run playwright install chromium
uv run --offline pytest -q
uv run --offline mypy src sandbox
uv run --offline ruff check .
uv run --offline ruff format --check .
uv lock --check --offline
uv build --offline
```

Playwright 1.58.0 selects Chromium **145.0.7632.6, revision 1208**. The browser is
installed in Playwright's per-user cache, not committed. Each run records its
actual browser and Playwright versions. Offline commands require prior setup.
Tests use isolated loopback servers; no external bank or business service.

- `tests/unit`: fake-surface safety, fixture binding, structured provider transport,
  discovery budgets, privacy, freshness, and compiler regressions.
- `tests/integration`: real Chromium workflows, DOM-derived targeting, compiled
  replay with changed inputs, receiving-server checks, privacy, and **automated**
  operator simulations. These tests make no external model requests.
- `evidence/phase2-fixtures`: selected real fixture executions and development
  attempts. Failed/withheld attempts are retained, not relabelled as successes.
  Public evidence is separate from default ignored `artifacts/local` output.
- `evidence/phase3-live/selected-runs.json`: indexes the successful genuine
  discovery/replay and retained failed development attempts.

## Authorized model discovery

The implemented goal is to prepare a savings sub-account and stop at unsubmitted
review. Its natural-language template, typed input/output contract, and reviewed
application policy are caller-supplied to `Discovery`; the CLI binds this one
reviewed task to an isolated local sandbox. This is not arbitrary-site automation.

Set `OPENAI_API_KEY` privately in your shell and `OPENAI_MODEL` to a currently
supported Responses API model with structured outputs. Do not paste credentials
into chat, commit them, or pass them as command-line arguments. Confirm model
availability and authorize the cost before running:

```sh
uv run --offline uicap discover \
  --model "$OPENAI_MODEL" --authorize-api \
  --max-calls 12 --max-output-tokens 1200 --call-timeout 30 \
  --evidence-dir evidence/phase3-live
```

`uv --offline` prevents package fetching, **not model API traffic**. `--authorize-api`
explicitly permits paid requests and sending the filtered observation to OpenAI.
No model request occurs without that flag. No provider SDK or new dependency is
required. Missing credentials, refusal, incomplete responses, and transport errors
fail closed; their raw details are never persisted.

The default invocation discovers with synthetic member A, then closes that browser
and replays the compiled candidate in a fresh browser with synthetic member B,
a different product, and a different nickname. Optional `--inputs-file` and
`--replay-inputs-file` replace those invocations; all three replay inputs must differ.
`--headed` displays the browsers. Default limits: 20 actions, 180 active seconds,
12 API attempts, 1,200 output tokens per attempt, and 30 seconds per call.
There is no automatic HTTP retry; at most one malformed-decision repair consumes
another call. Token/call limits are not a dollar-denominated billing cap.

Each attempt retains redacted events and a summary, including actual API attempt
counts, validated response/request IDs and token usage when available. It never
saves raw prompts, model responses, keys, or rich result outputs. Successful
completion writes `candidate.capability.json`; `summary.json` marks it validated
only after fresh replay succeeds. The nested `fresh-replay/` directory has separate
session IDs, captures and a zero-provider-call result. Failures are retained.

### Verified live example

Selected discovery: `evidence/phase3-live/fff6f1953ef04b81bfc567ce9c486628/`.
The actual model was `gpt-4.1-2025-04-14`: nine API calls produced eight browser
actions and a verified finish. `candidate.capability.json` has `live_discovery` /
`observed_live` provenance tied to the discovery run ID.

Its `fresh-replay/a535f375c5244e18bf5198efb0026577/` subdirectory records successful
replay with a new browser session and different member, product and nickname,
using zero model calls. Both runs verified the unsubmitted-review contract.
Artifact hashes match across compilation and replay.

Five failed development attempts remain alongside the successful one. They include
invalid input references, premature completion, a policy denial, and a model-requested
intervention; none produced a successful capability. The failures drove stricter
generation schemas and clearer typed-reference/completion semantics, not weaker
execution checks. All six attempts together used 29 actual API calls.

To replay a saved candidate independently:

```sh
# Terminal 1
uv run --offline uicap sandbox serve --port 8765
# Terminal 2; CAPABILITY_PATH points to the saved candidate.capability.json
uv run --offline uicap replay --origin http://127.0.0.1:8765 \
  --artifact "$CAPABILITY_PATH" --inputs-file examples/member_b.inputs.json
```

### Discovery boundaries

- `surfaces/discovery_dom.js` derives visible frame/section/role/label/table-caption
  targets from rendered elements, then resolves each locator back to the same
  unique element. Generated IDs, coordinates and ordinal selectors are not saved.
- `discovery.py` sends only current ephemeral target refs, the goal template,
  input definitions, approved public UI text, and typed input references.
  It also exposes public caller-declared enum options, completed input assignments,
  and value-free UI completion-check results.
  Raw restricted values, app source, fixture action recipes and hidden state are absent.
  The planner is text-only; masked workspace screenshots remain local evidence.
- The provider's strict schema enumerates declared input names and currently
  policy-authorized action kinds, target references and navigation destinations.
  Only observed, authorized choices are offered. The adapter checks returned
  references/action combinations, and the session independently rechecks policy,
  state freshness and ownership before executing. Validation diagnostics retain
  only fixed categories, recognized field names and field-presence information,
  never rejected values, unknown keys or exception messages.
- `compiler.py` structurally matches DOM-derived targets against reviewed target
  bindings to restore input references. Unreviewed targets cannot compile. Ordered
  fixture steps are discarded before discovery; no global transcript substitution
  is used. Every executed successful action is retained. Fill/select checkpoints
  preserve the chosen input reference; other actions need an observed semantic change.
- Goal/output checks and known error/recovery rules remain **authored** caller/profile
  knowledge, not discoveries inferred from one happy path. Compiled replay retains
  those recovery rules. Action targeting is observed, but this is not a general
  compiler for arbitrary unreviewed applications.
- All execution uses the existing policy/session gate, fresh observations and
  ownership epochs. Model `finish` requests still require identity, terminal and
  typed-output verification. Replay's dependency graph excludes discovery/providers.
- Discovery stops without an artifact on intervention/recovery conditions; it does
  not implement discovery-time human resume. Existing same-session takeover/resume
  remains supported during replay. Discovery uses one settled post-action observation
  and can conservatively reject delayed state changes or no-effect exploration.

Local verification exercised a scripted eight-action browser discovery and fresh
replay with changed inputs, with zero submissions and zero model requests.
Its ignored development evidence is under `artifacts/local/phase3-smoke/`.
It is **not** the genuine live discovery required for the final assignment.

## Run the complete browser fixture

Each `demo` starts its own isolated sandbox on an ephemeral loopback port, creates
fresh browser state, executes the full workflow, saves evidence, then closes.
No separately running server is needed.

```sh
uv run --offline uicap demo --case happy --evidence-dir evidence/phase2-fixtures/member-a
uv run --offline uicap demo --case happy --inputs-file examples/member_b.inputs.json --evidence-dir evidence/phase2-fixtures/member-b
uv run --offline uicap demo --case member_not_found --evidence-dir evidence/phase2-fixtures/not-found
uv run --offline uicap demo --case wrong_member_review --evidence-dir evidence/phase2-fixtures/identity-denial
```

Exit codes: `0` verified success; `3` declared business outcome; `2` failure or
aborted intervention. The last two commands deliberately demonstrate non-success.
Use `--headed` to display Chromium. The full authored path is navigation → search
→ result selection → member details → preparation → review. It stops at review;
it never opens an account. The UI has a real, separate **Open account** mutation
so denial tests are not testing a no-op.

To inspect the standalone UI and then replay against that installation:

```sh
# Terminal 1: synthetic UI only; ordinary browser access is NOT guarded.
uv run --offline uicap sandbox serve --port 8765
# Terminal 2: the runtime creates its own guarded Chromium context.
uv run --offline uicap replay --origin http://127.0.0.1:8765 --inputs-file examples/member_b.inputs.json --headed
```

The CLI binds the packaged fixture to the explicitly selected installation
origin. It does not replace input literals or accept a changed application,
version, or entry route. `--artifact PATH` may select a compatible authored
artifact; trusted profile/policy authority still comes from the packaged fixture.

## Actual human takeover and resume

**The user-guided takeover/resume demonstration completed successfully.** Its
summary is at
`evidence/phase2-fixtures/manual/54c4cbabdeae477d824e8b2eb5591620/summary.json`:
`success`, `human_intervened=true`, and zero provider calls. The earlier timed-out
attempt is retained. Automated tests are separate from this demonstration.
A `human_intervened` result records ownership transfer, not proof of a person's
identity or physical activity.

```sh
uv run --offline uicap demo --case session_expired --headed --evidence-dir evidence/phase2-fixtures/manual
```

1. Wait for the existing browser to show **Session expired** and the terminal to
   show `operator>`. Do not open a replacement browser/context.
2. Type `takeover` in that terminal. Wait for **HUMAN ownership granted**.
3. In the existing workspace frame, enter a made-up alias such as `demo-operator`
   in **Demo operator**, then click **Demo sign in**. No real credentials needed.
4. Confirm **Prepare sub-account** is visible. Do not change the selected member
   or open an account. Type `resume` in the terminal.
5. Resume validates the same context, allowed location, identity, saved step, and
   checkpoint before restoring automation. An invalid resume leaves HUMAN
   ownership intact. Successful continuation ends at review with `submitted=false`.

`capture` saves a masked capture; `abort` stops the run. EOF also aborts. The
operator budget is 30 minutes and terminal waiting is bounded. The run must stay
alive; process-crash recovery is not implemented. If a coding assistant owns the
terminal process, request takeover/resume through that assistant; only **you**
perform the browser reauthentication step. That is distinct from an automated
integration-test actor.

```text
AUTOMATION -> PAUSING -> AWAITING_OPERATOR -> HUMAN
HUMAN -> VALIDATING_RESUME -> AUTOMATION
Any nonterminal ownership state -> ABORTED
```

## Implemented boundaries

- `contracts.py`, `values.py`: strict tagged values/actions/predicates, typed
  references, exact minor-unit money, leading-zero identifiers, versioned
  artifacts/results, and semantic frame-scoped locators. The new table-caption
  locator supports the legacy adjacent-value layout; arbitrary XPath/CSS/code
  are not artifact commands. Unresolved secrets fail closed.
- `policy.py`, `profiles.py`: trusted origin/port, routes, actions, targets,
  destinations, permissions, literal/effect rules, identity, and terminal checks.
  Artifacts cannot expand authority, rebind trusted targets, weaken completion,
  or authorize retries of unknown/irreversible effects.
- `session.py`, `replay.py`: one executor lock, ownership epochs, freshness and
  semantic drift checks, exact uniqueness, typed pre/postconditions, guard
  precedence, bounded recovery/retries/budgets, and validated terminal outputs.
  Cancellation is not rollback: unsettled actions retain their gate. HUMAN
  ownership rejects automation, including stale proposals and concurrent resume.
- `surfaces/playwright.py`: private browser handles, visible rendered UI only,
  named-frame validation, fixed semantic locators, re-resolution before actions,
  and navigation events armed before clicks. No sleeps-as-completion, hidden
  application state, business APIs, or fixture oracle access. Unknown native
  dialogs are held, never automatically accepted.
- `transport.py`, `http_proxy.py`: context-wide request guards **plus a mandatory
  loopback HTTP proxy**. Chromium HTML downloads can bypass Playwright routing;
  rejecting downloads alone happens too late to prevent server receipt. The
  proxy independently validates exact method/origin/path before forwarding.
  Final submission is absent from its allowlist. Redirect responses are rejected
  without following, websocket routes/upgrades and CONNECT are denied, service
  workers blocked, and multipart/non-form uploads rejected. Responses must be
  HTML without attachment/refresh headers. An allowed endpoint must be contacted
  before response headers can be checked; do not claim those bytes were unfetched.
- `operator_capture.js`: records only bounded event/control/frame categories while
  backend ownership is HUMAN. No field values, keystrokes, DOM text, screenshots,
  URLs, or passwords. Its callbacks cannot grant ownership or resume execution.
- `sandbox`: server-rendered framed HTML, generated IDs, a table-labelled field,
  duplicate labels in distinct sections, review fees, synthetic session expiry
  and cookie rotation, and adverse UI scenarios. Business state/oracle counters
  are private to the fixture/test harness, never HTTP APIs or replay inputs.

The proxy deliberately supports only an explicit `http://127.0.0.1:PORT` fixture
origin; opaque HTTPS tunnelling is not permitted. Browser-normalized URL syntax
cannot be recovered after normalization. These are cooperative browser/HTTP
boundaries, **not OS egress isolation or hostile multi-user isolation**. Native
apps, arbitrary remote banking installations, and multi-tenant deployment are
not implemented.

## Evidence and privacy

A run directory contains structural `events.jsonl`, a redacted `summary.json`,
and capture decisions/images. Current runs also save the exact installed
`fixture.artifact.json`, `fixture.profile.json`, and `fixture.policy.json`.
Installation-specific hashes can differ because ephemeral origin ports differ.
Checksums identify content, not authenticity or cryptographic signatures.

Audit context includes generated run/session IDs, timestamps, artifact/profile
hashes, step indices, and checkpoint counts/stages. It excludes raw inputs,
outputs, exception details, URLs, observation text, and artifact-authored names.
Authorized in-memory results retain typed outputs; do not serialize those rich
results directly into public evidence.

Screenshots mask the **entire restricted workspace iframe before image bytes
are produced**; reviewed static shell/navigation remain visible. Capture is
withheld for changed/unreviewed shell content, unexpected frames/pages, or native
dialogs. Reports retain only structural counts and mask rectangles. There are no
raw traces, videos, DOM dumps, or unredacted fallback screenshots. This sacrifices
business-screen visual detail rather than claiming selective redaction is safe.

## Assignment reconciliation and phase limits

`ASSIGNMENT.pdf` was available and reviewed for Phase 2; it was not available in
Phase 1. The original assignment requires real UI operation, exception handling,
human takeover, and useful execution evidence. Phase 1 supplied only the safe
fake-surface core, not those browser/operator deliverables. Phase 2 adds those
mechanisms and richer sanitized failure/checkpoint evidence. The user-guided
same-session handoff has now been demonstrated as described above.

Phase 3's discovery, compiler and provider adapter are implemented, locally tested,
and exercised through genuine live discovery followed by fresh model-free replay.
The selected evidence is documented above. The JSON under
`src/ui_capability/fixtures` remains explicitly `test_fixture` / `hand_authored_test`;
scripted compiler verification also uses test-fixture provenance. Only the real
HTTP adapter's completed, validated response can qualify a successful discovery
for live provenance. One successful path is not a claim of production readiness
or complete assignment acceptance; adversarial review remains.

The original planning files and all Phase 1 regression tests are preserved.
`ASSIGNMENT.pdf` is explicitly ignored and excluded from source packaging, along
with other PDFs/private local materials. The Git remote is
`https://github.com/mattmuldoon48/computer-use-automation.git`.
No fabricated model provenance or final submission report is included.
Adversarial review and final submission packaging remain later phases.
