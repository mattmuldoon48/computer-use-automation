# UI Capability Runtime

A Python 3.12 runtime that discovers a bounded UI workflow with an explicitly
opted-in model, compiles observed actions into a capability, and replays it
without model calls. The demonstration uses real Chromium and a synthetic,
server-rendered legacy banking UI. It prepares a savings sub-account and stops
at **unsubmitted review**; it does not open an account.

Start with [REPORT.md](REPORT.md) for architecture and cuts, the
[submission manifest](evidence/phase5-submission/manifest.json) for revision and
evidence provenance, and [submission verification](evidence/phase5-submission/verification.json)
for the recorded reviewer runs and checkout checks. These records distinguish
fresh local verification from retained historical evidence. This is a local
submission snapshot, not a claim that the pending changes are published remotely.

## Setup and checks

Run from the repository root. Prerequisites are Python **3.12**, `uv`, cached
locked dependencies/build tools, and the Playwright Chromium browser already
installed in the current user's browser cache. Versions are pinned in
[pyproject.toml](pyproject.toml) and [uv.lock](uv.lock). Playwright **1.58.0** uses
Chromium **145.0.7632.6**, revision **1208**; the browser is not committed.

Cached, offline setup and checks:

```sh
uv sync --locked --offline
uv run --offline pytest -q
uv run --offline mypy src sandbox
uv run --offline ruff check .
uv run --offline ruff format --check .
uv lock --check --offline
uv build --offline
```

A machine without those caches needs separately authorized network setup:
`uv sync --locked` and `uv run --offline playwright install chromium`.
The latter downloads a browser: `uv --offline` only disables package fetching,
not networking by the program it launches. Online installation on a clean
machine is not claimed as verified. Offline verification uses existing caches.
Tests start isolated loopback servers and make no external model requests.
The historical [hardening verification](evidence/phase4-adversarial/verification.json)
recorded **221 passing tests**, Ruff, mypy, lock and build checks; use the
submission verification above for fresh results rather than treating that
historical count as a new run.

## Short model-free reviewer demo

Each `demo` creates its own isolated sandbox on an ephemeral loopback port and
fresh browser context, writes a new run directory, and closes both. No server,
credentials, or model access is needed. Add `--headed` to see Chromium.

```sh
uv run --offline uicap demo --case happy \
  --artifact evidence/phase4-adversarial/revised.capability.json \
  --inputs-file examples/member_b.inputs.json \
  --evidence-dir artifacts/local/reviewer-happy

uv run --offline uicap demo --case known_interstitial \
  --artifact evidence/phase4-adversarial/revised.capability.json \
  --inputs-file examples/member_b.inputs.json \
  --evidence-dir artifacts/local/reviewer-recovery

uv run --offline uicap demo --case member_not_found \
  --artifact evidence/phase4-adversarial/revised.capability.json \
  --evidence-dir artifacts/local/reviewer-not-found

uv run --offline uicap demo --case wrong_member_review \
  --artifact evidence/phase4-adversarial/revised.capability.json \
  --inputs-file examples/member_b.inputs.json \
  --evidence-dir artifacts/local/reviewer-identity-denial
```

Expected exits: happy and known interstitial **0** (verified success), member
not found **3** (declared business outcome), wrong-member review **2** (failure).
Run commands individually if your shell stops on nonzero exits. The not-found
command deliberately omits `--inputs-file`: the CLI supplies an absent synthetic
member only in that case. Passing the existing member-B file would not test
absence. Review success verifies member/product/nickname and `submitted=false`;
the public CLI projection omits those potentially restricted output values.

Final submission denial is not a CLI scenario. Exercise both the action gate and
the guarded browser transport, with independent receiving-server/ledger checks:

```sh
uv run --offline pytest -q \
  tests/integration/test_browser_replay.py::test_submission_denied_at_action_and_transport_boundaries
```

The sandbox has a real **Open account** mutation; the denial is not a no-op.
Tests may access its private oracle counters; replay and discovery cannot.
The submission manifest also links the fresh denial probe and sanitized counts.

For a separately running installation:

```sh
# Terminal 1: ordinary browser access to this synthetic UI is NOT guarded.
uv run --offline uicap sandbox serve --port 8765
# Terminal 2: replay creates its own guarded browser context.
uv run --offline uicap replay --origin http://127.0.0.1:8765 \
  --artifact evidence/phase4-adversarial/revised.capability.json \
  --inputs-file examples/member_b.inputs.json --headed
```

## What the evidence proves

- **Authored fixtures:** [packaged fixture data](src/ui_capability/fixtures)
  carries `test_fixture` / `hand_authored_test` provenance. Local scripted
  planners are deterministic test actors, not LLM discovery.
- **Genuine historical discovery:** the [selected live summary](evidence/phase3-live/fff6f1953ef04b81bfc567ce9c486628/summary.json)
  records actual model `gpt-4.1-2025-04-14`, nine API calls, eight executed actions,
  and success in 16.526 seconds. Its [original capability](evidence/phase3-live/fff6f1953ef04b81bfc567ce9c486628/candidate.capability.json)
  is `live_discovery` / `observed_live`. The [fresh replay summary](evidence/phase3-live/fff6f1953ef04b81bfc567ce9c486628/fresh-replay/a535f375c5244e18bf5198efb0026577/summary.json)
  records a separate browser session, success in 2.104 seconds, and zero provider
  calls, with changed member/product/nickname. These are single-run observations,
  not benchmarks. [Selected runs](evidence/phase3-live/selected-runs.json) also
  retains five unsuccessful development attempts; all six used 29 API calls.
- **Reviewed checkpoint revision:** the reviewer demos use version 2, not a new
  model discovery. [Derivation](evidence/phase4-adversarial/derivation.json)
  connects the unchanged original and revised canonical hashes. It removes 16
  redundant route predicates while preserving all eight observed actions,
  semantic checkpoints, typed inputs, identity/output checks and provenance.
  Policy still checks the actual route before each action. The
  [adversarial index](evidence/phase4-adversarial/selected-runs.json) retains the
  original-artifact failure and revised 11-case matrix.

Historical live and manual runs did not record exact source-revision metadata.
The hardening matrix also lacks exact per-run source diffs; its final source/test
snapshot is recorded separately. Do not retroactively assign those runs to the
submission revision. **No new provider run followed hardening**: the changed
provider/discovery code has local regression coverage, not renewed live evidence.

## Optional authorized discovery

This command can incur charges and send filtered observations to OpenAI. It is
not needed for setup, demos, or tests. Privately set `OPENAI_API_KEY` and select a
currently supported structured-output Responses API model in `OPENAI_MODEL`.
Confirm model availability and authorize cost first; never put credentials in
arguments, evidence, source control, or chat.

```sh
uv run --offline uicap discover \
  --model "$OPENAI_MODEL" --authorize-api \
  --max-calls 12 --max-output-tokens 1200 --call-timeout 30 \
  --evidence-dir artifacts/local/authorized-discovery
```

No provider call occurs without `--authorize-api`. `uv --offline` does **not**
block these API requests. By default discovery uses synthetic member A, then
validates the candidate in a fresh browser using different member, product and
nickname. `--inputs-file` and `--replay-inputs-file` can replace those invocations;
all three replay inputs must differ. `--headed` displays the browsers.
There is no automatic HTTP retry; one malformed-decision repair may consume
another call. These limits are not a dollar-denominated billing cap.

The text-only planner receives approved public UI text, ephemeral observed
references, the caller's goal/type definitions and value-free completion checks,
not raw restricted inputs, screenshots, fixture recipes, application source or
hidden business state. Its strict choice schema is policy-filtered; the session
independently checks every returned proposal. Goal/output contracts and known
error/recovery rules are authored knowledge. Compilation only accepts reviewed
target bindings. Failed, refused or incomplete requests fail closed. Raw prompts,
responses, keys and rich outputs are not persisted. Successful compilation writes
`candidate.capability.json`; only successful fresh replay marks it validated.

## Terminal takeover and same-session resume

The historical [user-guided handoff summary](evidence/phase2-fixtures/manual/54c4cbabdeae477d824e8b2eb5591620/summary.json)
records success, terminal control transfer, `human_intervened=true`, and zero
provider calls. An earlier timed-out attempt remains retained. This was a real
user-guided demonstration, distinct from automated integration-test operators;
the metadata itself proves ownership transfer, not a person's identity or
physical activity. It has not been newly repeated for submission packaging.

To try it yourself with the reviewed artifact:

```sh
uv run --offline uicap demo --case session_expired --headed \
  --artifact evidence/phase4-adversarial/revised.capability.json \
  --inputs-file examples/member_b.inputs.json \
  --evidence-dir artifacts/local/reviewer-handoff
```

1. Wait for **Session expired** in the existing browser and `operator>` in the
   terminal. Do not create a replacement browser/context.
2. Type `takeover`; wait for **HUMAN ownership granted**.
3. In that browser's workspace frame, enter a made-up alias in **Demo operator**
   and click **Demo sign in**. No real credentials are needed.
4. Confirm **Prepare sub-account** is visible without changing the member or
   opening an account. Type `resume` in the terminal.
5. Resume validates the same context, location, identity, saved step and
   checkpoint before returning ownership. Invalid resume leaves HUMAN ownership
   intact; successful continuation ends at unsubmitted review.

`capture` requests a masked capture; `abort` or terminal EOF stops the run.
Operator waiting is bounded to 30 minutes. Keep the process alive: durable
process-crash recovery is unsupported. When an assistant owns the terminal,
request terminal commands through it; the human performs browser reauthentication.
Headless intervention cases abort and are not human-handoff demonstrations.
Discovery-time human resume is not implemented.

## Safety, privacy and scope

Execution goes through one session gate with ownership epochs, fresh observations,
unique semantic targets, bounded retries/recovery, and identity/terminal checks.
Artifacts contain data, not executable selectors/code, and cannot expand trusted
profile/policy authority. Runtime targeting uses visible rendered UI, named
frames, section/role/label/table-caption locators; it does not call business APIs
or read hidden state. Unknown native, HTML and ARIA dialogs stop automation.

Context-wide request guards plus a mandatory loopback HTTP proxy enforce exact
method/origin/path allowlists. Submission, CONNECT, websocket upgrades, redirects,
service workers and unsupported uploads are denied. Response restrictions cannot
prevent the initial receipt of bytes from an otherwise allowed endpoint.
The proxy supports only explicit `http://127.0.0.1:PORT` fixture origins, not HTTPS
tunnels. Ordinary browsers outside the guarded context are not protected.

Current evidence consists of structural events, redacted summaries, installed
artifact/profile/policy snapshots and capture decisions. Rich in-memory outputs
must not be serialized publicly. Current captures mask the **entire page** and
verify every decoded pixel before persistence, or withhold the image. Fully
opaque screenshots deliberately lose visual UI detail; structural checkpoints
carry useful execution evidence. Historical captures retain their original
workspace-only masks and reviewed shell chrome, not the new capture guarantee.
No raw traces, videos, DOM dumps or unredacted fallback images are retained.

This is a cooperative local browser/HTTP boundary, not OS egress isolation,
hostile multi-user isolation, a production banking integration, native desktop
support, or a deployed multi-tenant service. DOM revalidation is not atomic
against hostile page mutation. Caller/profile/schema text must be trusted public
configuration. No production security or privacy attestation is claimed.
Assignment PDFs and private local materials are excluded from source packaging;
original planning documents and historical evidence remain unchanged.
