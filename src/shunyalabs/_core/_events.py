"""Event emitter for handling streaming server messages.

Supports both sync and async callbacks. Accepts any hashable event type
(str, Enum, etc.) for use across ASR, TTS, and Flow modules.

Adapted from the existing Flow SDK EventEmitter.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, Hashable, Optional

from ._logging import get_logger


class EventEmitter:
    """Type-safe event emitter for handling server messages.

    Supports both decorator and direct registration patterns.
    Works with both sync and async callbacks.

    Examples:
        >>> emitter = EventEmitter()
        >>> @emitter.on("message")
        ... def handler(data):
        ...     print(data)
        >>> emitter.emit("message", {"text": "hello"})
    """

    def __init__(self) -> None:
        self._handlers: dict[Hashable, set[Callable]] = {}
        self._once_handlers: dict[Hashable, set[Callable]] = {}
        self._logger = get_logger(__name__)

    def on(self, event: Hashable, callback: Optional[Callable] = None) -> Callable:
        """Register a persistent event handler.

        Can be used as a decorator or called directly.

        Args:
            event: The event type to listen for.
            callback: The callback function (optional for decorator usage).

        Returns:
            The callback function or decorator.
        """
        if callback is not None:
            self._add_handler(event, callback, persistent=True)
            return callback

        def decorator(func: Callable) -> Callable:
            self._add_handler(event, func, persistent=True)
            return func

        return decorator

    def once(self, event: Hashable, callback: Optional[Callable] = None) -> Callable:
        """Register a one-time event handler.

        Args:
            event: The event type to listen for.
            callback: The callback function (optional for decorator usage).

        Returns:
            The callback function or decorator.
        """
        if callback is not None:
            self._add_handler(event, callback, persistent=False)
            return callback

        def decorator(func: Callable) -> Callable:
            self._add_handler(event, func, persistent=False)
            return func

        return decorator

    def off(self, event: Hashable, callback: Callable) -> None:
        """Remove an event handler.

        Args:
            event: The event type.
            callback: The callback to remove.
        """
        self._handlers.get(event, set()).discard(callback)
        self._once_handlers.get(event, set()).discard(callback)

    def emit(self, event: Hashable, message: Any) -> None:
        """Emit event to all registered handlers.

        For async contexts, callbacks are scheduled as tasks.
        For sync callbacks, they're run in the executor.

        Args:
            event: The event type.
            message: The message data.
        """
        callbacks = self._handlers.get(event, set()).copy()
        once_callbacks = self._once_handlers.get(event, set()).copy()

        if once_callbacks:
            self._once_handlers[event].clear()

        all_callbacks = callbacks.union(once_callbacks)
        for cb in all_callbacks:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._async_emit(cb, message))
            except RuntimeError:
                # No running loop — call synchronously
                self._sync_emit(cb, message)

    async def _async_emit(self, callback: Callable, message: Any) -> None:
        """Emit a single event to a handler (async context)."""
        try:
            if inspect.iscoroutinefunction(callback):
                await callback(message)
            else:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, callback, message)
        except Exception as e:
            self._logger.warning(
                "Event handler error in %r: %s", callback, e, exc_info=True
            )

    def _sync_emit(self, callback: Callable, message: Any) -> None:
        """Emit a single event to a handler (sync context)."""
        try:
            callback(message)
        except Exception as e:
            self._logger.warning(
                "Event handler error in %r: %s", callback, e, exc_info=True
            )

    def remove_all_listeners(self, event: Optional[Hashable] = None) -> None:
        """Remove all listeners for an event, or all events if None."""
        if event is not None:
            self._handlers.pop(event, None)
            self._once_handlers.pop(event, None)
        else:
            self._handlers.clear()
            self._once_handlers.clear()

    def listeners(self, event: Hashable) -> list[Callable]:
        """Get all listeners for an event."""
        persistent = list(self._handlers.get(event, set()))
        once = list(self._once_handlers.get(event, set()))
        return persistent + once

    def _add_handler(self, event: Hashable, callback: Callable, persistent: bool) -> None:
        """Add handler to the appropriate collection."""
        if not callable(callback):
            raise TypeError("Callback must be callable")
        target = self._handlers if persistent else self._once_handlers
        if event not in target:
            target[event] = set()
        target[event].add(callback)


__all__ = ["EventEmitter"]
