"""In-memory pub/sub for SSE progress events + cooperative cancellation.

GCP MIGRATION: Replace with Pub/Sub or Firestore realtime listeners so
progress survives multi-instance Cloud Run deployments.
"""

import asyncio
import json
from typing import AsyncGenerator


class CancelledByUser(Exception):
    pass


class ProgressBus:
    def __init__(self) -> None:
        self._queues: dict[str, set[asyncio.Queue[str]]] = {}
        self._history: dict[str, list[str]] = {}
        self._cancelled: set[str] = set()
        self._stop_requested: set[str] = set()  # manual mode: finish recording, keep result
        self._continue_requested: set[str] = set()  # manual login: user signed in, proceed

    async def publish(self, flow_id: str, stage: str, message: str, percent: int = 0) -> None:
        payload = json.dumps({"stage": stage, "message": message, "percent": percent})
        history = self._history.setdefault(flow_id, [])
        history.append(payload)
        if len(history) > 300:
            del history[: len(history) - 300]
        # Deliver to every live subscriber. Guard each put so one dead queue
        # can't stop an error event from reaching the others.
        for queue in self._queues.get(flow_id, set()).copy():
            try:
                queue.put_nowait(payload)
            except Exception:
                pass

    async def publish_error(self, flow_id: str, message: str) -> None:
        """Terminal error event — always recorded in history so a reconnecting
        SSE subscriber still receives it on replay."""
        await self.publish(flow_id, "error", message, 0)

    async def subscribe(self, flow_id: str) -> AsyncGenerator[str, None]:
        """Yield SSE-formatted strings; replays history, then live events.

        Sends a keepalive ping every 15s of inactivity.
        """
        queue: asyncio.Queue[str] = asyncio.Queue()
        self._queues.setdefault(flow_id, set()).add(queue)
        try:
            for payload in self._history.get(flow_id, []):
                yield f"data: {payload}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield "data: ping\n\n"
        finally:
            self._queues.get(flow_id, set()).discard(queue)

    def clear(self, flow_id: str) -> None:
        self._history.pop(flow_id, None)
        self._cancelled.discard(flow_id)
        self._stop_requested.discard(flow_id)
        self._continue_requested.discard(flow_id)

    # ---- cancellation ----

    def request_cancel(self, flow_id: str) -> None:
        self._cancelled.add(flow_id)

    def is_cancelled(self, flow_id: str) -> bool:
        return flow_id in self._cancelled

    # ---- manual-mode stop (finish the recording and continue the pipeline) ----

    def request_stop(self, flow_id: str) -> None:
        self._stop_requested.add(flow_id)

    def is_stop_requested(self, flow_id: str) -> bool:
        return flow_id in self._stop_requested

    # ---- manual-login continue (user has signed in by hand, resume the run) ----

    def request_continue(self, flow_id: str) -> None:
        self._continue_requested.add(flow_id)

    def is_continue_requested(self, flow_id: str) -> bool:
        return flow_id in self._continue_requested

    def clear_cancel(self, flow_id: str) -> None:
        self._cancelled.discard(flow_id)

    def raise_if_cancelled(self, flow_id: str) -> None:
        if flow_id in self._cancelled:
            raise CancelledByUser()


progress_bus = ProgressBus()
