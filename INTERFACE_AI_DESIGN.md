# UI Capability Runtime — interface.ai Take-Home Design

**Status:** Proposed design and acceptance criteria. This document is not an implementation, a completed REPORT.md, or evidence of a live run. No performance, test, security, or hiring-outcome claims are implied.

**Working repository name:** `ui-capability-runtime`  
**Python package:** `ui_capability`  
**Proposed CLI:** `uicap`  
**Design date:** September 17, 2026

## 1. The submission's central argument

Build a small runtime that separates probabilistic discovery from conservative execution:

> A model discovers a task through the visible application. A compiler turns the observed execution into a typed capability. A model-free interpreter replays that capability, checks its results, and hands the same session to a human when it cannot safely continue.

The original brief—not this plan—is authoritative. In particular, the brief prioritizes system design, actual completion and replay, explicit runtime error handling, real human handoff, extensibility, safety, and communication. It asks for a thin but real implementation of every core requirement, not an elaborate implementation of a subset. [A, §§3, 5, 7]

### Scope

**Core:** One genuine LLM-driven discovery, one deliberately legacy-like local banking UI, parameterized artifact compilation, deterministic replay, typed results, bounded recovery, real same-session human handoff, policy enforcement, sanitized evidence, meaningful tests, and the prescribed README/REPORT/evidence deliverables.

**At most two stretches, after core acceptance:** A small second tenant skin using the same base artifact; repeated replay measurements on a documented fixture matrix. These directly exercise reuse and reliability. Do not add an agent catalog, MCP server, code generation, approval workflow, or LLM recovery as additional stretch projects.

**Do not build:** Cloud deployment, queues, clusters, distributed workers, a vector database, a general workflow platform, autonomous financial transactions, a native desktop driver, arbitrary public-site browsing, or a substantial operator dashboard. A terminal plus the existing headed browser is the operator interface. FastAPI is for the sample application, not a reason to introduce a separate control-plane service.

## 2. The concrete demonstration

### Local target: Member Operations Sandbox

Use a small FastAPI application with server-rendered templates and ordinary browser forms. No business API is exposed to the agent. The application's ordinary form requests still exist; the runtime interacts by operating the UI, not by calling those endpoints directly.

The UI intentionally contains a navigation frame, a workspace iframe, table-oriented screens, an unlabelled input adjacent to visible text, duplicate button labels in different scopes, dynamically generated DOM IDs, and no automation test IDs. Do not make every element inaccessible just to manufacture difficulty. Preserve realistic stable cues such as visible section titles, field captions, table headers, and form destinations.

All people, member identifiers, products, fees, and account records are synthetic. Keep the model's runtime tools separate from the coding agent: the runtime model cannot read the app source, fixture data, filesystem, hidden application state, test-controller endpoints, or databases.

### Primary goal

“Prepare a new savings sub-account for member `{member_id}`, using product `{product_code}` and nickname `{nickname}`. Stop on the review screen. Do not submit or open the account.”

A successful UI path is:

1. Open member search.
2. Enter the bound member identifier.
3. Search and select the correct member.
4. Verify that the detail screen belongs to the requested member.
5. Open the sub-account preparation form.
6. Select the requested product and enter the nickname.
7. Advance to review.
8. Verify the member, product, nickname, and unsubmitted state; extract the fee and currency.

This is the intended demonstration, not a hard-coded runtime recipe. The discovery model must determine the actual steps from the live UI. A task contract may specify input/output requirements and a terminal business condition; it must not secretly supply the click sequence.

### Input and output contracts

Inputs: `member_id` as a validated string, `product_code` as a supported enum, and `nickname` as a bounded string. Do not convert identifiers into integers or lose leading zeroes. Mark member IDs and free-text nicknames as restricted data even though the demo uses synthetic values.

Outputs: matched member identifier, product code, nickname, monthly fee in integer minor units, currency, and submitted state. Return restricted values to the authorized in-process caller when needed; do not copy them into logs. Parse displayed money with an explicit locale and decimal parser before converting to minor units. Never use binary floating-point for that conversion, and never ask the model to manufacture the output value.

Review rendering must not create an account. The separate test harness verifies that the synthetic account ledger is unchanged and the final submission endpoint was not invoked. This oracle is available to tests, not to discovery or replay. The UI also exposes a visible unsubmitted state for runtime checks.

### Fault scenarios

Implement named scenarios in the sandbox test harness: nonexistent member; ineligible product or business validation rejection; transient slowness; known harmless interstitial; permission denial; expired session; unknown dialog; duplicate target; wrong-member review; and attempted final submission. The runtime sees their UI effects, not their scenario labels.

For human handoff, expire the session after a detail screen. The operator reauthenticates using the same headed browser and a clearly labelled synthetic demo sign-in. No real credentials are involved. Preserve the browser context; application cookies may legitimately change during reauthentication.

## 3. Stack and dependency policy

Use Python 3.12, uv, Pydantic, async Playwright/Chromium, FastAPI and templates for the sandbox, and pytest. Ruff and mypy are appropriate quality gates. Use one structured model adapter through the OpenAI Responses API or another explicitly selected provider—not a provider matrix.

Keep the discovery model configurable and record the actual model identifier used. Current OpenAI documentation supports custom UI tools as an integration option; this project deliberately chooses a narrow typed tool interface instead of arbitrary model-generated executable code because the artifact and policy boundary are central. Structured Outputs constrain shape, not semantic correctness. [S4, S5]

Pin dependencies and the browser revision in the actual build. Verify methods against that pin. Recent Playwright documentation includes AI-oriented ARIA snapshots and element references, but those references are observation-local and must never become persisted replay identifiers. Prefer stable public APIs over relying on a brand-new convenience API. [S3]

Do not install dependencies, make paid model requests, or publish anything merely because this document describes doing so. Obtain authorization in the coding workflow. Offline tests must not make paid model calls. Missing credentials are an explicit blocker for live evidence, not a reason to substitute a scripted run and call it real.

## 4. Architecture and boundaries

```text
Request: goal template + typed inputs + target binding
                     |
          Discovery planner (LLM)
                     |
        Typed action proposal + observation ID
                     |
      Shared action gate and session ownership
             /                   \
      Policy engine          Evidence sink
             |
     Browser surface adapter ---- Live sandbox UI
             |
       Verified action records
             |
     Capability compiler + validator
             |
     Versioned immutable capability JSON
             |
   Model-free replay interpreter + fresh inputs
             |
      Same gate / adapter / evidence
             |
    Typed result or same-session intervention
```

Suggested layout; combine small modules rather than creating a framework:

```text
ui-capability-runtime/
  README.md
  REPORT.md
  pyproject.toml
  uv.lock
  .env.example
  src/ui_capability/
    contracts.py
    discovery.py
    compiler.py
    replay.py
    policy.py
    session.py
    evidence.py
    profiles.py
    cli.py
    surfaces/
      base.py
      playwright.py
      locator_compiler.py
      operator_capture.js
    models/
      base.py
      openai.py
  sandbox/
    app.py
    fixtures.py
    templates/
  profiles/
    member_ops.base.json
    tenant_a.json
  examples/
    prepare_subaccount.request.json
    replay_member_b.inputs.json
  tests/
    unit/
    integration/
    e2e/
    live/
  scripts/
    check_evidence.py
  evidence/
    README.md
    manifest.json
    discovery/
    replay/
    handoff/
    failures/
  docs/
    testing.md
```

The replay module must not import the provider SDK, discovery planner, or model adapter. Enforce this dependency rule. Passing a disabled model into replay is weaker than giving replay no model dependency at all.

Use immutable JSON artifacts, redacted JSONL events, and in-memory live-session state. A database is unnecessary for one process and one active operator. The session manager is the sole owner of browser handles and the sole executor of actions. The event sink and the artifact files are not a distributed transaction system.

## 5. Contracts to implement first

Use explicit tagged unions and strict validation with unknown fields rejected. Pydantic supports discriminator-based unions; keep the provider-facing schema sufficiently small for its supported JSON Schema subset and validate again at the application boundary. [S5, S6]

### GoalContract

Fields: goal template, input definitions, output definitions, target binding, terminal business intent, active budgets, and safety policy reference. This declares what success means for the task, not how to click through the application.

For the demo, require equality with the requested member/product/nickname and an unsubmitted review state. A generic natural-language goal cannot be proven correct merely because a model proposes a syntactically valid assertion. The documented narrow contract and the independent test oracle are important limits on that claim.

### BoundValue

Allow a small union: `input_ref`, `secret_ref`, `local_ref`, and approved non-sensitive literal. Resolve actual invocation values only at execution time. The normal example fills must use input references. Never parameterize by globally replacing strings in a completed transcript. A restricted value can appear in URLs, table selectors, error messages, and checkpoints as well as in form fills; all must preserve typed references.

Secrets are opaque references. Resolve only within a controlled action when appropriate; the model, artifact, event log, screenshots, and operator event recorder must not receive their raw value. The synthetic demo reauthentication does not require implementing a real secret manager.

### Observation

Contains an observation ID, run/session identifiers, ownership epoch, page and frame scopes, a filtered view of visible text, current interactive targets, and an optional appropriately redacted screenshot. Each target has an observation-local reference, visible/control metadata, frame context, and grounding evidence.

Use fixed, reviewed observation scripts to inspect rendered UI. Do not expose `evaluate`, shell, arbitrary HTTP, hidden application globals, cookies, storage, or filesystem operations to the model.

### ActionProposal

One action at a time: navigate to a permitted observed destination, click a current target, fill a current target with a BoundValue, select an option, bounded wait, request extraction, propose completion checks, or request intervention. A small explicit action type is preferable to an unrestricted command string.

Carry `observation_id` and ownership epoch. Unsupported actions, stale references, invalid input references, or unknown targets fail validation before execution. Risk and permissions come from trusted policy, not the proposal's self-description.

### CapabilityArtifact

Include schema version; capability ID and version; task/input/output contract; surface requirements; app compatibility; reusable target specifications; ordered actions; preconditions and postconditions; declared business outcomes; bounded recovery rules; extraction definitions; final success checks; required permissions; redaction metadata; and provenance.

Provenance distinguishes: observed in a genuine live discovery; mechanically derived by the compiler; supplied by the caller; and explicitly authored in the application profile. The artifact need not reproduce the model transcript.

Example shape, deliberately abbreviated and not a discovered artifact:

```yaml
schema_version: 1
capability_id: prepare_subaccount_review
capability_version: 1
surface_requirements: [web, frames, visible_text, form_controls]
inputs:
  member_id: {type: string, classification: restricted}
  product_code: {type: enum, values: [SAVINGS_BASIC, SAVINGS_PLUS]}
  nickname: {type: string, classification: restricted, max_length: 32}
steps:
  - id: fill_member_search
    operation: fill
    target_ref: member_search_field
    value: {kind: input_ref, name: member_id}
    preconditions: [member_search_ready]
    postconditions: [member_query_matches_input]
    retry_policy: known_safe_input_overwrite
targets:
  member_search_field:
    frame: {kind: named_frame, name: workspace}
    locator:
      kind: table_label_control
      label: Member number
      control: input
    expected_matches: 1
```

Implement all referenced types and conditions in the real schema. Do not accept free-text pseudo-conditions in the actual artifact. Predicates are a bounded vocabulary such as `visible`, `text_equals`, `value_equals`, `route_matches`, `count_equals`, and conjunctions; no arbitrary Python, JavaScript, unrestricted XPath expressions, or templates that can execute code.

The artifact records required permissions; it cannot grant them. Effective authority is bounded by trusted runtime policy, the application profile, and the caller's scope. Tenant overrides may narrow authority but cannot silently expand it.

### Results and lifecycle

Terminal results are `success` with validated outputs, `business_outcome` with a declared code and typed details, or `failure` with a code and sanitized diagnostic context. A run waiting for a person has lifecycle state `awaiting_operator`; it is not a fourth kind of successful business result.

Metadata includes run ID, artifact/profile hashes, failing step, expected/observed sanitized summaries, retry count, provider call count, and whether a human intervened. Do not put sensitive outputs into the audit projection of the result.

## 6. Genuine discovery and compilation

The discovery planner receives the goal contract, bound-input names, permitted action vocabulary, and the current visible observation. When a sensitive value is needed, it chooses the input reference; the runtime inserts the value. Synthetic screenshots and bounded public UI text provide useful multimodal context.

The loop is observe → propose → validate → authorize → act → verify → record. Use a small step budget and wall-clock budget, cancellation, per-call timeouts, and bounded repair of malformed provider output. Initial configurable defaults could be 20 actions and 180 seconds of active work; these are engineering choices, not performance claims. Operator waiting has a separate bounded deadline.

Record only actual executed actions and verified observations. Rejected proposals are useful audit events but are not successful replay steps. Do not silently delete executed exploration steps unless an explicit, tested transformation proves that doing so preserves the flow. The first implementation can retain harmless detours.

`finish` is only a request to verify completion. The runtime evaluates typed extraction and checkpoint predicates against the current UI and the goal contract. The model cannot set success, bypass an identity check, return an invented fee, or assert only a page title while ignoring required fields.

Compilation should:

- Convert transient references into durable, frame-aware target specifications derived from the actual matched elements.
- Keep BoundValue references, including in selectors and checkpoints.
- Validate all types, targets, references, retry budgets, and terminal checks.
- Attach explicitly authored app-profile error detectors with provenance.
- Reject weak targets, sensitive literals, unknown operations, unsupported surface features, and incomplete output contracts.
- Save a candidate capability only after genuine task completion.

Then replay it in a fresh session with different synthetic inputs. This is a separate validation run, not a continuation of the discovery browser. Keep that replay evidence distinct. A unit-test fixture artifact is useful during development, but is never presented as the final live-discovered artifact.

**Important epistemic boundary:** One successful discovery cannot reveal every possible error screen. Known error detectors come from a small, versioned application profile, clearly labelled as authored and tested. The live model discovers the happy-path actions; it does not magically discover errors that never occurred. Unknown conditions stop or escalate instead of being interpreted by another hidden model call.

## 7. Grounding and locator strategy

Use screenshots and visible/control observations for discovery; use deterministic, currently resolved targets for replay. Playwright supports role/label locators, strict uniqueness, and frame-scoped locators. Its documentation specifically cautions against choosing the first matching element to bypass ambiguity. [S1, S2]

Preferred strategies, in order of evidence strength for a particular target:

1. Exact role and accessible name in a verified frame/section.
2. Associated visible label with a unique control.
3. A constrained relational locator: exact table cell/row caption, appropriate section, then one associated visible control.
4. An explicitly reviewed stable attribute when no stronger cue exists.

Do not persist generated DOM IDs, observation references, absolute click coordinates, ordinal `nth` choices, or long DOM-path selectors as default identity. A screenshot during discovery does not make coordinate replay robust.

The compiler checks that the chosen durable locator resolves back to the actual observed target. On replay, resolve afresh and verify exactly one match plus expected role/control/context. An ambiguous strong locator is a stop condition, not permission to try a looser locator that happens to find one element.

Keep the first implementation to one compiled primary target with a small number of explicitly equivalent alternatives only when necessary. Avoid a large “self-healing locator” subsystem. Every alternative must preserve scope and semantic identity; disagreement is a failure.

For inaccessible native/canvas surfaces, v1 reports unsupported targeting or requests manual intervention. A future adapter can introduce OS accessibility or deterministic visual anchors. Do not label a screenshot-enabled web adapter as desktop support.

## 8. Deterministic replay and error handling

Here “deterministic” means fixed, model-free decision rules for the same artifact, inputs, and observed application conditions. Live backend data and runtime conditions can legitimately change the outcome. Do not promise identical output forever for a changing bank application.

For each step:

1. Check ownership, compatibility, input validity, current policy, and active budgets.
2. Acquire a fresh observation; detect safety and session conditions before acting.
3. Evaluate relevant business-outcome and recovery predicates in a fixed order.
4. Confirm the step's preconditions and unique target.
5. Persist a sanitized action-intent event.
6. Execute through the shared action gate.
7. Verify the postcondition within a bounded wait, watching for runtime exceptions.
8. Record verification or a typed failure; never assume a click succeeded.

Auto-waiting can help with element actionability but does not establish business success. Use explicit review/identity/output checks, not fixed sleeps or `networkidle` as the definition of completion. [S7]

### Failure taxonomy

| Condition | Treatment |
|---|---|
| Malformed invocation inputs | `failure: INVALID_ARGUMENT`, before browser actions |
| Well-formed but nonexistent member | `business_outcome: MEMBER_NOT_FOUND` |
| Ineligible product or known server-side business validation | Declared business outcome with typed details |
| Slow read/load | Bounded wait; retry only a reviewed safe action |
| Known harmless interstitial | Fixed allowlisted dismissal, then re-observe |
| Session expiration | Pause for same-session operator reauthentication |
| Permission denial | Stop; do not turn authorization failure into infinite retry |
| Unknown dialog, ambiguous target, or unsupported UI state | Safe stop plus intervention context |
| Wrong member on details/review | Identity failure; never return a different member's data |
| Attempted irreversible submission | Policy denial; never execute it |
| Mutation may have happened but acknowledgment is missing | `UNKNOWN_ACTION_OUTCOME`; never blindly repeat |

A recovery handler has a fixed maximum count and a recovery action allowlist. It cannot create an unbounded loop. After any recovery, discard stale observations and re-check the identity and relevant checkpoint.

For native JavaScript dialogs, prevent automatic acceptance: detect the dialog, pause, and let a trusted operator or an explicitly reviewed harmless-dialog handler decide. Distinguish these from an HTML modal, which is part of the normal rendered surface.

### Retry safety

Classify the actual operation in the trusted application profile, not by its verb. `click` can be navigation or account creation. Even `fill` can trigger autosave in some applications. In the demo, classify known inert form fills and preview actions deliberately.

Wait before reissuing actions. If the postcondition is already true, verify it rather than clicking again. Unknown-effect or irreversible actions are never automatically retried. A UI-only system generally cannot manufacture exactly-once side effects when the application offers no such contract; this project avoids autonomous commits and documents the unresolved general case.

Keep unsafe-condition detection ahead of success, and reject contradictory authoritative states rather than selecting the convenient one. Use a consistent observation where possible and bounded settling when a page is in a documented transition.

## 9. Real same-session human handoff

This is a required working capability, not a TODO. The brief permits a minimal operator UI but requires real pause, control transfer, same live session, resume, and action capture. [A, §3.6]

Use the headed Chromium window already owned by the run. The operator reads an intervention summary in the terminal, acknowledges takeover, acts in that window, and requests resume in the terminal. No VNC deployment, remote browsing service, or React console is necessary.

### Control model

```text
AUTOMATION → PAUSING → AWAITING_OPERATOR → HUMAN
HUMAN → VALIDATING_RESUME → AUTOMATION
Any nonterminal state → ABORTED
```

The session manager owns an action lock/queue and a monotonically increasing ownership epoch. An action proposal carries the epoch and observation ID. Transfer increments the epoch; stale model responses cannot execute after a human has taken over.

Stop scheduling new actions, settle or explicitly classify the in-flight action, and only then announce control transfer. Cancelling a coroutine does not prove an application action was undone. During human ownership, every automation action is rejected at the central gate. No background browser-action task may bypass that gate.

### Evidence and resumption

Record takeover/resume timestamps, browser-context/session identity, interrupted step, reason, and redacted human UI activity. Install a small fixed recorder in each relevant document/frame to capture click/change/navigation metadata while the trusted controller says the human owns the session. Never log keystrokes or raw field values. Redact in the recorder and again at the event boundary. `isTrusted` is not proof of human identity; automation can also produce trusted browser events.

The page-side recorder can emit observations, not grant ownership, change policy, resume a run, or invoke privileged controller functions. The terminal is the local trust boundary. Document that a local headed-browser arrangement is cooperative single-operator control, not hostile multi-user OS isolation or a complete native-input audit.

Resume only after checking the same browser context, permitted origin/frame, intended member, and a valid recovery checkpoint. Permit either retry of the paused safe step when its precondition holds, or advance past that step only when its postcondition is already verified. Otherwise remain paused or abort. No arbitrary “skip to step 8” control.

An operator intervention is logged as a human segment; it does not silently rewrite the saved capability. Report the run as human-assisted even though it made zero model calls during replay. Keep a separate unassisted replay for the deterministic core demonstration.

On process/browser failure, mark the run interrupted and require a fresh run or explicit reconciliation. Do not claim durable live-session resumption across crashes just because events were written to disk.

## 10. Safety and network boundaries

The model and page content are untrusted. Only the caller, trusted runtime configuration, and the application policy can grant permissions. Official computer-use guidance also emphasizes isolation, site/action restrictions, bounded runs, verifying outcomes, and treating screen content as untrusted. [S4]

### Application action policy

Use exact target origin including port; route/method permissions; allowed action types; known operation effects; and per-run constraints. Unknown effect is denied or escalated. A broad `allowed_actions: [click, fill]` is not enough to prevent a dangerous click on an allowed page.

The final submission action is outside this capability's authority. Block both its UI operation and its backing request. Approval to recover a session cannot broaden that scope. The operator can resolve allowed recovery tasks but cannot submit an account under this read/prepare-only capability.

### Browser transport policy

Use a fresh non-persistent context, no imported personal browsing profile, no real credentials, no downloads or uploads, and no exposed shell or arbitrary JavaScript tools. Apply request policy at context scope, not only on the initial page, and block service workers. Configure WebSocket denial before opening pages. Playwright documents context routing, the service-worker interception caveat, and separate WebSocket routing. [S8]

For the local demo, choose a deliberately small transport policy: reject all HTTP redirect responses and make the sandbox's normal forms render directly without redirects. Check exact normalized scheme/host/port/path/method before forwarding each permitted request. A possible implementation uses `route.fetch(max_redirects=0, max_retries=0)` and refuses redirect responses before fulfilling them; the documented `max_redirects=0` avoids silently following a redirect. Prove behavior with the pinned Playwright version, including popups and redirect chains. [S9]

The transport handler relays the real application's permitted response. It must not give application response bodies or hidden state to the planner/interpreter; UI observations remain the source of business state. No request body/header logging. A forbidden endpoint must receive zero requests even if the browser later reports a navigation error.

Keep test-controller endpoints off the allowed origin or entirely out of HTTP. Treat cancellation of a download event as cleanup, not proof that data was never received. Use outbound-denial tests for direct navigation, redirects, frames, and popups.

These are tested controls for an isolated synthetic demo, not a complete hostile-browser network sandbox. Stronger production isolation requires OS/container egress enforcement, identity controls, and a reviewed deployment environment. Do not add that infrastructure to the take-home merely to make an unearned “bank-grade” claim.

## 11. Privacy and evidence

Have distinct in-memory execution values, provider observations, persisted audit projections, and public evidence exports. Redact before crossing each boundary, not only after writing a log file.

Persist public static UI labels and typed references, not whole screenshots/DOM dumps by default. Remove query values, raw exception inputs, credentials, cookies, tokens, member numbers, and free-text form content. Avoid serializing Pydantic validation exceptions wholesale because they can contain rejected values.

The failure artifact should include a safe structural snapshot and, when permitted, a masked screenshot. For this controlled sandbox, identify sensitive field/record regions explicitly and capture only after masks are applied. If safe capture cannot be established, withhold the screenshot and record a sanitized structural snapshot plus the reason. Regular expressions alone do not establish complete PII detection on arbitrary pages.

Do not enable public raw Playwright traces by default. Traces can include DOM snapshots and network request/response details; masking a separate screenshot does not redact a trace archive. [S10]

Provider observations require separate data minimization. Send only synthetic or approved/redacted visual/text observations in this demo. A client setting such as `store=False` must not be described as a guarantee of provider-wide zero retention. Production use would require appropriate provider/data handling controls; those are not implemented here.

### Evidence manifest

Record actual code revision (and dirty status or code-diff hash), dependency/browser versions, artifact and profile checksums, fixture seed, run type, real model identifier/response identifiers where available, model-call counts, timings, outcome, and human assistance. Never invent response IDs, tests, metrics, or timestamps.

Keep artifact checksums outside the bytes being checksummed to avoid a self-referential hash. A local checksum makes comparisons and provenance clearer; it is not a cryptographic attestation that an experiment happened.

Do not persist hidden model reasoning. Log a short bounded action-purpose/reason code where useful, and sanitize it. Save evidence of what was requested, executed, observed, and verified.

## 12. Heterogeneity and tenant reuse

The surface boundary consists of observe, resolve target, perform typed action, evaluate bounded predicate, capture safe evidence, and handoff hooks. Represent requirements explicitly. Do not expose Playwright `Page`, CSS strings, or browser cookies through the generic runtime interface.

A browser adapter implements frame-aware semantic and relational targets. A future desktop adapter would implement a different target family using OS accessibility or reviewed visual anchors. Not every browser locator can translate to desktop; reject unsupported target families early and document the need for surface-specific bindings.

Separate reusable capability logic from installation binding:

- Base capability: ordered steps, inputs/outputs, invariants, outcome taxonomy.
- Product profile: known error predicates, action effects, compatibility, supported target conventions.
- Tenant binding: origin, entry route, frame labels, locale, and narrowly allowed locator-label overrides.
- Runtime policy: the authority ceiling that none of those files may exceed.

For the optional second skin, change branding, a field caption, generated IDs, and nonessential layout while keeping product semantics consistent. Reuse the base artifact unchanged with an explicit versioned binding. Record the hashes of both. Unknown product versions or incompatible target structure stop; do not call the model silently to “heal” a replay.

Design for compatibility checks and fail-closed invalidation, not a sprawling multi-tenant deployment. Clearly distinguish tested overrides from an unproven claim that one recording works on thousands of installations.

## 13. Acceptance matrix

These are required checks, not claims that they already pass.

| Test | Required observation |
|---|---|
| Real discovery | A live provider call leads to actual UI actions and verified goal completion |
| Artifact provenance | Final capability derives from that run; authored profile rules labelled separately |
| Changed inputs | Fresh-session replay uses different member/product/nickname, not discovery literals |
| Model-free execution | No provider dependency in replay and no provider calls in instrumented execution |
| Typed extraction | Displayed fee parses correctly; malformed/ambiguous values fail |
| Wrong-member trap | Incorrect review identity never returns success |
| Nonexistent member | Declared business result, not generic exception |
| Business validation | Server-side rejection maps to declared business result |
| Slow load | Bounded waiting works without duplicating side effects |
| Known interstitial | Reviewed recovery runs at most its configured limit |
| Permission denial | Stops without blind retries |
| Ambiguous locator | Zero clicks; actionable failure evidence |
| Unknown dialog | No automatic acceptance; intervention requested |
| Final submission trap | UI action denied and endpoint request count remains zero |
| Redirect/iframe/popup escape | Disallowed origin receives zero requests |
| Privacy canaries | Injected sensitive values absent from every persisted text surface; image masks verified |
| Stale proposal | Old observation/ownership epoch rejected |
| Human ownership | No automation action starts while the human owns the session |
| Real human handoff | Person acts in same headed browser; sanitized actions recorded |
| Safe resume | Wrong identity/checkpoint keeps run paused or aborts |
| Browser crash | Interrupted result, no false durable-resume promise |
| Artifact tampering | Invalid operations, widened policy requests, or bad versions are rejected |
| Optional tenant reuse | Same base artifact; explicit binding changes; evidence distinguishes both |
| Optional repeated replay | Fixed matrix/seeds and all attempts retained, including failures |

Use unit tests for schema/policy/compilation/branch rules, real-browser integration tests for locators and transport, and an actual manual pass for human operation. Tests that programmatically exercise the ownership API are not proof that a person took over the real session.

Use a fail-fast test double that raises on any model invocation during replay, alongside import-boundary checks and observed provider traffic/counters. Disabling an environment variable alone does not prove that no model was used.

Validate privacy across artifacts, JSONL, results written to disk, URLs, errors, screenshots, and any archives. Do not use OCR as the primary screenshot-redaction test; assert masking coverage in known synthetic regions and inspect representative exports.

## 14. Implementation phases

### Phase 0 — Inspect and reconcile

Read the actual brief, this plan, the repository status, existing tooling, and installed versions. Preserve unrelated work and use a dedicated branch when appropriate. Report differences between the design and available APIs. Do not install, spend, commit, push, or deploy without authorization.

### Phase 1 — Contracts and model-free core

Implement typed contracts, policy evaluation, replay over a fake surface, event sanitization, and session ownership with the highest-risk unit tests. Fixture artifacts are explicitly test-only. Prove there is no LLM import in replay.

### Phase 2 — Real browser vertical slice

Build the minimal sandbox and browser adapter. Exercise real DOM interactions, frame targeting, binding references, explicit success checks, policy traps, and a first manual handoff. Keep the operator UI as terminal controls. Request approval for dependency/browser setup when needed.

### Phase 3 — Genuine discovery and compilation

Add the narrow structured model adapter and real action loop. Once paid API access is authorized, run a small live discovery early rather than leaving the central requirement until the end. Compile its artifact and replay using fresh inputs. A missing key or unsupported model blocks this acceptance gate, not all local development.

### Phase 4 — Faults, privacy, and handoff hardening

Exercise the acceptance matrix, including same-session reauthentication, stale action rejection, outbound escape tests, wrong-member review, and artifact/log/screenshot leakage checks. Refine the compiler only when evidence reveals a weakness.

### Phase 5 — Evidence and submission polish

Run a clean end-to-end demonstration, preserve all selected outcomes honestly, validate evidence, write the short REPORT from implemented behavior, and test README commands from a fresh checkout. Only then consider the two small stretch goals.

After each phase: show files changed, relevant diff, exact commands run, actual results, known gaps, and a proposed next phase. Do not auto-expand scope because scaffolding is easy to generate.

## 15. Proposed command experience and reviewer demonstration

The following is an interface specification, not a claim that these commands currently exist:

```bash
uv run uicap sandbox serve
uv run uicap discover \
  --request examples/prepare_subaccount.request.json \
  --profile profiles/tenant_a.json \
  --headed \
  --artifact-out artifacts/prepare_subaccount.json

uv run uicap replay \
  --artifact artifacts/prepare_subaccount.json \
  --inputs-file examples/replay_member_b.inputs.json \
  --profile profiles/tenant_a.json \
  --headed

uv run uicap demo --case member_not_found
uv run uicap demo --case session_expired
uv run uicap demo --case forbidden_submit
uv run python scripts/check_evidence.py
```

The `demo` wrapper may configure the sandbox and test fixtures, but must not pass scenario labels or private fixture data to the interpreter. Avoid real sensitive values in shell history; sample input files are synthetic.

A concise reviewer walkthrough:

1. State the goal, run real discovery, and show the actual learned artifact.
2. Inspect input references, target scopes, output checks, and provenance.
3. Replay from a fresh session using a different member and show actual zero model calls.
4. Show member-not-found as a business outcome, not a crash.
5. Trigger session expiration, take control of the same window, reauthenticate, resume, and show the human-action segment.
6. Show a denied final submission with zero server receipt and a sanitized failure artifact.

A short recording is useful but optional in the original brief. Do not claim edits in a recording are a continuous run. Runtime timing/model-call figures come from actual evidence, not terminal decorations.

## 16. REPORT.md and final submission gate

The brief specifies `/README.md`, a roughly 1–3 page `/REPORT.md` with seven headings, and `/evidence/`. Keep this planning document separate from the concise REPORT. [A, §6]

Use exactly these REPORT headings:

1. Architecture
2. Artifact schema
3. Determinism & error handling
4. Heterogeneity & multi-tenant
5. Escalation & handoff
6. Safety
7. Cuts

Write the REPORT after implementation. Describe only what is built, how it was tested, what is mocked, and the actual limits. A module stub is not an implemented surface. A local JSON checksum is not proof of compliance. A few successful replays are not a population-level reliability guarantee.

Final gate: all core requirements represented by working behavior; genuine live discovery evidence present; changed-input model-free replay verified; business outcome demonstrated; real same-session handoff demonstrated; failure evidence sanitized; no secrets; README verified; REPORT truthful and short; optional features not masking core gaps.

The brief requests a public GitHub repository and submission by emailing its URL to `assignments@interface.ai`, with the URL on its own line, from the application address, and no zip. Do not publish the employer's original assignment PDF or email by default. Do not push code or send that email without the user's explicit authorization. [A, §11]

## Sources and scope of attribution

[A] User-provided **Assignment A — Computer-Use Automation System.pdf**, interface.ai, 10 pages. Sections referenced above are the assignment's own numbering. Requirements and deliverables come from this source; architecture, scenario, scope choices, contracts, and acceptance criteria are recommendations in this design.

Technical documentation checked September 17, 2026. Recheck against the actual locked dependency versions when implementing:

[S1] Playwright Python, Locators: https://playwright.dev/python/docs/locators

[S2] Playwright Python, FrameLocator: https://playwright.dev/python/docs/api/class-framelocator

[S3] Playwright Python, Locator / ARIA snapshot: https://playwright.dev/python/docs/api/class-locator#locator-aria-snapshot

[S4] OpenAI, Computer use: https://developers.openai.com/api/docs/guides/tools-computer-use

[S5] OpenAI, Structured model outputs: https://developers.openai.com/api/docs/guides/structured-outputs

[S6] Pydantic, Unions: https://pydantic.dev/docs/validation/latest/concepts/unions/

[S7] Playwright Python, Auto-waiting: https://playwright.dev/python/docs/actionability

[S8] Playwright Python, BrowserContext: https://playwright.dev/python/docs/api/class-browsercontext

[S9] Playwright Python, Route.fetch: https://playwright.dev/python/docs/api/class-route#route-fetch

[S10] Playwright Python, Trace viewer: https://playwright.dev/python/docs/trace-viewer
