"""Task-lifecycle tests.

These assert the ordering rule the protocol enforces: the Task is emitted first, and
the stream always ends on a terminal state. A task that ends without a terminal
status leaves a client waiting for its own timeout, which is the failure mode most
likely to look like "the agent hung".
"""

from collections.abc import AsyncIterator, Sequence

import pytest
from a2a.types import a2a_pb2 as pb

from app.card import DURATION_EXTENSION_URI
from app.executor import REPORT_ARTIFACT_NAME, ResearchExecutor
from app.research import CannedResearchProvider, ResearchEvent


class FakeEventQueue:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def enqueue_event(self, event) -> None:
        self.events.append(event)


class FakeContext:
    def __init__(self, text: str = "a question", metadata: dict | None = None) -> None:
        self.task_id = "task-1"
        self.context_id = "ctx-1"
        self._text = text
        self.message = pb.Message(
            message_id="m1",
            role=pb.Role.ROLE_USER,
            parts=[pb.Part(text=text)] if text else [],
        )
        if metadata:
            for key, value in metadata.items():
                self.message.metadata[key] = value

    def get_user_input(self) -> str:
        return self._text


class ExplodingProvider:
    async def research(
        self, question: str, permitted_context: Sequence[str]
    ) -> AsyncIterator[ResearchEvent]:
        yield ResearchEvent(kind="progress", text="starting")
        raise RuntimeError("upstream exploded")


class SilentProvider:
    """Finishes without ever producing a report."""

    async def research(
        self, question: str, permitted_context: Sequence[str]
    ) -> AsyncIterator[ResearchEvent]:
        yield ResearchEvent(kind="progress", text="thinking")


def states(queue: FakeEventQueue) -> list[str]:
    out = []
    for event in queue.events:
        if isinstance(event, (pb.Task, pb.TaskStatusUpdateEvent)):
            out.append(pb.TaskState.Name(event.status.state))
        elif isinstance(event, pb.TaskArtifactUpdateEvent):
            out.append("ARTIFACT")
    return out


async def test_happy_path_emits_task_first_and_terminates_completed():
    queue = FakeEventQueue()
    await ResearchExecutor(CannedResearchProvider()).execute(FakeContext(), queue)

    assert isinstance(queue.events[0], pb.Task), "Task must be the first event emitted"
    sequence = states(queue)
    assert sequence[0] == "TASK_STATE_SUBMITTED"
    assert "ARTIFACT" in sequence
    assert sequence[-1] == "TASK_STATE_COMPLETED"


async def test_artifact_carries_the_report():
    queue = FakeEventQueue()
    await ResearchExecutor(CannedResearchProvider()).execute(FakeContext(), queue)

    artifacts = [e for e in queue.events if isinstance(e, pb.TaskArtifactUpdateEvent)]
    assert len(artifacts) == 1
    assert artifacts[0].artifact.name == REPORT_ARTIFACT_NAME
    assert "## Sources" in artifacts[0].artifact.parts[0].text


async def test_the_first_status_carries_a_per_task_estimate():
    # The refinement half of the duration convention: the card says what this
    # agent usually costs, this says what this task will.
    queue = FakeEventQueue()
    await ResearchExecutor(CannedResearchProvider(delay_seconds=1.0)).execute(
        FakeContext(), queue
    )

    working = [
        e
        for e in queue.events
        if isinstance(e, pb.TaskStatusUpdateEvent)
        and e.status.state == pb.TaskState.TASK_STATE_WORKING
    ]
    estimate = working[0].metadata[DURATION_EXTENSION_URI]["estimatedSeconds"]
    assert estimate == 2.5


async def test_a_provider_that_cannot_estimate_declares_nothing():
    # Live web research does not know how long it will take, and a guess a
    # caller acts on is worse than silence.
    class UnknowingProvider:
        estimated_seconds = None

        async def research(self, question, permitted_context):
            yield ResearchEvent(kind="report", text="# done", sources=[])

    queue = FakeEventQueue()
    await ResearchExecutor(UnknowingProvider()).execute(FakeContext(), queue)

    working = [
        e
        for e in queue.events
        if isinstance(e, pb.TaskStatusUpdateEvent)
        and e.status.state == pb.TaskState.TASK_STATE_WORKING
    ]
    assert working, "the task still starts work"
    assert DURATION_EXTENSION_URI not in working[0].metadata


async def test_provider_failure_terminates_the_task_as_failed():
    queue = FakeEventQueue()
    await ResearchExecutor(ExplodingProvider()).execute(FakeContext(), queue)

    assert states(queue)[-1] == "TASK_STATE_FAILED"


async def test_provider_that_never_reports_still_terminates():
    queue = FakeEventQueue()
    await ResearchExecutor(SilentProvider()).execute(FakeContext(), queue)

    assert states(queue)[-1] == "TASK_STATE_FAILED"


async def test_empty_question_fails_without_calling_the_provider():
    queue = FakeEventQueue()
    await ResearchExecutor(ExplodingProvider()).execute(FakeContext(text=""), queue)

    assert isinstance(queue.events[0], pb.Task)
    assert states(queue)[-1] == "TASK_STATE_FAILED"


async def test_permitted_context_reaches_the_provider():
    captured: dict[str, Sequence[str]] = {}

    class CapturingProvider:
        async def research(self, question, permitted_context):
            captured["context"] = list(permitted_context)
            yield ResearchEvent(kind="report", text="# done", sources=[])

    queue = FakeEventQueue()
    context = FakeContext(metadata={"permittedContext": ["excerpt one", "excerpt two"]})
    await ResearchExecutor(CapturingProvider()).execute(context, queue)

    assert captured["context"] == ["excerpt one", "excerpt two"]


async def test_missing_permitted_context_is_empty_not_an_error():
    captured: dict[str, Sequence[str]] = {}

    class CapturingProvider:
        async def research(self, question, permitted_context):
            captured["context"] = list(permitted_context)
            yield ResearchEvent(kind="report", text="# done", sources=[])

    queue = FakeEventQueue()
    await ResearchExecutor(CapturingProvider()).execute(FakeContext(), queue)

    assert captured["context"] == []


@pytest.mark.parametrize("progress_count", [0, 1, 5])
async def test_progress_events_are_working_status_updates(progress_count):
    class NoisyProvider:
        async def research(self, question, permitted_context):
            for i in range(progress_count):
                yield ResearchEvent(kind="progress", text=f"step {i}")
            yield ResearchEvent(kind="report", text="# done", sources=[])

    queue = FakeEventQueue()
    await ResearchExecutor(NoisyProvider()).execute(FakeContext(), queue)

    with_message = [
        e
        for e in queue.events
        if isinstance(e, pb.TaskStatusUpdateEvent)
        and e.status.state == pb.TaskState.TASK_STATE_WORKING
        and e.status.message.parts
    ]
    assert len(with_message) == progress_count


class SlowProvider:
    """Goes quiet for longer than a client's between-event read timeout.

    This is the shape of a real web-search run, which the canned provider cannot
    reproduce: it emits on a fixed schedule by construction, so it never leaves a
    gap in the first place.
    """

    def __init__(self, quiet_seconds: float) -> None:
        self._quiet = quiet_seconds

    async def research(
        self, question: str, permitted_context: Sequence[str]
    ) -> AsyncIterator[ResearchEvent]:
        import asyncio

        yield ResearchEvent(kind="progress", text="searching the web (1)")
        await asyncio.sleep(self._quiet)
        yield ResearchEvent(kind="report", text="# Report", sources=[])


def status_messages(queue: FakeEventQueue) -> list[str]:
    """Text of every status update, empty string for message-less ones."""
    out = []
    for event in queue.events:
        if isinstance(event, pb.TaskStatusUpdateEvent):
            parts = list(event.status.message.parts) if event.status.HasField("message") else []
            out.append(" ".join(p.text for p in parts))
    return out


async def test_a_quiet_provider_still_emits_keepalive_events():
    # Without this the stream survives only as long as the model stays chatty,
    # and one long web-search call ends the delegation with the caller
    # announcing failure for work that was proceeding normally.
    queue = FakeEventQueue()
    await ResearchExecutor(
        SlowProvider(quiet_seconds=0.3), heartbeat_interval_seconds=0.05
    ).execute(FakeContext(), queue)

    sequence = states(queue)
    assert sequence[-1] == "TASK_STATE_COMPLETED"
    working = [s for s in sequence if s == "TASK_STATE_WORKING"]
    assert len(working) >= 4, f"expected keepalives during the quiet stretch, got {sequence}"


async def test_keepalives_carry_no_message():
    # Clients render progress from the message text, so a keepalive with a
    # message would put a meaningless line in front of a participant every
    # interval. It exists to reset a read timeout, not to say anything.
    queue = FakeEventQueue()
    await ResearchExecutor(
        SlowProvider(quiet_seconds=0.3), heartbeat_interval_seconds=0.05
    ).execute(FakeContext(), queue)

    messages = status_messages(queue)
    assert "searching the web (1)" in messages
    assert messages.count("") >= 3, f"expected message-less keepalives, got {messages}"


async def test_a_chatty_provider_emits_no_keepalives():
    queue = FakeEventQueue()
    await ResearchExecutor(
        CannedResearchProvider(), heartbeat_interval_seconds=30
    ).execute(FakeContext(), queue)

    # Two message-less status updates are structural — start_work() and the
    # terminal complete(). Anything past those is a keepalive fired against a
    # provider that never went quiet.
    messages = status_messages(queue)
    assert messages.count("") == 2, messages
    assert "searching for current sources" in messages


async def test_provider_failure_still_terminates_with_heartbeats_enabled():
    # The producer runs in its own task now, so an exception has to be forwarded
    # rather than raised where the caller's `except` used to see it directly.
    queue = FakeEventQueue()
    await ResearchExecutor(ExplodingProvider(), heartbeat_interval_seconds=0.05).execute(
        FakeContext(), queue
    )

    assert states(queue)[-1] == "TASK_STATE_FAILED"


async def test_the_provider_generator_is_closed_when_the_run_ends():
    closed: dict[str, bool] = {"value": False}

    class ClosingProvider:
        async def research(self, question, permitted_context):
            try:
                yield ResearchEvent(kind="report", text="# done", sources=[])
            finally:
                closed["value"] = True

    queue = FakeEventQueue()
    await ResearchExecutor(ClosingProvider(), heartbeat_interval_seconds=0.05).execute(
        FakeContext(), queue
    )

    assert closed["value"], "an interrupted provider stream must not leak its connection"
