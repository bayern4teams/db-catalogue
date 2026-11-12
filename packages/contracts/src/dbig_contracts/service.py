"""The one request guard every service mounts (report Part 3, shared changes made once, F2).

A plain ASGI middleware, so this library needs no web framework: it answers `GET /healthz`
without a key, refuses a request without a valid caller key (401) or without the route's scope
(403), bounds the body (413) and the request rate (429), then hands the request on.
"""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Awaitable, Callable, MutableMapping
from threading import Lock
from typing import Any

from .security import CallerKey, authenticate, permits

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

HEALTH_PATH = "/healthz"


async def _json(send: Send, status: int, body: dict) -> None:
    payload = json.dumps(body).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


class ServiceGuard:
    """`route_scopes` maps a path prefix to the scope a caller key needs for it; the longest
    matching prefix wins, and a path no prefix matches needs only a valid key."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        keys: list[CallerKey],
        route_scopes: dict[str, str],
        max_request_bytes: int,
        max_requests_per_minute: int | None = None,
    ) -> None:
        self.app = app
        self.keys = keys
        self.route_scopes = sorted(route_scopes.items(), key=lambda item: -len(item[0]))
        self.max_request_bytes = max_request_bytes
        self.max_requests_per_minute = max_requests_per_minute
        # One window per caller, so a busy caller cannot lock the others out (review finding 9,
        # 2026-11-12); bounded by the callers in the key file.
        self.timestamps: dict[str, deque[float]] = {}
        self.lock = Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path, method = scope["path"], scope["method"]
        if path == HEALTH_PATH and method == "GET":
            # Fixed text, no key: the Docker health check only learns that the process answers.
            await _json(send, 200, {"status": "ok"})
            return
        headers = {name.lower(): value for name, value in scope["headers"]}
        authorization = headers.get(b"authorization", b"").decode("latin-1") or None
        caller = authenticate(self.keys, authorization)
        if caller is None:
            await _json(send, 401, {"error": {"code": "UNAUTHORIZED"}})
            return
        needed = next((need for prefix, need in self.route_scopes if path.startswith(prefix)), None)
        if needed is not None and not permits(caller, needed):
            await _json(send, 403, {"error": {"code": "FORBIDDEN_SCOPE"}})
            return
        if method == "POST":
            # No Content-Length (an empty or chunked body) is allowed: the loop below still bounds
            # what is read (review finding 8, 2026-11-12). A stated length must fit.
            length = headers.get(b"content-length")
            if length is not None and (
                not length.decode("latin-1").isdecimal()
                or int(length) > self.max_request_bytes
            ):
                await _json(send, 413, {"error": {"code": "PAYLOAD_TOO_LARGE"}})
                return
            body = bytearray()
            more = True
            while more:
                message = await receive()
                body.extend(message.get("body", b""))
                more = message.get("more_body", False)
                if len(body) > self.max_request_bytes:
                    await _json(send, 413, {"error": {"code": "PAYLOAD_TOO_LARGE"}})
                    return
            buffered: Message = {"type": "http.request", "body": bytes(body), "more_body": False}
            sent = False

            async def replay() -> Message:
                nonlocal sent
                if sent:
                    return await receive()
                sent = True
                return buffered

            receive = replay
        if self.max_requests_per_minute is not None:
            with self.lock:
                now = time.monotonic()
                window = self.timestamps.setdefault(caller.caller, deque())
                while window and window[0] < now - 60:
                    window.popleft()
                if len(window) >= self.max_requests_per_minute:
                    limited = True
                else:
                    limited = False
                    window.append(now)
            if limited:
                await _json(send, 429, {"error": {"code": "RATE_LIMITED"}})
                return
        await self.app(scope, receive, send)
