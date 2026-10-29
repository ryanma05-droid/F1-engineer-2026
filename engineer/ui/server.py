"""The app window's server: a tiny HTTP server on this PC only (127.0.0.1), run on the engineer's
own asyncio loop, so the window shows the live race and changes settings without a second program.

The page (engineer/ui/page.py) asks for /api/state about once a second. Settings changes come in as
POST /api/settings and must carry the X-Engineer header: a web page from anywhere else can't add
that header to a request to this PC, so only our own page can change settings.

Routes:
    GET  /                   the app page
    GET  /api/state          live status (session, radio calls, controller, tempo, report)
    GET  /api/report         the latest driver report's themes, tracks and strengths
    GET  /report/latest.pdf  the latest driver report PDF
    POST /api/settings       {"controller": "xbox"|"playstation"|"off", "tempo": ..., "setup_done"}
    POST /api/report/build   build a new driver report in the background
    POST /api/feedback/dismiss {"id": ...}  drop a question from your feedback log
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

MAX_BODY = 64 * 1024
TYPES = {".pdf": "application/pdf", ".html": "text/html; charset=utf-8",
         ".json": "application/json"}


class Response:
    def __init__(self, status: int = 200, body: bytes | str | dict | list = b"",
                 content_type: str | None = None):
        if isinstance(body, (dict, list)):
            body, content_type = json.dumps(body), content_type or "application/json"
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.status, self.body = status, body
        self.content_type = content_type or "text/plain; charset=utf-8"


REASONS = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
           405: "Method Not Allowed", 413: "Payload Too Large", 500: "Internal Server Error"}

Handler = Callable[[dict], Awaitable[Response]]


class UiServer:
    """routes: {(method, path): async handler(json_body) -> Response}."""

    def __init__(self, routes: dict[tuple[str, str], Handler], host: str = "127.0.0.1"):
        self.routes, self.host = routes, host
        self.port: int | None = None
        self._server: asyncio.AbstractServer | None = None

    async def start(self, port: int, tries: int = 10) -> int:
        """Listen on port, or the next free one (another copy may hold it). Returns the port."""
        last: OSError | None = None
        for p in range(port, port + tries):
            try:
                self._server = await asyncio.start_server(self._client, self.host, p)
                self.port = p
                return p
            except OSError as exc:
                last = exc
        raise OSError(f"no free port for the app window from {port}: {last}")

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def close(self) -> None:
        if self._server is not None:
            self._server.close()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            resp = await self._handle(reader)
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            resp = Response(400, "bad request")
        except Exception:
            log.exception("app window request failed")
            resp = Response(500, "error")
        head = (f"HTTP/1.1 {resp.status} {REASONS.get(resp.status, '')}\r\n"
                f"Content-Type: {resp.content_type}\r\nContent-Length: {len(resp.body)}\r\n"
                f"Cache-Control: no-store\r\nConnection: close\r\n\r\n")
        try:
            writer.write(head.encode("latin-1") + resp.body)
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    async def _handle(self, reader: asyncio.StreamReader) -> Response:
        request = (await reader.readline()).decode("latin-1").split()
        if len(request) < 2:
            return Response(400, "bad request")
        method, path = request[0], request[1].split("?")[0]
        headers = {}
        while True:
            line = (await reader.readline()).decode("latin-1").strip()
            if not line:
                break
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
        length = int(headers.get("content-length", "0") or 0)
        if length > MAX_BODY:
            return Response(413, "too large")
        body = await reader.readexactly(length) if length else b""
        handler = self.routes.get((method, path))
        if handler is None:
            known = any(p == path for _, p in self.routes)
            return Response(405 if known else 404, "not found")
        if method == "POST":
            if headers.get("x-engineer") != "1":
                return Response(403, "forbidden")
            data = json.loads(body or b"{}")
            if not isinstance(data, dict):
                return Response(400, "expected an object")
            return await handler(data)
        return await handler({})


def file_response(path: Path) -> Response:
    if not path.exists():
        return Response(404, "not found")
    return Response(200, path.read_bytes(), TYPES.get(path.suffix, "application/octet-stream"))
