"""Bridges a research run onto the A2A task lifecycle.

Event ordering is not negotiable: the `Task` must be enqueued before any status or
artifact event, and the stream must end on a terminal status. The SDK enforces this
by raising, so getting it wrong is loud rather than subtle — but it does mean the
Task has to go out before any work starts.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import a2a_pb2 as pb

from .research import ResearchProvider

logger = logging.getLogger("a2a.executor")

REPORT_ARTIFACT_NAME = "report.md"


class ResearchExecutor(AgentExecutor):
    """Runs a research provider and reports it as an A2A task."""

    def __init__(self, provider: ResearchProvider) -> None:
        self._provider = provider

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
            async for event in self._provider.research(question, permitted_context):
                if event.kind == "progress":
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


__all__ = ["REPORT_ARTIFACT_NAME", "ResearchExecutor"]
