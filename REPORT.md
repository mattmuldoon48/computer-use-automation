## Architecture

UI Capability Runtime separates model-assisted discovery from deterministic,
model-free execution. Python 3.12, strict Pydantic contracts and Playwright drive
real Chromium against a synthetic legacy banking application. The implemented
goal prepares a savings sub-account and stops at **unsubmitted review**; it does
not open an account. [README](README.md) provides the shortest demo and exact
commands; the [evidence index](evidence/README.md) distinguishes current local
verification, historical genuine runs, simulations and outstanding live evidence.

`Discovery(template, profile, session, planner, caller_permissions).run(inputs)`
accepts a reviewed natural-language goal template, app/version/origin/entry binding,
typed I/O, trusted profile and policy-bound session. The CLI supplies the banking
configuration. Ordered template steps are discarded: the planner observes the
live UI and chooses actions rather than following the authored recipe. Contracts,
target conventions and error detectors remain reviewed application knowledge.
`compiler.py` records verified automated actions; `replay.py` consumes only the
resulting data and never imports a planner/provider. Both use `session.py` for
ownership, freshness, authorization and execution.

This is an explicit bounded interface, not arbitrary-site compilation. Shared
`Policy._validate_terminal` still enforces banking fields, supported products,
exact output matches, fee typing and unsubmitted status. A different prose goal
is not a supported new workflow. Sandbox business-state oracles belong only to
external tests/probes, never the discovery/replay decision loop.

## Artifact schema

[`contracts.py`](src/ui_capability/contracts.py) defines a versioned, strict,
extra-field-rejecting data language, not executable browser code. A capability
contains its identifier/version, public goal template, classified typed inputs
and outputs, application/version/origin/entry binding, budgets, surface
requirements, semantic targets, ordered actions, pre/postconditions, identity and
terminal predicates, extractions, output matches, declared business outcomes,
recovery references, permissions, redaction declarations and provenance.

Targets combine a main/named frame, optional section and role, label or table
caption locator, requiring exactly one match. Values are input, secret, local or
public-literal references. Leading-zero identifiers stay strings; money is parsed
into integer minor units. Secret/unresolved references fail closed. The banking
workflow takes `member_id`, `product_code`, `nickname`; outputs also include fee,
currency and `submitted=false`.

Fixture planners remain `test_fixture` / `hand_authored_test`; only the actual
provider adapter can issue `live_discovery` / `observed_live` provenance.
Human-assisted candidates additionally declare reviewed restoration checkpoint
IDs and a `same_session_operator_restoration` dependency. Their immutable
provenance remains `candidate_unverified`; an external summary identifies the
exact artifact hash and successful or failed changed-input replay. Absence of new
optional assistance fields preserves historical canonical hashes. Human actions
are never emitted as executable steps, and an unrepresentable assistance
dependency prevents reusable artifact export.

## Determinism & error handling

Replay follows saved actions and predicates rather than asking a model to
reinterpret screens. One executor lock, ownership epochs, observation freshness,
exact target resolution and route authorization gate each action. Trusted policy
and profile remain the authority ceiling. Policy/identity/contradictory-state
checks prevent a plausible-looking review for the wrong member from becoming
success. CLI exits distinguish verified success (0), declared business outcome
(3), and failure/aborted intervention (2).

Settling, reviewed recovery and safe-effect retries are bounded; cancellation is
not rollback, and unsettled effects retain their gate. Assisted artifacts must
reference compatible trusted restoration checkpoints; pending-action checkpoint
contracts must match their saved action and conditions. Unsupported state never
licenses silent model “healing” or repetition of an uncertain action.

Failures retain stage, step index, expected/observed categories, counts and
predicate positions without raw exceptions or values. Action logs identify kind,
purpose, effect and numeric action/target/guard indexes. Terminal extraction
separately diagnoses a missing fee row and invalid money formatting. A bounded
semantic snapshot supplies richer failure context without readable screenshots.

## Heterogeneity & multi-tenant

The implemented surface exercises named frames, generated IDs, table-labelled
controls, scoped duplicate labels, slowness and expiry. Semantic scopes avoid
saved-coordinate and generated-ID dependencies. Each demo isolates its app and
browser context; rebinding preserves app/version/entry compatibility. This is
one reviewed web application and locale (`en_US`), not demonstrated cross-tenant
portability.

**Future design, not implemented:** keep observation, target resolution, typed
action, bounded predicate evaluation, safe capture and handoff behind the surface
adapter. Control flow must not depend on Playwright `Page`, CSS or cookies. A
desktop adapter needs a distinct accessibility/visual-anchor target family and
explicit capability checks, not pretend translation of browser locators.

Separate a base capability (steps, typed I/O, invariants, outcomes), product
profile (errors, effects, version compatibility and conventions), versioned tenant
binding (origin, entry, frames, locale and reviewed label overrides), and runtime
policy as the non-expandable authority ceiling. Hash/install these separately;
check versions and required structure before execution and at checkpoints.
Unknown versions or missing/ambiguous targets require reviewed rebinding and
revalidation. See the original [design seam](INTERFACE_AI_DESIGN.md#12-heterogeneity-and-tenant-reuse).

## Escalation & handoff

Replay and discovery can return a typed intervention identifying session, epoch,
step/checkpoint and reason. Approved goal/profile snapshots and value-free state
provide operator context. The terminal's `takeover` transfers the same live
browser through AUTOMATION → PAUSING → AWAITING_OPERATOR → HUMAN; `resume`
validates context, allowed location, identity and checkpoint before restoring
AUTOMATION. Invalid restoration retains HUMAN ownership. No automation executes
while HUMAN owns the session. `capture`, `abort`, EOF, cancellation and bounded
operator deadlines have explicit handling; the process must remain alive.

Discovery uses a separate reviewed checkpoint model, not `Replay.resume` against
its empty template. A planner-requested pause requires restoration of the saved
state. The supported expiry path pins the already-executed preparation action,
its pre-observation and actual action index. Synthetic reauthentication must
restore the reviewed pristine preparation form; manual member/form changes are
rejected. The pending action is compiled exactly once with reviewed predicates,
not inferred arbitrary human work. Stale proposals are discarded; continuation
re-observes with a fresh epoch before querying the planner. Active-time accounting
continues across pauses; operator waiting has its own bound.

The authored-initial fallback can continue a single invocation after exact state
restoration but withholds reusable export without a reviewed profile dependency.
Other unsupported blocked states may stop rather than synthesize a checkpoint.
Human-assisted evidence is not labelled autonomous. Local scripted operators are
explicit simulations; historical human evidence is not a newly witnessed handoff.
A new real-human claim requires the user's participation.

## Safety

Prepare-only policy blocks the real **Open account** mutation. Context-wide
request guards and a mandatory loopback HTTP proxy independently enforce exact
method/origin/path allowlists. External probes check receiving-server and ledger
counters. Redirects, CONNECT, websocket upgrades, service workers and unsupported
uploads are denied. Response checks cannot undo receipt at an allowed endpoint;
ordinary unguarded browsers and OS-level egress are outside this boundary.

Configuration is trusted public data; runtime private values enter only through
typed inputs. Projection follows reviewed target/field provenance, not a substring
scan of JSON. Public labels may coincide with nicknames such as `a` or `Review`;
private field values remain input references, including distinct slots with equal
values. Unknown dynamic text and unreviewed locators are excluded. Scalar approval
or a caller-added literal predicate does not declassify private UI contents.
Do not interpolate invocation values into authored goal/policy/profile text.
Direct compiler calls also require an independent reviewed-target map; final
publication review removed the optional authority fallback.

Persisted diagnostics use fixed categories, indexes, counts and flags. Captures
remain fully opaque with decoded-pixel verification or are withheld; semantic
snapshots describe the last observed state, not an atomic live-UI attestation.
No raw prompts, keys, traces or rich output values enter public evidence. Optional
manual screen recording uses only unmistakably synthetic data and does not
weaken automatic redaction. This is not a production security/privacy attestation.

## Cuts

One bounded, prepare-only workflow; no arbitrary-site compiler, irreversible
submission, model-driven recovery, durable workflow service, desktop implementation,
tenant platform or operator dashboard. These avoid expanding authority and
infrastructure beyond the demonstrated core.

Next priorities: exercise the base/profile/binding seam on a second synthetic
product skin with deliberate drift; extend reviewed restoration coverage only
where observed errors justify it; prototype one desktop accessibility family
before claiming broader surface support.

Historical evidence remains unchanged, including its source-revision limitations.
New summaries record source commit/dirty state and runtime digest. The focused
closing pass's local outcomes and any resolved intermediate failures are indexed
separately. A subsequently authorized live discovery compiled eight actions in
nine provider calls and passed exact-candidate changed-input replay with zero
replay model calls; the evidence index selects its immutable receipts. This run
was unassisted. After preserving an interrupted attempt, a separately authorized
fresh run completed real-user discovery handoffs and exact-candidate replay:
ten discovery calls, eight actions, zero replay model calls. Its operator
restoration dependency remains explicit; it is not labelled autonomous.
