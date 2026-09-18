"""Hand-authored Phase 1 fixture. NOT a discovered capability or live evidence."""

from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from ui_capability.contracts import (
    Binding,
    CapabilityArtifact,
    Click,
    Fill,
    Observation,
    ObservedTarget,
    PublicLiteral,
    Scalar,
    Visible,
)
from ui_capability.evidence import EvidenceSink
from ui_capability.policy import ActionRule, Effect, Policy
from ui_capability.profiles import Profile
from ui_capability.replay import Replay
from ui_capability.session import Session
from ui_capability.surfaces.fake import FakeSurface

BINDING = Binding(
    origin="http://127.0.0.1:8000", entry_route="/prepare", app="member_ops", version="v1"
)
INPUTS: dict[str, Scalar] = {
    "member_id": "000042",
    "product_code": "SAVINGS_BASIC",
    "nickname": "Rainy day",
}


def artifact() -> CapabilityArtifact:
    path = Path(__file__).parent / "fixtures" / "prepare_review.json"
    return CapabilityArtifact.model_validate_json(path.read_bytes())


def policy(cap: CapabilityArtifact, effect: Effect = Effect.PREVIEW) -> Policy:
    from ui_capability.contracts import walk

    literals = tuple(node.value for node in walk(cap) if isinstance(node, PublicLiteral))
    return Policy(
        policy_id="prepare_only",
        binding=BINDING,
        permissions=("prepare",),
        approved_literals=literals,
        rules=tuple(
            ActionRule(
                action=kind,
                target=cap.targets[key],
                origin=BINDING.origin,
                route="/prepare",
                permission="prepare",
                effect=rule_effect,
            )
            for kind, key, rule_effect in (
                ("fill", "nickname_field", Effect.SAFE_OVERWRITE),
                ("click", "review_button", effect),
                ("click", "dismiss", Effect.READ),
            )
        ),
    )


def observation(
    cap: CapabilityArtifact,
    inputs: dict[str, Scalar],
    *,
    review: bool = False,
    filled: bool = False,
    extra: tuple[str, ...] = (),
    wrong_member: bool = False,
) -> Observation:
    values: dict[str, Scalar] = {
        **inputs,
        "monthly_fee_minor": "1,234.56",
        "currency": "USD",
        "submitted": False,
        "nickname_field": inputs["nickname"] if filled else "",
        "review_button": "Review",
    }
    if wrong_member:
        values["member_id"] = "009999"
    if review:
        values["review"] = "Unsubmitted review"
    values.update(dict.fromkeys(extra, "Visible"))
    controls = {
        "nickname_field": ("textbox", "input"),
        "review_button": ("button", "button"),
        "dismiss": ("button", "button"),
    }
    targets = tuple(
        ObservedTarget(
            ref=name,
            spec=cap.targets[name],
            role=controls.get(name, ("status", "text"))[0],
            control=controls.get(name, ("status", "text"))[1],
            visible=True,
            text=str(value),
            value=value,
            grounding="visible_role",
        )
        for name, value in values.items()
    )
    return Observation(
        observation_id="fixture_observation",
        run_id="fixture_run",
        session_id="fixture_session",
        ownership_epoch=0,
        origin=BINDING.origin,
        route="/prepare",
        targets=targets,
    )


@dataclass
class Rig:
    replay: Replay
    session: Session
    surface: FakeSurface
    audit: StringIO
    cap: CapabilityArtifact


def rig(
    *,
    cap: CapabilityArtifact | None = None,
    profile: Profile | None = None,
    inputs: dict[str, Scalar] | None = None,
    effect: Effect = Effect.PREVIEW,
    update: bool = True,
    wrong_member: bool = False,
) -> Rig:
    cap = cap or artifact()
    inputs = inputs or dict(INPUTS)
    audit = StringIO()
    surface = FakeSurface(BINDING, [observation(cap, inputs)])
    filled = False

    async def acted(action: object, target: object, value: object) -> None:
        nonlocal filled
        if not update:
            return
        if isinstance(action, Fill):
            filled = True
        surface.observations[:] = [
            observation(
                cap,
                inputs,
                filled=filled,
                review=isinstance(action, Click),
                wrong_member=wrong_member and isinstance(action, Click),
            )
        ]

    surface.on_action = acted
    session = Session(surface, policy(cap, effect), EvidenceSink(audit))
    replay = Replay(
        cap,
        profile
        or Profile(
            profile_id="member_ops",
            binding=BINDING,
            targets=artifact().targets,
            terminal_checks=(Visible(target="review"),),
        ),
        session,
        frozenset({"prepare"}),
    )
    return Rig(replay, session, surface, audit, cap)
