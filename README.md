# UI Capability Runtime

Discover a bounded UI workflow with an explicitly authorized model, save its
typed capability, and replay it without model calls. The Python 3.12 demo drives
real Chromium through a synthetic legacy banking UI: prepare a savings
sub-account and stop at **unsubmitted review**, never open the account.

## Shortest reviewer path — no model or credentials

With the pinned environment and Playwright Chromium already installed:

```sh
uv run --no-sync --offline uicap demo --case happy \
  --artifact evidence/final-review/live/5af33fc272cc42e49af6012e6a2b0ed9/candidate.capability.json \
  --inputs-file examples/member_b.inputs.json \
  --evidence-dir artifacts/local/reviewer-happy
```

Expected exit **0**. Add `--headed` to watch the browser. Each demo starts an
isolated loopback sandbox and fresh guarded browser, then closes both. The
artifact above is the newer successful unassisted live-discovery candidate;
this command only replays it, with no model calls. [REPORT.md](REPORT.md) explains
the design and cuts;
[the evidence index](evidence/README.md) selects discovery/artifact/replay,
handoff and error records, distinguishing historical runs, local simulations,
and completed authorized live discovery and real-user handoff evidence.

## Setup and local checks

Requires Python **3.12**, `uv`, and Chromium for Playwright **1.58.0**
(**145.0.7632.6**, revision **1208**). Dependencies are pinned in
[pyproject.toml](pyproject.toml) and [uv.lock](uv.lock).

On a new environment, only after authorizing dependency/browser installation:

```sh
uv sync --locked
uv run --no-sync --offline playwright install chromium
```

The browser command downloads a browser despite `uv --offline`: that flag only
controls package fetching. An existing cached environment can use
`uv sync --locked --offline` when installation is authorized. Neither command
is necessary when the pinned environment and browser already exist.

```sh
uv run --no-sync --offline pytest -q
uv run --no-sync --offline mypy src sandbox
uv run --no-sync --offline ruff check .
uv run --no-sync --offline ruff format --check .
uv lock --check --offline
uv build --offline
```

`uv build` may provision an isolated build environment from cached build tools;
when installation is prohibited, use an already available build backend with
`--no-build-isolation`. [Current local verification](evidence/final-review/verification.json)
records the exact commands/environment actually exercised, including any build
prerequisite or failure. Empty-cache network setup is not claimed verified.
Tests use isolated loopback servers and simulated planners/operators, not paid
model calls or real-human evidence.

## Error, recovery and handoff demos

Use the historical reviewed artifact for these recovery/error examples:

```sh
uv run --no-sync --offline uicap demo --case known_interstitial \
  --artifact evidence/phase4-adversarial/revised.capability.json \
  --inputs-file examples/member_b.inputs.json
uv run --no-sync --offline uicap demo --case member_not_found \
  --artifact evidence/phase4-adversarial/revised.capability.json
uv run --no-sync --offline uicap demo --case wrong_member_review \
  --artifact evidence/phase4-adversarial/revised.capability.json \
  --inputs-file examples/member_b.inputs.json
uv run --no-sync --offline uicap demo --case session_expired --headed \
  --artifact evidence/phase4-adversarial/revised.capability.json \
  --inputs-file examples/member_b.inputs.json
```

Expected exits: recovery **0**, member not found **3**, identity failure **2**.
Run individually if your shell stops on nonzero exits. The not-found case
intentionally omits `--inputs-file`, so the CLI supplies an absent synthetic
member. Final submission denial is tested against both the action gate and
receiving-server/ledger boundary:

```sh
uv run --no-sync --offline pytest -q \
  tests/integration/test_browser_replay.py::test_submission_denied_at_action_and_transport_boundaries
```

### Operating the same live session

When **Session expired** appears and the terminal requests intervention:

1. Type `takeover`; wait for **HUMAN ownership granted**.
2. In the existing browser, enter a made-up alias in **Demo operator** and click
   **Demo sign in**. No credentials are needed. Do not open a replacement context.
3. Leave the restored member and preparation fields unchanged. Type `resume`.
4. The runtime validates the same context, allowed location, exact identity and
   reviewed checkpoint before automation continues. Invalid resume retains HUMAN
   ownership. `capture` saves only a masked capture; `abort` or EOF stops the run.

The same terminal protocol supports discovery. A discovery action already
executed before expiry is retained and settled, not silently dropped or repeated.
Old proposals are discarded and the model receives a fresh observation/epoch.
Human actions are recorded separately and never copied into executable steps.
Assisted candidates declare their restoration dependency; their immutable
provenance remains `candidate_unverified`, while the paired replay summary records
what was actually validated. Assistance outside a representable reviewed
checkpoint cannot produce a reusable capability. Headless intervention aborts;
operator waiting is bounded to 30 minutes per intervention. Keep the process
alive: durable crash recovery is not implemented.

## Supported goal and target interface

The reviewed natural-language goal is:

> Prepare {product_code} for member {member_id} with nickname {nickname}.
> Stop at unsubmitted review; do not open the account.

The supported target is app `member_ops`, version `v1`, entry route `/`, locale
`en_US`, at an explicitly bound loopback installation origin. The reviewed
profile is `member_ops_v1` and policy is `member_ops_prepare`.
[The packaged template](src/ui_capability/fixtures/member_ops.artifact.json)
contains this goal, binding and typed input/output contract; its ordered steps
are discarded before discovery. The library invocation is explicit:

```python
from ui_capability.demo import load_demo
from ui_capability.discovery import Discovery

# origin is the compatible installation; session owns its live guarded surface;
# planner is an explicitly authorized Planner. Keep all three alive across pauses.
bundle = load_demo(origin)
discovery = Discovery(
    template=bundle.artifact,     # goal.goal_template + goal.binding + typed I/O
    profile=bundle.profile,      # reviewed targets, guards and resume checkpoints
    session=session,             # bound to bundle.policy and this exact origin
    planner=planner,
    caller_permissions=frozenset({"prepare"}),
)
outcome = await discovery.run({
    "member_id": "000042", "product_code": "SAVINGS_BASIC", "nickname": "Rainy day",
})
# For an Intervention: retain discovery/session, transfer control with
# await session.takeover(), then call await discovery.resume() after restoration.
```

The CLI supplies these reviewed objects and its own isolated sandbox; it does not
accept arbitrary goal/site flags. Changing prose does not define a new supported
workflow. `Policy._validate_terminal` intentionally still requires the banking
member/product/nickname contract, exact output matches, supported products, typed
fee extraction and `submitted=false`. Unknown tasks, authority or targets are
rejected rather than made to work by weakening those conditions.

Configuration is trusted public text, not interpolated invocation data. Supply
private values only through typed inputs. Projection uses reviewed positions and
`InputRef`s: a private nickname `a` or `Review` does not taint unrelated public
labels/schema keys. Unknown dynamic text/locators remain excluded; scalar approval
alone is not authority to publish private UI contents.
Even direct `DeterministicCompiler` calls must supply an independent
`reviewed_targets` map; the unscoped three-argument constructor is not supported.

## Optional authorized discovery → exact artifact → replay

**This can incur charges. Do not run without explicit API authorization.** Set
`OPENAI_API_KEY` privately and select a supported structured-output Responses API
model in `OPENAI_MODEL`. Never put keys in arguments, logs, screenshots or chat.
`uv --offline` does not block the program's model requests.

```sh
unset discovery_run
if discovery_stdout=$(uv run --no-sync --offline uicap discover \
  --model "$OPENAI_MODEL" --authorize-api --case happy \
  --max-calls 12 --max-output-tokens 1200 --call-timeout 30 \
  --evidence-dir evidence/final-review/live); then
  printf '%s\n' "$discovery_stdout"
  discovery_run=$(printf '%s\n' "$discovery_stdout" | uv run --no-sync --offline python -c '
import sys
prefix = "Redacted discovery evidence: "
paths = [line.removeprefix(prefix) for line in sys.stdin.read().splitlines()
         if line.startswith(prefix)]
if len(paths) != 1 or not paths[0]:
    raise SystemExit("Expected one discovery evidence directory")
print(paths[0])
') &&
  uv run --no-sync --offline uicap demo --case happy \
    --artifact "$discovery_run/candidate.capability.json" \
    --inputs-file examples/member_b.inputs.json \
    --evidence-dir artifacts/local/selected-candidate-replay
else
  printf '%s\n' "$discovery_stdout" >&2
  printf '%s\n' 'Discovery or paired replay failed; do not choose another candidate.' >&2
fi
```

Discovery already replays that exact candidate in a new browser with changed
member, product and nickname. The final command is an additional independent,
model-free replay. Select the unique stdout path, never the newest directory.
Summary records link the paired replay and retain source commit/dirty state,
runtime digest, versions, artifact/profile hashes and actual provider metadata.
Do not rewrite the candidate after verification.

For an authorized **discovery handoff**, run `uicap discover` directly in the
terminal with `--case session_expired --headed` and the same bounds; do not hide
its prompts in shell command substitution. The human must operate that browser,
and may need to restore the fresh replay's separate expired session too. Ask for
participation before calling this real-human evidence. Defaults are 20 actions,
180 active seconds, 12 model calls, 1,200 output tokens per call and 30 seconds per
call; one malformed-output repair consumes the same call allowance. Operator
waiting is separate. These limits are not a dollar-denominated billing cap.

A separately running compatible sandbox can use `uicap sandbox serve --port 8765`
and `uicap replay --origin http://127.0.0.1:8765 --artifact PATH --inputs-file
examples/member_b.inputs.json`. Rebinding changes only the compatible installation
origin, not the goal, app/version or permission ceiling.

## Safety and optional visual walkthrough

Every action passes ownership/epoch, fresh-state, unique-target and policy gates.
A context request guard and mandatory loopback HTTP proxy independently enforce
method/origin/path allowlists. The real **Open account** mutation is blocked.
Replay has no planner dependency. This is not OS egress isolation, hostile-user
isolation, HTTPS tunnelling or a production banking security attestation.

Evidence retains fixed categories, indexes, counts and typed references, not
runtime values, model reasoning, credentials or raw DOM. `failure.snapshot.json`
adds bounded semantic state to entirely opaque, decoded-pixel-verified captures;
it describes the last observed state, not an atomic live-UI attestation. See the
[evidence index](evidence/README.md) for index mappings and historical limitations.
Do not weaken capture defaults for presentation.

For an optional **manual synthetic-only screen recording**, close unrelated
windows/notifications, show only the sandbox browser and terminal, and run the
headed model-free happy demo followed by the headed expiry demo above. Narrate
search → preparation → unsubmitted review, then takeover → synthetic sign-in →
resume. Show the final safe result, not credentials, environment files or raw
outputs. Stop before **Open account**. Inspect the recording for private material
before retaining or sharing it. This optional video is not a substitute for logs,
artifacts or a witnessed human handoff.
