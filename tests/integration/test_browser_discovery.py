"""Scripted planner integration coverage; never evidence of genuine LLM discovery."""

import asyncio
import json
from io import StringIO

import pytest

from sandbox.app import create_app
from sandbox.server import running_app
from ui_capability.contracts import Success
from ui_capability.demo import load_demo
from ui_capability.discovery import Discovery
from ui_capability.evidence import EvidenceSink
from ui_capability.provider import ModelDecision, OpenAIPlanner
from ui_capability.replay import Replay
from ui_capability.session import Session
from ui_capability.surfaces.playwright import PlaywrightSurface

pytestmark = pytest.mark.browser


class ScriptedPlanner:
    """An explicitly authored test actor, choosing only from current observed refs."""

    live = False
    call_count = 0

    def __init__(self):
        self.records = []
        self.requests = []
        self.script = [
            ("click", "Member search", None),
            ("fill", "Member number", "member_id"),
            ("click", "Search", None),
            ("click", "member_id", None),
            ("click", "Prepare sub-account", None),
            ("select", "Product", "product_code"),
            ("fill", "Nickname", "nickname"),
            ("click", "Review", None),
        ]

    async def propose(self, request):
        self.call_count += 1
        index = len(self.requests)
        self.requests.append(request)
        # A filled preparation form is not the terminal review screen.
        assert request["completion_checks"]["terminal"] is (index == len(self.script))
        observed = request["observation"]
        fields = {
            "observation_id": observed["observation_id"],
            "ownership_epoch": observed["ownership_epoch"],
            "kind": "finish",
            "target_ref": None,
            "input_name": None,
            "destination": None,
            "milliseconds": None,
            "reason": None,
        }
        if index < len(self.script):
            kind, name, input_name = self.script[index]
            found = []
            for target in observed["targets"]:
                descriptor = target["descriptor"]
                locator = descriptor["locator"]
                label = locator.get("name", locator.get("label"))
                expected = (
                    {"kind": "input_ref", "name": name}
                    if name == "member_id"
                    else {"kind": "literal", "value": name}
                )
                if label != expected:
                    continue
                if kind not in target["allowed_actions"]:
                    continue
                if name == "Review" and descriptor["section"] != {
                    "kind": "literal",
                    "value": "Account preparation",
                }:
                    continue
                found.append(target)
            assert len(found) == 1, (index, found, observed)
            assert kind in found[0]["allowed_actions"]
            fields.update(kind=kind, target_ref=found[0]["ref"], input_name=input_name)
        assert fields["kind"] in request["allowed_action_kinds"]
        assert "navigate" not in request["allowed_action_kinds"]
        return ModelDecision.model_validate(fields)


@pytest.mark.parametrize("replay_case", ["happy", "known_interstitial"])
def test_compiled_observed_flow_replays_new_inputs_without_provider(
    monkeypatch, tmp_path, replay_case
):
    app = create_app()
    first = {"member_id": "000042", "product_code": "SAVINGS_BASIC", "nickname": "PRIVATE_FIRST"}
    second = {"member_id": "000099", "product_code": "SAVINGS_PLUS", "nickname": "PRIVATE_SECOND"}

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
            outcome = await Discovery(
                bundle.artifact,
                bundle.profile,
                Session(surface, bundle.policy, sink),
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
            encoded = (
                outcome.artifact.model_dump_json() + json.dumps(planner.requests) + audit.getvalue()
            )
            for private in (first["member_id"], first["nickname"]):
                assert private not in encoded
        finally:
            await surface.close()

        async def forbidden(*args, **kwargs):
            pytest.fail("deterministic replay attempted a provider call")

        monkeypatch.setattr(OpenAIPlanner, "propose", forbidden)
        monkeypatch.setattr(planner, "propose", forbidden)
        artifact_path = tmp_path / "compiled.json"
        artifact_path.write_text(outcome.artifact.model_dump_json())
        replay_app = create_app(replay_case)
        with running_app(replay_app) as replay_origin:
            rebound = load_demo(replay_origin, artifact_path)
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
                assert replay_app.state.oracle.request_counts[("POST", "/workspace/review")] == 1
                if replay_case == "known_interstitial":
                    assert (
                        replay_app.state.oracle.request_counts[("POST", "/workspace/dismiss")] == 1
                    )
                assert replay_app.state.oracle.submit_requests == 0
                assert replay_app.state.oracle.ledger == []
            finally:
                await replay_surface.close()
        assert app.state.oracle.submit_requests == 0
        assert app.state.oracle.ledger == []

    with running_app(app) as origin:
        asyncio.run(scenario(origin))
