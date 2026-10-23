"""UDP listener: receives F1 25 packets, stamps arrival time, fans raw bytes out to subscribers.

Subscribers are plain functions `fn(raw: bytes, t_ns: int)`. Today those are the recorder and the
pipeline (parser -> race state -> events). They run on the asyncio loop, so they must be quick.

Forwarding: every packet can be re-sent unchanged to other host:port targets (e.g. SimHub), so the
game only needs to send to us. We use a separate socket for forwarding: on Windows, a UDP send to a
port nobody listens on can make the *receiving* socket report errors, and we never want forwarding
problems to interrupt telemetry.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from typing import Callable, Iterable

log = logging.getLogger(__name__)

Subscriber = Callable[[bytes, int], None]

RECV_BUFFER_BYTES = 4 * 1024 * 1024  # room for bursts of packets sent on the same frame


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, listener: "UdpListener"):
        self.listener = listener

    def datagram_received(self, data: bytes, addr) -> None:
        self.listener._on_packet(data, time.time_ns())

    def error_received(self, exc: Exception) -> None:
        log.warning("UDP receive error (ignored): %s", exc)


class UdpListener:
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 20777,
        forward: Iterable[tuple[str, int]] = (),
    ):
        self.host = host
        self.port = port
        self.forward_targets = [(h, int(p)) for h, p in forward]
        self.subscribers: list[Subscriber] = []
        self.packets_received = 0
        self.subscriber_errors = 0
        self._transport: asyncio.DatagramTransport | None = None
        self._forward_sock: socket.socket | None = None

    def subscribe(self, fn: Subscriber) -> None:
        self.subscribers.append(fn)

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RECV_BUFFER_BYTES)
        except OSError:
            pass  # not fatal; the OS default buffer still works at 60 Hz
        sock.bind((self.host, self.port))
        sock.setblocking(False)
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self), sock=sock
        )
        if self.forward_targets:
            self._forward_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._forward_sock.setblocking(False)
        log.info("Listening for F1 25 telemetry on %s:%d", self.host, self.port)
        for h, p in self.forward_targets:
            log.info("Forwarding packets to %s:%d", h, p)

    @property
    def bound_port(self) -> int:
        """Actual port (useful when started with port 0 in tests)."""
        assert self._transport is not None
        return self._transport.get_extra_info("sockname")[1]

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        if self._forward_sock is not None:
            self._forward_sock.close()
            self._forward_sock = None

    def _on_packet(self, data: bytes, t_ns: int) -> None:
        self.packets_received += 1
        if self._forward_sock is not None:
            for target in self.forward_targets:
                try:
                    self._forward_sock.sendto(data, target)
                except OSError as exc:  # target not running, buffer full, etc.
                    log.debug("forward to %s failed: %s", target, exc)
        for fn in self.subscribers:
            try:
                fn(data, t_ns)
            except Exception:
                # One bad packet or a bug in one subscriber must not stop the others or the loop.
                self.subscriber_errors += 1
                if self.subscriber_errors <= 10:  # don't flood the console at 60 Hz
                    log.exception("subscriber %r failed", fn)
