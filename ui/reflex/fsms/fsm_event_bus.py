from collections import defaultdict
from typing import Any, Callable


class EventBus:
    def __init__(self):
        self._subs: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event: str, handler: Callable[..., Any]) -> Callable[[], None]:
        self._subs[event].append(handler)
        return lambda: self._subs[event].remove(handler)  # unsubscribe

    def publish(self, event: str, **payload) -> None:
        for h in list(self._subs[event]):
            h(**payload)

fsm_event_bus = EventBus()  # single app-wide instance