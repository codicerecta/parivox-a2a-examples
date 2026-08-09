"""Bridges a research run onto the A2A task lifecycle.

Event ordering is not negotiable: the `Task` must be enqueued before any status or
artifact event, and the stream must end on a terminal status. The SDK enforces this
by raising, so getting it wrong is loud rather than subtle — but it does mean the
Task has to go out before any work starts.

The task stream is also kept alive on a timer rather than on the research
provider's goodwill — see `HEARTBEAT_INTERVAL_SECONDS`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Sequence

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import a2a_pb2 as pb

from .research import ResearchEvent, ResearchProvider

logger = logging.getLogger("a2a.executor")

REPORT_ARTIFACT_NAME = "report.md"

# How long the task may go without emitting anything before a keepalive status
# is sent.
#
# Clients time out on the gap *between* events, not on total duration — a
# reasonable design, since a silent stream is indistinguishable from a dead one.
# But that makes stream survival depend on how chatty a model happens to be
# during a given run, which is not a property anything here controls: a single
# web-search call can sit quiet for a long stretch, and one quiet stretch ends
# the delegation with the caller announcing failure for work that was fine.
#
# Emitting on a timer makes liveness a property of this server instead. The
# keepalive is a WORKING status with no message, which is deliberate: clients
# render progress from the message text, so a message-less update resets their
# read timeout without putting anything in front of a user. Total-duration
# bounds still apply on the caller's side, and should — this keeps a live task
# alive, it does not keep a stuck one alive forever.
HEARTBEAT_INTERVAL_SECONDS = 20.0

_HEARTBEAT = object()


class ResearchExecutor(AgentExecutor):
    """Runs a research provider and reports it as an A2A task."""

    def __init__(
        self,
        provider: ResearchProvider,
        heartbeat_interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self._provider = provider
        self._heartbeat_interval = heartbeat_interval_seconds

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        question = _extract_question(context)
        permitted_context = _extract_context_excerpts(context)

        # The Task goes first. Enqueuing a status update before this raises -32006.
        await event_queue.enqueue_event(
            pb.Task(
                id=context.task_id,
                context_id=context.context_id,
                status=pb.TaskStatus(state=pb.TaskState.TASK_STATE_SUBMITTED),
            )
        )
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)

        if not question:
            await _fail(updater, "No question was provided to research.")
            return

        await updater.start_work()
        logger.info("Researching: %s", question[:200])

        produced_report = False
        try:
            source = self._provider.research(question, permitted_context)
            async for event in _with_heartbeat(source, self._heartbeat_interval):
                if event is _HEARTBEAT:
                    # No message part: keeps the stream alive without showing a
                    # participant a progress line that says nothing.
                    await updater.update_status(pb.TaskState.TASK_STATE_WORKING)
                elif event.kind == "progress":
                    await updater.update_status(
                        pb.TaskState.TASK_STATE_WORKING,
                        message=updater.new_agent_message([pb.Part(text=event.text)]),
                    )
                elif event.kind == "report":
                    await updater.add_artifact(
                        [pb.Part(text=event.text)],
                        name=REPORT_ARTIFACT_NAME,
                        metadata={"sourceCount": len(event.sources)},
                    )
                    produced_report = True
        except Exception as exc:  # noqa: BLE001 - any failure must terminate the task
            # A task that ends without a terminal status leaves the client hanging
            # until its own timeout, so every failure path has to land on `failed`.
            logger.exception("Research run failed")
            await _fail(updater, f"Research failed: {exc}")
            return

        if not produced_report:
            await _fail(updater, "Research completed without producing a report.")
            return

        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.update_status(pb.TaskState.TASK_STATE_CANCELED)


async def _with_heartbeat(
    source: AsyncIterator[ResearchEvent], interval: float
) -> AsyncIterator[object]:
    """Yield the provider's events, injecting `_HEARTBEAT` during quiet stretches.

    The provider is drained by a separate task feeding a queue, because an async
    generator cannot be polled with a timeout — awaiting `__anext__` commits to
    waiting however long the model takes. Errors are forwarded through the queue
    rather than raised in the producer, so the caller's single `except` around
    the loop still sees them and still lands the task on a terminal state.
    """
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for item in source:
                await queue.put(("item", item))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - forwarded, not swallowed
            await queue.put(("error", exc))
        else:
            await queue.put(("done", None))

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=interval)
            except TimeoutError:
                yield _HEARTBEAT
                continue
            if kind == "done":
                return
            if kind == "error":
                raise payload  # type: ignore[misc]
            yield payload
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        # Async generators are not closed by cancelling the task that drives
        # them; without this an interrupted OpenAI stream leaks its HTTP
        # connection until garbage collection.
        aclose = getattr(source, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()


async def _fail(updater: TaskUpdater, message: str) -> None:
    await updater.failed(message=updater.new_agent_message([pb.Part(text=message)]))


def _extract_question(context: RequestContext) -> str:
    """Pull the question out of the incoming message."""
    text = context.get_user_input()
    if text:
        return text.strip()
    message = getattr(context, "message", None)
    if message is None:
        return ""
    return " ".join(part.text for part in message.parts if part.text).strip()


def _extract_context_excerpts(context: RequestContext) -> Sequence[str]:
    """Read caller-supplied background excerpts from message metadata.

    This is the only channel through which the caller discloses anything beyond the
    question itself, which is what makes disclosure auditable on their side.
    """
    message = getattr(context, "message", None)
    metadata = getattr(message, "metadata", None) if message is not None else None
    if not metadata:
        return []
    try:
        excerpts = metadata["permittedContext"]
    except (KeyError, TypeError):
        return []
    if isinstance(excerpts, str):
        return [excerpts]
    try:
        return [str(item) for item in excerpts]
    except TypeError:
        return []


__all__ = ["HEARTBEAT_INTERVAL_SECONDS", "REPORT_ARTIFACT_NAME", "ResearchExecutor"]
