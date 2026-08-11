"""The Agent Card this specialist publishes at /.well-known/agent-card.json."""

from __future__ import annotations

from a2a.types import a2a_pb2 as pb
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT, TransportProtocol

AGENT_NAME = "Research Specialist"
# Bumped for the duration declaration below. The card version is what answers
# "which build produced this report?" long after the fact, so it moves whenever
# behaviour a caller can observe moves.
AGENT_VERSION = "0.2.0"
SKILL_ID = "web-research"
# Parivox's optional convention for declaring how long work takes. It rides on
# `capabilities.extensions[]` because an AgentCard has no metadata field of its
# own, so the protocol's own extension point is the only place to put it.
# Declaring it is not required: a caller that finds nothing here falls back to
# generic wording.
DURATION_EXTENSION_URI = "https://parivox.net/a2a/extensions/duration/v1"


def build_agent_card(
    public_url: str,
    *,
    auth_enabled: bool,
    typical_duration_seconds: float | None = None,
) -> pb.AgentCard:
    """Build the Agent Card.

    `public_url` is the RPC endpoint clients should call — inside a VPC that is the
    private DNS name, not a public hostname. It is advertised under
    `supportedInterfaces`, which is how a client discovers where to send requests.

    `typical_duration_seconds` is what this particular configuration costs; see
    `settings.py::_derive_typical_duration`. Omitting it produces exactly the
    card this agent published before the convention existed.
    """
    card = pb.AgentCard(
        name=AGENT_NAME,
        description=(
            "Researches a question against current web sources and returns a "
            "markdown report with inline citations. Streams progress while working."
        ),
        version=AGENT_VERSION,
        capabilities=pb.AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/markdown"],
        skills=[
            pb.AgentSkill(
                id=SKILL_ID,
                name="Web research",
                description=(
                    "Answer a research question using current web sources, returning "
                    "a cited markdown report. Best for questions where recency and "
                    "provenance matter."
                ),
                tags=["research", "web-search", "citations"],
                examples=[
                    "What changed in EB-1 visa processing this year? I need current sources.",
                    "Summarize recent guidance on remote notarization, with citations.",
                ],
                input_modes=["text/plain"],
                output_modes=["text/markdown"],
            )
        ],
        supported_interfaces=[
            pb.AgentInterface(
                url=public_url,
                protocol_binding=TransportProtocol.JSONRPC,
                protocol_version=PROTOCOL_VERSION_CURRENT,
            )
        ],
    )

    if typical_duration_seconds and typical_duration_seconds > 0:
        extension = card.capabilities.extensions.add()
        extension.uri = DURATION_EXTENSION_URI
        extension.description = (
            "Typical wall-clock duration of a task on this deployment, in seconds."
        )
        # Not `required`: a client that has never heard of this extension must
        # still be able to call the agent, which is the whole difference between
        # a convention and a dependency.
        extension.required = False
        extension.params.update({"typicalSeconds": float(typical_duration_seconds)})

    if auth_enabled:
        # Declares that RPC calls carry a shared secret in the X-API-Key header.
        card.security_schemes["apiKey"].api_key_security_scheme.name = "X-API-Key"
        card.security_schemes["apiKey"].api_key_security_scheme.location = "header"
        requirement = card.security_requirements.add()
        requirement.schemes["apiKey"].CopyFrom(pb.StringList())

    return card


__all__ = [
    "AGENT_NAME",
    "AGENT_VERSION",
    "DURATION_EXTENSION_URI",
    "SKILL_ID",
    "build_agent_card",
]
