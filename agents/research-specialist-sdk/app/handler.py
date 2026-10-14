"""Spec-conforming request handler subclass.

a2a-sdk 1.1.2's DefaultRequestHandlerV2 delegates task lifecycle operations to
ActiveTask without mapping InvalidParamsError to UnsupportedOperationError on
terminal tasks (unlike on_cancel_task, which converts it to TaskNotCancelableError).

Per A2A 1.0 §3.1.1, §3.1.2, and §3.1.6, operations attempting to interact with a
terminal task (completed, failed, canceled, rejected) MUST return
UnsupportedOperationError (-32004).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.request_handler import validate, validate_request_params
from a2a.utils.errors import InvalidParamsError, UnsupportedOperationError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from a2a.server.context import ServerCallContext
    from a2a.types.a2a_pb2 import Message, SendMessageRequest, SubscribeToTaskRequest, Task


class ConformingRequestHandler(DefaultRequestHandler):
    """DefaultRequestHandler with spec-compliant error mapping for terminal tasks."""

    @validate_request_params
    async def on_message_send(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> Message | Task:
        try:
            return await super().on_message_send(params, context)
        except InvalidParamsError as e:
            if "terminal state" in str(e):
                raise UnsupportedOperationError(str(e)) from e
            raise

    @validate_request_params
    @validate(
        lambda self: self._agent_card.capabilities.streaming,
        "Streaming is not supported by the agent",
    )
    async def on_message_send_stream(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Any, None]:
        try:
            async for event in super().on_message_send_stream(params, context):
                yield event
        except InvalidParamsError as e:
            if "terminal state" in str(e):
                raise UnsupportedOperationError(str(e)) from e
            raise

    @validate_request_params
    @validate(
        lambda self: self._agent_card.capabilities.streaming,
        "Streaming is not supported by the agent",
    )
    async def on_subscribe_to_task(
        self,
        params: SubscribeToTaskRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Any, None]:
        task_id = params.id
        try:
            active_task = await self._active_task_registry.get_or_create(
                task_id,
                call_context=context,
                create_task_if_missing=False,
            )
        except InvalidParamsError as e:
            raise UnsupportedOperationError(str(e)) from e

        async for event in active_task.subscribe(include_initial_task=True):
            yield event
