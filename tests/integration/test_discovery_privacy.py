"""Real-browser privacy regressions with explicit scripted-planner test fixtures."""

import asyncio
import json
from io import StringIO

import pytest

from sandbox.app import create_app
from sandbox.server import running_app
from tests.integration.test_browser_discovery import ScriptedPlanner
from ui_capability.contracts import Fill, InputRef, PublicLiteral, Success, walk
from ui_capability.demo import load_demo
from ui_capability.discovery import Discovery
from ui_capability.evidence import EvidenceSink
from ui_capability.provider import OpenAIPlanner
from ui_capability.replay import Replay
from ui_capability.session import Session
from ui_capability.surfaces.playwright import PlaywrightSurface

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("nickname", ["a", "Review"])
def test_private_public_collisions_discover_and_replay_changed_inputs(
    nickname, monkeypatch, tmp_path
):
    first = {"member_id": "000042", "product_code": "SAVINGS_BASIC", "nickname": nickname}
    second = {"member_id": "000099", "product_code": "SAVINGS_PLUS", "nickname": "CHANGED_PRIVATE"}
    embedded = f"PRIVATE_RUNTIME={nickname}"

    async def forbidden(*args, **kwargs):
        pytest.fail("privacy fixture attempted a real provider request")

    monkeypatch.setattr(OpenAIPlanner, "propose", forbidden)

    async def scenario(origin):
        bundle = load_demo(origin)
        audit = StringIO()
        sink = EvidenceSink(audit)
        planner = ScriptedPlanner()
        surface = await PlaywrightSurface.launch(
            bundle.artifact.goal.binding, bundle.profile.targets, first, sink, discovery=True
        )
        first_context = surface.context_id
        try:
            # Test-only DOM mutation: a visible private embedded label and private
            # text are observed but have no reviewed target or field binding.
            page = surface._PlaywrightSurface__page
            frame = page.frame(name="workspace")
            await frame.evaluate(
                """value => {
                    const link = document.createElement('a');
                    link.href = '#';
                    link.textContent = value;
                    document.body.append(link);
                }""",
                embedded,
            )
            trusted = bundle.policy.model_copy(
                update={"approved_literals": (*bundle.policy.approved_literals, embedded)}
            )
            outcome = await Discovery(
                bundle.artifact,
                bundle.profile,
                Session(surface, trusted, sink),
                planner,
                frozenset({"prepare"}),
            ).run(first)
            assert isinstance(outcome.result, Success), outcome.result
            assert outcome.result.outputs == {
                **first,
                "monthly_fee_minor": 250,
                "currency": "USD",
                "submitted": False,
            }
            assert outcome.artifact is not None
            assert outcome.artifact.provenance.kind == "test_fixture"
            assert outcome.result.metadata.provider_call_count == 0
            # No dynamic observed literal is added to the persisted capability.
            initial_literals = [
                node for node in walk(bundle.artifact) if isinstance(node, PublicLiteral)
            ]
            assert all(
                node in initial_literals
                for node in walk(outcome.artifact)
                if isinstance(node, PublicLiteral)
            )
            nickname_actions = [
                step.action
                for step in outcome.artifact.steps
                if isinstance(step.action, Fill) and step.action.target == "nickname_field"
            ]
            assert [action.value for action in nickname_actions] == [InputRef(name="nickname")]
            terminal_targets = planner.requests[-1]["observation"]["targets"]
            nickname_target = next(
                target
                for target in terminal_targets
                if target["descriptor"]["locator"]
                == {"kind": "table_label_value", "label": {"kind": "literal", "value": "Nickname"}}
            )
            assert nickname_target["text"] == {"kind": "input_ref", "name": "nickname"}
            review_target = next(
                target
                for target in planner.requests[-2]["observation"]["targets"]
                if target["descriptor"]
                == bundle.artifact.targets["review_button"].model_dump(mode="json")
            )
            assert review_target["text"] == {"kind": "literal", "value": "Review"}
            persisted = tmp_path / "private_collision_candidate.json"
            persisted.write_text(outcome.artifact.model_dump_json())
            boundary = persisted.read_text() + json.dumps(planner.requests) + audit.getvalue()
            assert embedded not in boundary
            assert first["member_id"] not in boundary
        finally:
            await surface.close()

        monkeypatch.setattr(planner, "propose", forbidden)
        with running_app(create_app()) as replay_origin:
            rebound = load_demo(replay_origin, persisted)
            replay_surface = await PlaywrightSurface.launch(
                rebound.artifact.goal.binding, rebound.profile.targets, second, sink
            )
            try:
                assert replay_surface.context_id != first_context
                result = await Replay(
                    rebound.artifact,
                    rebound.profile,
                    Session(replay_surface, rebound.policy, sink),
                    frozenset({"prepare"}),
                ).run(second)
                assert isinstance(result, Success), result
                assert result.outputs == {
                    **second,
                    "monthly_fee_minor": 700,
                    "currency": "USD",
                    "submitted": False,
                }
                assert result.metadata.provider_call_count == 0
                assert second["member_id"] not in audit.getvalue()
                assert second["nickname"] not in audit.getvalue()
            finally:
                await replay_surface.close()

    with running_app(create_app()) as origin:
        asyncio.run(scenario(origin))
