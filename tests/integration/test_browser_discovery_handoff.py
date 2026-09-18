"""Real Chromium, synthetic app, scripted planner and simulated operator; no human claim."""

import asyncio
import json
from io import StringIO

import pytest

from sandbox.app import create_app
from sandbox.server import running_app
from tests.integration.test_browser_discovery import ScriptedPlanner
from tests.integration.test_browser_replay import operator_page
from ui_capability.contracts import ActionProposal, FailureCode, Intervention, Ownership, Success
from ui_capability.demo import load_demo
from ui_capability.discovery import Discovery
from ui_capability.errors import RuntimeFault
from ui_capability.evidence import EvidenceSink
from ui_capability.provider import ModelDecision, OpenAIPlanner
from ui_capability.replay import Replay
from ui_capability.session import Session
from ui_capability.surfaces.playwright import PlaywrightSurface

pytestmark = pytest.mark.browser
FIRST = {"member_id": "000042", "product_code": "SAVINGS_BASIC", "nickname": "PRIVATE_FIRST"}
SECOND = {"member_id": "000099", "product_code": "SAVINGS_PLUS", "nickname": "PRIVATE_SECOND"}


class HandoffPlanner:
    """An explicit fixture wrapping the existing scripted planner, never a model adapter."""

    live = False

    def __init__(self, request_pause):
        self.actor = ScriptedPlanner()
        self.request_pause = request_pause
        self.call_count = 0
        self.requests = []
        self.records = []

    async def propose(self, request):
        self.call_count += 1
        self.requests.append(request)
        if self.request_pause and len(request["prior_action_kinds"]) == 4:
            self.request_pause = False
            observed = request["observation"]
            return ModelDecision(
                kind="intervene",
                observation_id=observed["observation_id"],
                ownership_epoch=observed["ownership_epoch"],
                reason="unsupported_state",
                target_ref=None,
                input_name=None,
                destination=None,
                milliseconds=None,
            )
        return await self.actor.propose(request)


@pytest.mark.parametrize("mode", ["planner", "session_expired"])
def test_same_browser_discovery_restoration_and_changed_input_candidate_replay(
    mode, monkeypatch, tmp_path
):
    app = create_app("session_expired" if mode == "session_expired" else "happy")

    async def scenario(origin):
        bundle = load_demo(origin)
        audit = StringIO()
        sink = EvidenceSink(audit)
        planner = HandoffPlanner(mode == "planner")
        surface = await PlaywrightSurface.launch(
            bundle.artifact.goal.binding, bundle.profile.targets, FIRST, sink, discovery=True
        )
        session = Session(surface, bundle.policy, sink)
        discovery = Discovery(
            bundle.artifact, bundle.profile, session, planner, frozenset({"prepare"})
        )
        try:
            outcome = await discovery.run(dict(FIRST))
            assert isinstance(outcome.result, Intervention), outcome.result
            assert outcome.artifact is None
            assert outcome.result.reason == (
                "session_expired" if mode == "session_expired" else "unsupported_state"
            )
            page = operator_page(surface)
            context, context_id = page.context, surface.context_id
            cookies = await context.cookies()
            old_epoch = planner.requests[-1]["observation"]["ownership_epoch"]
            await session.takeover()
            observed = await session.observe()
            with pytest.raises(RuntimeFault) as denied:
                await session.execute(
                    ActionProposal(
                        observation_id=observed.observation_id,
                        ownership_epoch=observed.ownership_epoch,
                        action=bundle.artifact.steps[0].action,
                    ),
                    bundle.artifact,
                    dict(FIRST),
                    {},
                    frozenset({"prepare"}),
                )
            assert denied.value.code == FailureCode.OWNERSHIP_DENIED
            workspace = page.frame_locator('iframe[name="workspace"]')
            if mode == "session_expired":
                with pytest.raises(RuntimeFault):
                    await discovery.resume()
                assert session.ownership == Ownership.HUMAN
                await workspace.get_by_label("Demo operator", exact=True).fill(
                    "SIMULATED_OPERATOR_CANARY"
                )
                await workspace.get_by_role("button", name="Demo sign in", exact=True).click()
                await workspace.get_by_role(
                    "heading", name="Prepare sub-account", exact=True
                ).wait_for()
                assert await context.cookies() != cookies
                # Valid identity is necessary, and restoring a later workflow state is insufficient.
                member = workspace.get_by_role("cell", name=FIRST["member_id"], exact=True)
                await member.evaluate("cell => cell.textContent = '000099'")
                with pytest.raises(RuntimeFault) as invalid:
                    await discovery.resume()
                assert invalid.value.code == FailureCode.INVALID_RESUME
                await workspace.get_by_role("cell", name="000099", exact=True).evaluate(
                    "(cell, value) => cell.textContent = value", arg=FIRST["member_id"]
                )
                # Form work by the simulated operator cannot become an inferred automated effect.
                await workspace.get_by_label("Nickname", exact=True).fill("OPERATOR_FORM_CANARY")
                with pytest.raises(RuntimeFault):
                    await discovery.resume()
                assert session.ownership == Ownership.HUMAN
                await workspace.get_by_label("Nickname", exact=True).fill("")
                await workspace.get_by_label("Product", exact=True).select_option("SAVINGS_PLUS")
                with pytest.raises(RuntimeFault):
                    await discovery.resume()
                assert session.ownership == Ownership.HUMAN
                await workspace.get_by_label("Product", exact=True).select_option("SAVINGS_BASIC")
            outcome = await discovery.resume()
            assert isinstance(outcome.result, Success), outcome.result
            assert outcome.result.outputs == {
                **FIRST,
                "monthly_fee_minor": 250,
                "currency": "USD",
                "submitted": False,
            }
            assert outcome.result.metadata.human_intervened
            assert outcome.result.metadata.provider_call_count == 0
            assert page.context is context and surface.context_id == context_id
            assert outcome.artifact is not None
            assert outcome.artifact.provenance.kind == "test_fixture"
            assert outcome.artifact.provenance.assistance.replay_status == "candidate_unverified"
            assert len(outcome.artifact.steps) == 8
            assert (
                sum(
                    getattr(step.action, "target", None) == "prepare_link"
                    for step in outcome.artifact.steps
                )
                == 1
            )
            assert planner.requests[-1]["observation"]["ownership_epoch"] > old_epoch
            assert app.state.oracle.request_counts[("GET", "/workspace/prepare")] == 1
            assert app.state.oracle.request_counts[("POST", "/workspace/review")] == 1
            assert app.state.oracle.signin_count == int(mode == "session_expired")
            events = [json.loads(line) for line in audit.getvalue().splitlines()]
            verified = [
                event["action_index"] for event in events if event["event"] == "action_verified"
            ]
            assert verified == list(range(8))
            encoded = (
                outcome.artifact.model_dump_json() + json.dumps(planner.requests) + audit.getvalue()
            )
            for private in (
                FIRST["member_id"],
                FIRST["nickname"],
                "SIMULATED_OPERATOR_CANARY",
                "OPERATOR_FORM_CANARY",
            ):
                assert private not in encoded
        finally:
            await surface.close()

        async def forbidden_provider(*args, **kwargs):
            pytest.fail("Changed-input replay must be model-free")

        monkeypatch.setattr(OpenAIPlanner, "propose", forbidden_provider)
        monkeypatch.setattr(OpenAIPlanner, "_transport", forbidden_provider)
        path = tmp_path / "assisted_candidate.json"
        path.write_text(outcome.artifact.model_dump_json())
        replay_app = create_app()
        with running_app(replay_app) as replay_origin:
            rebound = load_demo(replay_origin, path)
            replay_surface = await PlaywrightSurface.launch(
                rebound.artifact.goal.binding, rebound.profile.targets, SECOND, sink
            )
            try:
                assert replay_surface.context_id != context_id
                replay = Replay(
                    rebound.artifact,
                    rebound.profile,
                    Session(replay_surface, rebound.policy, sink),
                    frozenset({"prepare"}),
                )
                result = await replay.run(dict(SECOND))
                assert isinstance(result, Success), result
                assert result.outputs == {
                    **SECOND,
                    "monthly_fee_minor": 700,
                    "currency": "USD",
                    "submitted": False,
                }
                assert (
                    result.metadata.provider_call_count == 0
                    and not result.metadata.human_intervened
                )
                assert replay_app.state.oracle.signin_count == 0
                assert (
                    replay_app.state.oracle.submit_requests == 0
                    and replay_app.state.oracle.ledger == []
                )
            finally:
                await replay_surface.close()
        assert app.state.oracle.submit_requests == 0 and app.state.oracle.ledger == []

    with running_app(app) as origin:
        asyncio.run(scenario(origin))
