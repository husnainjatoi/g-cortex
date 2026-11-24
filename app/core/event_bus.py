"""
G-Cortex Cross-Subagent Event Bus
In-process publish/subscribe event dispatcher that enables autonomous
cross-subagent communication across Course, Mess, and Requisition domains.
"""
import logging
import threading
from typing import Callable

logger = logging.getLogger("g_cortex.event_bus")

# ── Event Type Constants ───────────────────────────────────────
# Course Management Events
COURSE_ENROLLED = "course.enrolled"
COURSE_DROPPED = "course.dropped"

# Mess Management Events
MESS_REBATE_REQUESTED = "mess.rebate_requested"
MESS_ENTRY_LOGGED = "mess.entry_logged"

# Requisition Management Events
VENUE_BOOKED = "venue.booked"
VENUE_RELEASED = "venue.released"
EQUIPMENT_CHECKED_OUT = "equipment.checked_out"
EQUIPMENT_RETURNED = "equipment.returned"


class EventBus:
    """Thread-safe in-process publish/subscribe event dispatcher.

    Subagents register handlers for specific event types. When any
    subagent publishes an event, all registered handlers are called
    synchronously in the publishing thread.
    """

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()
        self._event_log: list[dict] = []

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """Register a handler function for a specific event type.

        Args:
            event_type: Event category string (e.g. VENUE_BOOKED).
            handler: Callable that accepts (event_type: str, data: dict).
        """
        with self._lock:
            self._handlers.setdefault(event_type, [])
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)
                logger.debug("Subscribed handler '%s' to event '%s'", handler.__name__, event_type)

    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        """Remove a previously registered handler.

        Args:
            event_type: Event category string.
            handler: The handler callable to remove.
        """
        with self._lock:
            handlers = self._handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)

    def publish(self, event_type: str, data: dict) -> int:
        """Dispatch an event to all registered subscribers.

        Args:
            event_type: Event category string.
            data: Arbitrary event payload dictionary.

        Returns:
            Number of handlers invoked.
        """
        with self._lock:
            handlers = list(self._handlers.get(event_type, []))
            self._event_log.append({"event_type": event_type, "data": data})

        invoked = 0
        for handler in handlers:
            try:
                handler(event_type, data)
                invoked += 1
            except Exception as exc:
                logger.error(
                    "Event handler '%s' raised an exception for event '%s': %s",
                    getattr(handler, "__name__", repr(handler)),
                    event_type,
                    exc,
                    exc_info=True,
                )

        logger.info("Published '%s' → %d handler(s) invoked | payload: %s", event_type, invoked, data)
        return invoked

    def get_event_log(self) -> list[dict]:
        """Return a snapshot of all published events (for debugging/auditing)."""
        with self._lock:
            return list(self._event_log)

    def clear_log(self) -> None:
        """Clear the internal event log."""
        with self._lock:
            self._event_log.clear()

    def list_subscriptions(self) -> dict[str, list[str]]:
        """Return a mapping of event types to their registered handler names."""
        with self._lock:
            return {
                evt: [getattr(h, "__name__", repr(h)) for h in handlers]
                for evt, handlers in self._handlers.items()
            }


# Singleton instance shared across the application
event_bus = EventBus()
