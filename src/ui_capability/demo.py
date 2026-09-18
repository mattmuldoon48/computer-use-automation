"""Explicitly authored browser fixture binding. No discovery or application oracle access."""

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from .contracts import Binding, CapabilityArtifact
from .policy import Policy
from .profiles import Profile


@dataclass(frozen=True)
class DemoBundle:
    artifact: CapabilityArtifact
    profile: Profile
    policy: Policy


def load_demo(
    origin: str = "http://127.0.0.1:8000", artifact_path: Path | None = None
) -> DemoBundle:
    root = files("ui_capability").joinpath("fixtures")
    artifact = CapabilityArtifact.model_validate_json(
        artifact_path.read_bytes()
        if artifact_path
        else root.joinpath("member_ops.artifact.json").read_bytes()
    )
    profile = Profile.model_validate_json(root.joinpath("member_ops.profile.json").read_bytes())
    policy = Policy.model_validate_json(root.joinpath("member_ops.policy.json").read_bytes())
    if (
        artifact.goal.binding.model_copy(update={"origin": profile.binding.origin})
        != profile.binding
    ):
        raise ValueError("fixture application, version, or entry route is incompatible")
    # Explicit installation binding only. No replacement of invocation values or transcripts.
    binding = Binding(origin=origin, entry_route="/", app="member_ops", version="v1")
    artifact = artifact.model_copy(
        update={"goal": artifact.goal.model_copy(update={"binding": binding})}
    )
    profile = profile.model_copy(update={"binding": binding})
    policy = policy.model_copy(
        update={
            "binding": binding,
            "rules": tuple(rule.model_copy(update={"origin": origin}) for rule in policy.rules),
        }
    )
    policy.validate_artifact(artifact)
    return DemoBundle(artifact, profile, policy)
