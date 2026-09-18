"""Bounded UI discovery. The planner proposes; deterministic gates decide and verify."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from .compiler import DeterministicCompiler, goal_template
from .contracts import (
    ActionProposal,
    CapabilityArtifact,
    Click,
    Diagnostic,
    ExecutableAction,
    Failure,
    FailureCode,
    Fill,
    InputRef,
    Intervention,
    Metadata,
    Navigate,
    Observation,
    PublicLiteral,
    Select,
    Success,
    TerminalResult,
    Wait,
)
from .errors import RuntimeFault
from .evidence import contract_hash
from .profiles import Profile
from .provider import ModelDecision, OpenAIPlanner, Planner, ProviderError
from .replay import Replay, _Stop
from .session import Session


@dataclass(frozen=True)
class DiscoveryOutcome:
    result: TerminalResult
    artifact: CapabilityArtifact | None


def live_planner(planner: Planner) -> bool:
    """Only the real HTTP adapter can issue a live-discovery capability."""
    return type(planner) is OpenAIPlanner and planner.live


class _DiscoveryDriver(Replay):
    """One non-resumable discovery invocation; replay retains its human handoff API.

    Shared replay routines supply profile guard precedence, input/authority checks,
    typed predicates and terminal extraction. No ordered template step is retained.
    Recovery/intervention states fail closed rather than omit actions from the trace.
    """

    def __init__(
        self,
        template: CapabilityArtifact,
        profile: Profile,
        session: Session,
        planner: Planner,
        caller_permissions: frozenset[str],
    ) -> None:
        super().__init__(goal_template(template), profile, session, caller_permissions)
        self.planner = planner
        self._compiler: DeterministicCompiler | None = None
        self._repair_used = False
        bound = self.artifact.goal.budgets.max_actions + 2
        configured = getattr(planner, "max_calls", bound)
        self._max_calls = (
            min(bound, configured) if type(configured) is int and configured > 0 else bound
        )

    def _metadata(self) -> Metadata:
        base = super()._metadata()
        return base.model_copy(
            update={
                "provider_call_count": self.planner.call_count if live_planner(self.planner) else 0,
                "step_index": self._index,
            }
        )

    def _preflight(self, supplied: object) -> None:
        try:
            self.artifact = goal_template(self.artifact)
            self.profile = Profile.model_validate_json(self.profile.model_dump_json())
        except (ValidationError, ValueError, TypeError):
            raise RuntimeFault(FailureCode.INVALID_ARTIFACT) from None
        if type(self.planner.call_count) is not int or self.planner.call_count != 0:
            raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        self._validate_invocation(supplied)
        self._compiler = DeterministicCompiler(self.artifact, self.session.policy, self._inputs)

    async def _pause(
        self, reason: Literal["session_expired", "unknown_dialog", "unsupported_state"]
    ) -> None:
        # Discovery has no safe resume protocol. Never inherit Replay.resume by accident.
        raise RuntimeFault(FailureCode.INTERRUPTED)

    async def resume(self) -> TerminalResult:
        raise RuntimeFault(FailureCode.INVALID_RESUME)

    async def _observe(self, *, allow_recovery: bool = False) -> Observation:
        return await super()._observe(allow_recovery=False)

    def _permitted(self, action: ExecutableAction, observation: Observation) -> bool:
        try:
            self.session.policy.authorize(
                action,
                self.artifact.targets.get(getattr(action, "target", "")),
                observation.origin,
                observation.route,
                self.artifact.required_permissions,
                self.permissions,
            )
            return True
        except RuntimeFault:
            return False

    def _request(self, observation: Observation) -> dict[str, object]:
        assert self._compiler is not None
        compiler = self._compiler
        targets: list[dict[str, object]] = []
        allowed_kinds = {"intervene"}
        input_name = next(iter(self._inputs), None)
        for target in observation.targets:
            if not target.derived or not target.visible:
                continue
            try:
                name = compiler.target_name(target, observation)
            except RuntimeFault as fault:
                if fault.code == FailureCode.AMBIGUOUS_TARGET:
                    raise
                continue
            candidates: list[ExecutableAction] = []
            if target.control in {"button", "link"}:
                candidates.append(Click(target=name))
            elif target.control in {"input", "textarea"} and input_name is not None:
                candidates.append(Fill(target=name, value=InputRef(name=input_name)))
            elif target.control == "select" and input_name is not None:
                candidates.append(Select(target=name, value=InputRef(name=input_name)))
            allowed_actions = [
                action.kind for action in candidates if self._permitted(action, observation)
            ]
            allowed_kinds.update(allowed_actions)
            descriptor: dict[str, object] = {
                "ref": target.ref,
                "descriptor": self.artifact.targets[name].model_dump(mode="json"),
                "allowed_actions": allowed_actions,
            }
            for field in ("text", "value"):
                value = compiler._bound_value(getattr(target, field))
                if value is not None:
                    descriptor[field] = value.model_dump(mode="json")
            targets.append(descriptor)
        if len(targets) > 128:
            raise RuntimeFault(FailureCode.BUDGET_EXCEEDED)
        visible_text = []
        for text in observation.visible_text[:128]:
            value = compiler._bound_value(text)
            if value is not None:
                visible_text.append(value.model_dump(mode="json"))
        definitions = {
            name: {
                "type": definition.type,
                "classification": definition.classification,
                "min_length": definition.min_length,
                "max_length": definition.max_length,
                "values": [
                    value
                    for value in definition.values
                    if definition.classification == "public" and not compiler._sensitive(value)
                ],
            }
            for name, definition in self.artifact.goal.inputs.items()
        }
        compiler._route(observation)
        previous_diagnostic = self._diagnostic
        try:
            completion_checks = {
                "identity": self._verify(self.artifact.identity_checks, observation, "identity"),
                "terminal": self._verify(
                    self.profile.terminal_checks + self.artifact.final_checks,
                    observation,
                    "terminal",
                ),
            }
        finally:
            # Unmet completion probes are normal during discovery, not a later
            # provider failure's cause. Raised faults carry their own diagnostic.
            self._diagnostic = previous_diagnostic
        if all(completion_checks.values()):
            allowed_kinds.add("finish")
        destinations = [
            destination
            for destination in observation.destinations[:128]
            if compiler.public(destination)
            and not compiler._sensitive(destination)
            and self._permitted(Navigate(destination=PublicLiteral(value=destination)), observation)
        ]
        if destinations:
            allowed_kinds.add("navigate")
        if self._permitted(Wait(milliseconds=1), observation):
            allowed_kinds.add("wait")
        request: dict[str, object] = {
            "goal_template": self.artifact.goal.goal_template,
            "input_definitions": definitions,
            "completion_checks": completion_checks,
            "allowed_action_kinds": sorted(allowed_kinds),
            "observation": {
                "observation_id": observation.observation_id,
                "ownership_epoch": observation.ownership_epoch,
                "route": observation.route,
                "targets": targets,
                "visible_text": visible_text,
                "destinations": destinations,
            },
            "bounds": {
                "remaining_actions": self.artifact.goal.budgets.max_actions
                - self.session.action_count,
                "remaining_provider_calls": self._max_calls - self.planner.call_count,
            },
            "prior_action_kinds": [step.action.kind for step in compiler.steps],
            "completed_input_bindings": sorted(
                {
                    step.action.value.name
                    for step in compiler.steps
                    if isinstance(step.action, (Fill, Select))
                    and isinstance(step.action.value, InputRef)
                }
            ),
        }
        encoded = json.dumps(request, ensure_ascii=False)
        # Defense in depth for caller-authored goal text, frame names and enum definitions.
        # Never repair a leak with global string substitution: reject it before transport.
        if compiler._sensitive(encoded):
            raise RuntimeFault(
                FailureCode.POLICY_DENIED,
                Diagnostic(stage="discovery", expected="valid_contract", observed="denied"),
            )
        if len(encoded.encode("utf-8")) > 64_000:
            raise RuntimeFault(
                FailureCode.BUDGET_EXCEEDED,
                Diagnostic(
                    stage="discovery", expected="within_budget", observed="budget_exhausted"
                ),
            )
        return request

    async def _propose(self, observation: Observation) -> ModelDecision:
        request = self._request(observation)
        while True:
            count = self.planner.call_count
            if count >= self._max_calls:
                raise RuntimeFault(
                    FailureCode.BUDGET_EXCEEDED,
                    Diagnostic(
                        stage="discovery", expected="within_budget", observed="budget_exhausted"
                    ),
                )
            try:
                decision = await self.planner.propose(request)
                decision = ModelDecision.model_validate_json(decision.model_dump_json())
            except (ProviderError, ValidationError) as error:
                if isinstance(error, ProviderError) and error.code == "budget_exceeded":
                    raise RuntimeFault(
                        FailureCode.BUDGET_EXCEEDED,
                        Diagnostic(
                            stage="discovery", expected="within_budget", observed="budget_exhausted"
                        ),
                    ) from None
                malformed = isinstance(error, ValidationError) or error.code == "malformed_output"
                if not malformed or self._repair_used:
                    raise RuntimeFault(
                        FailureCode.INTERRUPTED,
                        Diagnostic(
                            stage="discovery",
                            expected="valid_decision",
                            observed="invalid_format" if malformed else "unknown",
                        ),
                    ) from None
                self._repair_used = True
                request = self._request(observation)
                request["repair"] = (
                    "Return one valid structured decision; use null for irrelevant fields."
                )
                continue
            if self.planner.call_count != count + 1:
                raise RuntimeFault(
                    FailureCode.INTERRUPTED,
                    Diagnostic(stage="discovery", expected="valid_decision", observed="mismatch"),
                )
            if (
                decision.observation_id != observation.observation_id
                or decision.ownership_epoch != observation.ownership_epoch
                or decision.ownership_epoch != self.session.epoch
            ):
                raise RuntimeFault(
                    FailureCode.STALE_OBSERVATION,
                    Diagnostic(stage="discovery", expected="fresh_observation", observed="stale"),
                )
            return decision

    def _action(self, decision: ModelDecision, observation: Observation) -> ExecutableAction:
        assert self._compiler is not None
        if decision.kind in {"click", "fill", "select"}:
            found = [target for target in observation.targets if target.ref == decision.target_ref]
            if len(found) != 1:
                raise RuntimeFault(
                    FailureCode.AMBIGUOUS_TARGET if found else FailureCode.TARGET_NOT_FOUND
                )
            name = self._compiler.target_name(found[0], observation)
            if decision.kind == "click":
                return Click(target=name)
            if decision.input_name not in self._inputs:
                raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
            value = InputRef(name=decision.input_name)
            return (
                Fill(target=name, value=value)
                if decision.kind == "fill"
                else Select(target=name, value=value)
            )
        if decision.kind == "navigate":
            if decision.destination not in observation.destinations:
                raise RuntimeFault(FailureCode.POLICY_DENIED)
            assert decision.destination is not None
            return Navigate(destination=PublicLiteral(value=decision.destination))
        if decision.kind == "wait":
            assert decision.milliseconds is not None
            return Wait(milliseconds=decision.milliseconds)
        raise RuntimeFault(FailureCode.INTERRUPTED)

    def _completed_live(self) -> bool:
        if not live_planner(self.planner) or self.planner.call_count <= 0:
            return False
        if not self.planner.records:
            return False
        last = self.planner.records[-1]
        return (
            last.get("status") == "completed"
            and last.get("decision_validated") is True
            and isinstance(last.get("response_id"), str)
            and bool(last["response_id"])
        )

    async def discover(self, inputs: object) -> DiscoveryOutcome:
        async with self._run_lock:
            if self._started:
                raise RuntimeFault(FailureCode.OWNERSHIP_DENIED)
            self._started = True
            candidate: CapabilityArtifact | None = None
            try:
                async with asyncio.timeout(self.artifact.goal.budgets.active_seconds):
                    self._preflight(inputs)
                    assert self._compiler is not None
                    observed = await self._observe()
                    if not self._verify(self.artifact.preconditions, observed, "initial"):
                        raise RuntimeFault(FailureCode.PRECONDITION_FAILED)
                    while True:
                        decision = await self._propose(observed)
                        if decision.kind == "intervene":
                            raise RuntimeFault(
                                FailureCode.INTERRUPTED,
                                Diagnostic(
                                    stage="discovery",
                                    expected="known_state",
                                    observed="interrupted",
                                ),
                            )
                        if decision.kind == "finish":
                            # A finish proposal is only a request for independent verification.
                            observed = await self._observe()
                            if not self._verify(
                                self.artifact.identity_checks, observed, "identity"
                            ):
                                raise RuntimeFault(FailureCode.IDENTITY_MISMATCH)
                            if not self._verify(
                                self.profile.terminal_checks + self.artifact.final_checks,
                                observed,
                                "terminal",
                            ):
                                raise RuntimeFault(FailureCode.POSTCONDITION_FAILED)
                            extracted = self._extract(observed)
                            candidate = self._compiler.finish(
                                live=self._completed_live(), run_id=self.session.run_id
                            )
                            self.artifact = candidate
                            self._artifact_hash = contract_hash(candidate)
                            result: TerminalResult = Success(
                                outputs=extracted.outputs, metadata=self._metadata()
                            )
                            break
                        if self.session.action_count >= self.artifact.goal.budgets.max_actions:
                            raise RuntimeFault(FailureCode.BUDGET_EXCEEDED)
                        action = self._action(decision, observed)
                        envelope = self._compiler.proposal_artifact(action, observed)
                        await self.session.execute(
                            ActionProposal(
                                observation_id=observed.observation_id,
                                ownership_epoch=observed.ownership_epoch,
                                action=action,
                            ),
                            envelope,
                            self._inputs,
                            self._locals,
                            self.permissions,
                            purpose="discovery",
                            step_index=self._index,
                        )
                        after = await self._observe()
                        self._compiler.record(action, observed, after)
                        self._index += 1
                        self.session.evidence.emit(
                            "action_verified",
                            action_index=self.session.action_count - 1,
                            step_index=self._index - 1,
                        )
                        observed = after
            except _Stop as stop:
                result = (
                    self._failure(FailureCode.INTERRUPTED)
                    if isinstance(stop.result, Intervention)
                    else stop.result
                )
            except RuntimeFault as fault:
                result = self._failure(fault.code, fault.diagnostic)
            except TimeoutError:
                result = self._failure(FailureCode.BUDGET_EXCEEDED)
            except asyncio.CancelledError:
                await self._abort_safely()
                self._record(self._failure(FailureCode.INTERRUPTED))
                raise
            except Exception:
                result = self._failure(FailureCode.INTERRUPTED)
            if isinstance(result, Failure):
                candidate = None
                await self._abort_safely()
            self._record(result)
            return DiscoveryOutcome(result=result, artifact=candidate)


class Discovery:
    """Public discovery interface, deliberately without replay's resume contract."""

    def __init__(
        self,
        template: CapabilityArtifact,
        profile: Profile,
        session: Session,
        planner: Planner,
        caller_permissions: frozenset[str],
    ) -> None:
        self._driver = _DiscoveryDriver(template, profile, session, planner, caller_permissions)

    async def run(self, inputs: object) -> DiscoveryOutcome:
        return await self._driver.discover(inputs)
