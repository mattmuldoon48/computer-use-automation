"""DOM discovery is independent of supplied target recipes; all execution remains gated."""

import asyncio
from io import StringIO

import pytest

from sandbox.app import create_app
from sandbox.server import running_app
from ui_capability.contracts import FrameScope, PublicLiteral, RoleLocator, Success, TargetSpec
from ui_capability.demo import load_demo
from ui_capability.evidence import EvidenceSink
from ui_capability.replay import Replay
from ui_capability.session import Session
from ui_capability.surfaces.playwright import PlaywrightSurface

pytestmark = pytest.mark.browser


def test_unseeded_dom_targets_support_fresh_policy_gated_replay():
    """Exercise frames, adjacent captions, input-ref links and section-disambiguated buttons."""
    app = create_app()
    values = {"member_id": "000099", "product_code": "SAVINGS_PLUS", "nickname": "Reserve"}

    async def scenario(origin):
        bundle = load_demo(origin)
        sink = EvidenceSink(StringIO())
        surface = await PlaywrightSurface.launch(
            bundle.artifact.goal.binding, {}, values, sink, discovery=True
        )
        try:
            session = Session(surface, bundle.policy, sink)
            # The existing reviewed replay drives this regression, not a model or
            # compiler. The surface never receives any of its target recipes.
            replay = Replay(bundle.artifact, bundle.profile, session, frozenset({"prepare"}))
            result = await replay.run(values)
            assert isinstance(result, Success), result
            assert result.outputs == {
                **values,
                "monthly_fee_minor": 700,
                "currency": "USD",
                "submitted": False,
            }
            observed = await session.observe()
            assert all(target.derived for target in observed.targets)
            assert app.state.oracle.submit_requests == 0
            assert app.state.oracle.request_counts[("POST", "/workspace/review")] == 1
        finally:
            await surface.close()

    with running_app(app) as origin:
        asyncio.run(scenario(origin))


def test_discovery_merges_authored_specs_and_rejects_ambiguous_or_hidden_controls():
    heading = TargetSpec(
        frame=FrameScope(kind="named_frame", name="workspace"),
        locator=RoleLocator(role="heading", name=PublicLiteral(value="Operations home")),
    )

    async def scenario(origin):
        bundle = load_demo(origin)
        surface = await PlaywrightSurface.launch(
            bundle.artifact.goal.binding,
            {"home": heading, "home_alias": heading},
            {},
            EvidenceSink(StringIO()),
            discovery=True,
        )
        try:
            # Deliberately test-only DOM mutation, analogous to hostile/operator
            # changes. The production API never gives a planner these handles.
            page = surface._PlaywrightSurface__page
            frame = page.frame(name="workspace")
            await frame.set_content(
                "<h1>Operations home</h1>"
                "<fieldset><legend>Account preparation</legend>"
                "<button>Review</button><button>Review</button></fieldset>"
                "<section><h2>Help</h2><button>Review</button></section>"
                '<label for="unstable-id">Nickname</label><input id="unstable-id">'
                '<label for="private-id">Password</label>'
                '<input id="private-id" type="password" value="PASSWORD_CANARY">'
                '<input type="hidden" value="HIDDEN_CANARY">'
                "<button hidden>HIDDEN_ACTION_CANARY</button>"
                '<div aria-hidden="true"><button>ARIA_HIDDEN_CANARY</button></div>'
            )
            observed = await surface.observe("run", "session", 0)
            home = [target for target in observed.targets if target.spec == heading]
            assert len(home) == 1 and home[0].derived
            reviews = [
                target
                for target in observed.targets
                if isinstance(target.spec.locator, RoleLocator)
                and target.spec.locator.name == PublicLiteral(value="Review")
            ]
            assert len(reviews) == 1
            assert reviews[0].spec.section == PublicLiteral(value="Help")
            assert reviews[0].derived
            serialized = observed.model_dump_json()
            for private in (
                "unstable-id",
                "private-id",
                "PASSWORD_CANARY",
                "HIDDEN_CANARY",
                "HIDDEN_ACTION_CANARY",
                "ARIA_HIDDEN_CANARY",
            ):
                assert private not in serialized
            # Observation-local references must not change semantic ordering.
            refreshed = await surface.observe("run", "session", 0)
            assert [target.model_dump(exclude={"ref"}) for target in observed.targets] == [
                target.model_dump(exclude={"ref"}) for target in refreshed.targets
            ]
        finally:
            await surface.close()

    with running_app(create_app()) as origin:
        asyncio.run(scenario(origin))
