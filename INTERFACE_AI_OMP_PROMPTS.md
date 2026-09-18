# OMP / Codex Prompts — interface.ai UI Capability Runtime

These prompts accompany `INTERFACE_AI_DESIGN.md`. They are implementation instructions, not a report of completed work. Use them in a dedicated project on your personal development machine. Do not place this recruiting project, personal model credentials, or external coding-agent configuration on an employer machine.

## How to use

Place the design file in the intended project folder. Keep a local copy of the employer's brief available for comparison, but exclude the original PDF and recruiting email from public Git by default. Run the initial prompt first. Continue with the phase prompts after reviewing results; do not ask an agent to independently run every phase, spend money, and publish in one unattended pass.

## Initial prompt — inspect and implement Phase 1

```text
You are helping me build a focused take-home submission for interface.ai, called
UI Capability Runtime. Read INTERFACE_AI_DESIGN.md and the original assignment
PDF when available. The employer's assignment is authoritative. This is an
engineering submission, not a general browser-agent platform.

First inspect the current directory, repository status, existing AGENTS.md or
project instructions, relevant files, Python/tool versions, and installed
packages. Preserve unrelated files and existing uncommitted work. Do not change
another repository. Use a dedicated feature branch when that is safe; do not
commit, push, publish a repository, send email, deploy, or alter global machine
configuration without asking.

Do not install packages or browsers, fetch external resources, or incur paid
model calls without my authorization. Missing tools should be reported with the
precise proposed setup action. Do not print secrets or read unrelated credential
files. Treat API access as separate from this coding-agent session.

Implement only Phase 1 after inspection: contracts, deterministic replay core
over a fake surface, policy evaluation, ownership/control state, and sanitized
evidence. Do not build a frontend dashboard or a cloud architecture. Do not
implement the final discovery evidence using a script and label it LLM-driven.

Core architecture:
- Python 3.12, uv, Pydantic, async Playwright for the eventual browser adapter.
- FastAPI/templates only for a future local synthetic banking target.
- One process owns each live browser session and every action passes through it.
- Model discovery, mechanical artifact compilation, and model-free replay are
  separate modules with enforced dependency boundaries.
- Replay cannot import or receive a model provider/client.
- JSON capability files, redacted JSONL events, and in-memory session state.
- No queues, vector store, workflow platform, cloud services, or native desktop
  implementation.

Implement strict tagged contracts for GoalContract, BoundValue, Observation,
ActionProposal, TargetSpec, typed predicates, Step, CapabilityArtifact,
terminal result variants, and intervention/lifecycle state. Keep the provider
schema separate enough to respect a provider's supported JSON Schema subset.
Reject unknown fields and unknown operations. No dynamic code execution, eval,
or unrestricted selector/expression language in persisted artifacts.

BoundValue supports input_ref, secret_ref, local_ref, and approved non-sensitive
literals. Do not parameterize by replacing literal strings in transcripts.
Terminal results distinguish success, expected business_outcome, and failure.
Awaiting a human is a run lifecycle state, not success.

Implement ownership transitions AUTOMATION -> PAUSING -> AWAITING_OPERATOR ->
HUMAN -> VALIDATING_RESUME -> AUTOMATION, with abort available. Use an action
lock/queue and ownership epochs. A stale observation or ownership epoch cannot
execute. No action may bypass the central gate.

Implement replay with explicit preconditions/postconditions, exact target
uniqueness, fixed guard ordering, bounded waits/recovery, and safe retry
classification. A successful click is not proof of business success. Never
blindly retry an action whose side effect might already have happened.

Policy is authored/trusted configuration, not a model's declaration. A
capability may request authority but cannot grant it. Unknown effect is denied
or escalated. All restricted input values and rejected validation values must
be removed from persisted logs and exception projections.

Use clearly labelled hand-authored artifacts only as test fixtures during this
phase. No live evidence, timestamps, provider IDs, test counts, or performance
numbers may be invented. No placeholder screenshot or fabricated result may
be added under final evidence directories.

Prioritize tests for strict schema rejection, unresolved input refs, business
outcome versus crash, bounded recovery, unsafe retries, wrong identity,
ambiguous target, stale epoch, actions during HUMAN ownership, invalid resume,
and privacy canaries. Enforce that replay cannot import discovery/provider
modules.

Before coding, give a short implementation plan and flag conflicts with the
brief or existing repository. Then make the scoped changes and run available
authorized checks. Finish with files changed, exact commands/results, known
unimplemented requirements, and a proposed next phase. Never report a check as
passed if it was not executed successfully.
```

## Phase 2 prompt — actual UI and same-session handoff

```text
Continue only Phase 2 of INTERFACE_AI_DESIGN.md. Inspect the current code and
previous results first. Preserve all existing contracts and policy invariants
unless a specific justified correction is needed. Ask before installs/network,
and do not commit or push.

Build Member Operations Sandbox using synthetic data and server-rendered forms.
The primary task is prepare a savings sub-account review for bound member_id,
product_code, and nickname; do not submit or open an account. Include a workspace
iframe, table-based screens, at least one unlabelled input with a visible nearby
caption, duplicate labels in different scopes, generated DOM IDs, and no test
IDs. Do not make it an API-driven task. Do not expose fixture or app internals to
the runtime.

Build the Playwright surface with fixed observation helpers for visible UI,
frame-aware targets, typed operations, explicit identity/output checks, and
sanitized capture. Resolve semantic or constrained relational locators; never
silently pick the first ambiguous element. No raw Playwright Page object,
arbitrary JavaScript, shell, or unrestricted HTTP tool may reach the runtime
model interface.

Implement context-level request policy and block final submission at both action
and transport boundaries. The normal demo does not need HTTP redirects; reject
them before following. Verify current pinned APIs before using route.fetch with
max_redirects=0. Block service workers and WebSockets, cover frames/popups, and
never log bodies/headers. Document the limits of browser-only isolation.

Use a headed browser and terminal controls for a real minimal operator handoff.
Pause and settle automation before giving HUMAN ownership. The person uses the
same browser context, then requests resume. Capture redacted click/change/
navigation metadata, not raw values or keystrokes. The injected recorder cannot
grant permissions or control the runtime. Do not treat isTrusted as proof of a
human. Revalidate identity and checkpoints before resume; no arbitrary skip.

Use a synthetic expired-session scenario for handoff. A separate test oracle
checks that no account was created, but the runtime must not query that oracle.
Use labelled fixture artifacts for browser integration at this stage; do not
call them live-discovered artifacts.

Run authorized real-browser checks, inspect representative screenshots, and
report which handoff checks are automated versus actually performed by a person.
Do not claim human handoff is demonstrated until a person has used the session.
```

## Phase 3 prompt — genuine discovery and learned artifact

```text
Continue Phase 3 of INTERFACE_AI_DESIGN.md after inspecting Phase 1-2. Implement
a narrow structured model adapter, genuine observe-propose-authorize-act-verify
loop, and deterministic capability compiler. Do not broaden tools or replace
the authoritative executor with a general agent SDK.

The runtime model sees the natural-language goal, typed input refs, current
visible UI observations and approved/synthetic screenshots. It cannot see app
source, fixture data, hidden application globals, test controllers, or a
hard-coded action recipe. Have it choose one typed action at a time. Verify
observation IDs and ownership epochs at execution. Enforce budgets, cancellation,
timeouts, and bounded malformed-output handling. A model finish request must
pass deterministic goal/identity/output checks.

Compile actual successful actions into typed versioned capability JSON. Derive
reusable targets from the actual observed elements, preserve input references in
fills/selectors/checkpoints, and reject unstable or sensitive targets. Do not
use global text substitution. Do not silently prune executed steps without a
validated transformation.

Clearly separate discovered actions, mechanically derived targets, caller
contract, and authored profile error rules. One happy-path run cannot prove
knowledge of error screens it never encountered.

Before a real API request, confirm authorization, supported model ID, credentials
availability without displaying their values, and a bounded run budget. A
missing key or denied spending permission is a blocker for genuine live evidence;
continue only safe local work. Never fabricate model output, IDs, screenshots,
metrics, or discovery logs to make the evidence look complete.

Once authorized, execute at least one genuine successful discovery against the
actual UI, save its provenance and capability, and replay from a fresh browser
with a different synthetic member/product/nickname. Replay must use no LLM.
Instrument actual model calls, enforce import boundaries, and fail tests on any
attempted replay-provider call.

Keep failures as well as successes in development records. Only report actual
commands, actual results, and actual outstanding gaps. Do not declare the
assignment finished merely because the happy path now works.
```

## Phase 4 prompt — adversarial review and fault coverage

```text
Review and harden Phase 4 of INTERFACE_AI_DESIGN.md. Start with a code review of
boundary violations and correctness risks, not new features. Preserve all
previous safety, authorization, and evidence requirements.

Exercise the full acceptance matrix: absent member, business validation, slow
load, known harmless interstitial, permission denial, session expiration, unknown
dialog, ambiguous locator, wrong-member review, attempted final submission,
redirect/frame/popup escape, stale proposal, action during HUMAN control, unsafe
resume, browser crash, artifact tampering, and privacy canaries.

Verify no forbidden request reaches a receiver; a later browser error is not
proof of prevention. Verify no LLM decisions occur during replay. Verify that
runtime code never reads a hidden scenario flag or fixture oracle. Verify that
changing inputs changes the UI invocation and that discovery literals do not
survive in restricted artifact fields.

Review retry semantics: wait/re-observe before repeating; never retry unknown
or irreversible effects automatically. Known recovery handlers must have bounded
counts and re-check identity. Distinguish an HTML modal from a native JS dialog.
Never automatically accept an unknown confirmation.

Review human handoff races: late model responses, in-flight actions, ownership
transfer, cached locators, frame navigation, duplicate resume requests, and a
human completing more than the paused step. Require a valid checkpoint and
preserve honest human-assistance metadata.

Audit privacy across provider observations, artifacts, JSONL, persisted result
files, errors, URLs, screenshots, and archives. No raw traces by default. Mask
before persistence; verify images visually and by known sensitive-region tests.
Fail closed on unsafe capture. No claims that regex or store=False guarantees
full privacy.

Fix the smallest set of core problems, run the actual checks, and report
remaining limitations candidly. Do not add tenant variants, visual matching,
MCP, cloud services, or dashboards in this phase.
```

## Phase 5 prompt — final evidence and concise submission

```text
Complete Phase 5 of INTERFACE_AI_DESIGN.md after core acceptance. Inspect the
actual implementation and test output. Do not treat this design document as
proof that a requirement is implemented.

Prepare a clean reviewer demo and evidence manifest with real code revision,
dirty state/diff hash when applicable, dependency/browser versions, checksums,
fixture seeds, genuine model metadata, actual call counts/timing, final outcome,
and human assistance. Keep checksums outside the bytes they checksum. A checksum
is not an attestation that an experiment happened.

Evidence must include genuine discovery and its artifact, changed-input
unassisted model-free replay, a business outcome, a same-session real human
handoff, and a denied risky action with sanitized failure evidence. Do not invent
results or silently replace live runs with fixture playback. Mark missing live
or human evidence as missing.

Write README.md with exact verified setup and demo commands, offline test mode,
API requirements, mocked components, and clear limits. Keep REPORT.md around
1-3 pages and use exactly these headings:
1. Architecture
2. Artifact schema
3. Determinism & error handling
4. Heterogeneity & multi-tenant
5. Escalation & handoff
6. Safety
7. Cuts

Report only implemented behavior and actual measurements. Separate browser
support from future desktop design, local operator ownership from hardened
multi-user isolation, and synthetic safety tests from production compliance.
Do not publish the employer's original brief or recruiting email by default.

Only if every core gate passes, propose at most two small stretches: the same
base artifact on a second explicit tenant skin, and a fixed repeated-replay
matrix. Do not implement extra stretches without agreement.

Run final checks and a fresh-checkout verification when authorized. Show the
submission readiness checklist, exact failures/blockers, file diff, and proposed
commit contents. Do not commit, push, make the repository public, or email the
submission without my explicit instruction.
```
