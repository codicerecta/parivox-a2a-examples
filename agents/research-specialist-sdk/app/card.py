"""The Agent Card this specialist publishes at /.well-known/agent-card.json."""

from __future__ import annotations

from a2a.types import a2a_pb2 as pb
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT, TransportProtocol

AGENT_NAME = "Research Specialist"
AGENT_VERSION = "0.1.0"
SKILL_ID = "web-research"


def build_agent_card(public_url: str, *, auth_enabled: bool) -> pb.AgentCard:
    """Build the Agent Card.

    `public_url` is the RPC endpoint clients should call — inside a VPC that is the
    private DNS name, not a public hostname. It is advertised under
    `supportedInterfaces`, which is how a client discovers where to send requests.
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

    if auth_enabled:
        # Declares that RPC calls carry a shared secret in the X-API-Key header.
        card.security_schemes["apiKey"].api_key_security_scheme.name = "X-API-Key"
        card.security_schemes["apiKey"].api_key_security_scheme.location = "header"
        requirement = card.security_requirements.add()
        requirement.schemes["apiKey"].CopyFrom(pb.StringList())

    return card


__all__ = ["AGENT_NAME", "AGENT_VERSION", "SKILL_ID", "build_agent_card"]
