"""UDP client for the Marstek Open API.

EN:
  * Every request gets its own global id 0..999 (wraps to 0). params.id (the
    "instance") counts with it.
  * Queue mode ON : requests are sent one after another. After each request the
    client waits for the answer (request_timeout), retries up to request_retries
    times and then waits request_delay before the next one.
    request_retries = 0 -> no watchdog. request_retries > 0 -> after all retries
    failed, the watchdog fires (Communication -> FAIL).
  * Queue mode OFF: requests are sent immediately one after another ("fire").
    Each request waits up to request_timeout for its id. No watchdog, ever.
  * Answers are matched by id. Answers arriving after the timeout ("late") are
    still processed, so data is never thrown away.
DE:
  * Jede Anfrage bekommt eine eigene globale ID 0..999 (danach wieder 0).
    params.id (Instance) zählt mit.
  * Queue EIN : Anfragen werden nacheinander gesendet, mit Timeout, Retries und
    request_delay dazwischen. Retries = 0 -> kein Watchdog, > 0 -> nach
    erfolglosen Retries Communication -> FAIL.
  * Queue AUS : Anfragen werden sofort nacheinander rausgeschickt, jede wartet
    bis request_timeout auf ihre ID. Nie FAIL.
  * Verspätete Antworten werden trotzdem verarbeitet.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from logger import get_logger
from marstek_api import ParseError, parse_datagram

_LOG = get_logger("udp")

ID_MODULO = 1000  # ids 0..999

Handler = Callable[[dict[str, Any], bool], Awaitable[None]]


class RequestTimeout(Exception):
    """No answer for a request within request_timeout (after all retries)."""


class DeviceError(Exception):
    """The device answered with an "error" object."""

    def __init__(self, code: Any, message: Any, data: Any = None) -> None:
        super().__init__(f"device error {code}: {message} ({data})")
        self.code, self.message, self.data = code, message, data


@dataclass
class CommSettings:
    queue_enabled: bool = True
    delay: float = 2.0
    timeout: float = 5.0
    retries: int = 0

    def describe(self) -> str:
        if self.queue_enabled:
            return (f"queue ON (delay {self.delay}s, timeout {self.timeout}s, "
                    f"retries {self.retries}{', watchdog' if self.retries else ', no watchdog'})")
        return f"queue OFF / fire mode (timeout {self.timeout}s, no watchdog)"


@dataclass
class Stats:
    sent: int = 0
    responses: int = 0
    timeouts: int = 0
    retries: int = 0
    late: int = 0
    duplicates: int = 0
    errors: int = 0
    parse_repairs: int = 0
    last_rtt_ms: float | None = None
    last_response: str | None = None
    queue_length: int = 0
    pending: int = 0

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Pending:
    req_id: int
    method: str
    payload: bytes
    handler: Handler | None
    future: asyncio.Future
    sent_at: float = 0.0
    sends: int = 0
    answers: int = 0
    timer: asyncio.TimerHandle | None = field(default=None, repr=False)


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, client: "UdpClient") -> None:
        self.client = client

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.client._on_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        _LOG.error("UDP socket error: %s", exc)


class UdpClient:
    def __init__(
        self,
        host: str,
        port: int,
        local_port: int,
        settings: CommSettings,
        on_alive: Callable[[], None],
        on_watchdog: Callable[[Pending], None],
        on_stats: Callable[[], None],
    ) -> None:
        self.host, self.port, self.local_port = host, port, local_port
        self.settings = settings
        self.stats = Stats()
        self._on_alive, self._on_watchdog, self._on_stats = on_alive, on_watchdog, on_stats
        self._next_id = 0
        self._history: dict[int, Pending] = {}
        self._queue: asyncio.Queue[Pending] = asyncio.Queue()
        self._transport: asyncio.DatagramTransport | None = None
        self._worker: asyncio.Task | None = None

    # ----------------------------------------------------------------- startup
    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self), local_addr=("0.0.0.0", self.local_port)
        )
        sock = self._transport.get_extra_info("sockname")
        _LOG.info("UDP socket bound to local port %s, device %s:%s", sock[1], self.host, self.port)
        _LOG.info("Communication: %s", self.settings.describe())
        self._worker = asyncio.create_task(self._queue_worker(), name="udp-queue")

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        for p in self._history.values():
            if p.timer:
                p.timer.cancel()
        if self._transport:
            self._transport.close()

    def apply_settings(self, settings: CommSettings) -> None:
        self.settings = settings
        _LOG.info("Communication settings applied: %s", settings.describe())
        if not settings.queue_enabled:
            # flush anything waiting in the queue straight out
            while not self._queue.empty():
                self._fire(self._queue.get_nowait())
            self._update_counts()

    # --------------------------------------------------------------- requests
    def next_id(self) -> int:
        rid = self._next_id
        self._next_id = (rid + 1) % ID_MODULO
        return rid

    def submit(
        self,
        method: str,
        params_factory: Callable[[int], dict[str, Any]],
        handler: Handler | None = None,
    ) -> asyncio.Future:
        """Queue (or fire) one request. Returns a future with the result dict."""
        rid = self.next_id()
        msg = {"id": rid, "method": method, "params": params_factory(rid)}
        payload = json.dumps(msg, separators=(",", ":")).encode()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        # avoid "exception was never retrieved" warnings for fire-and-forget calls
        fut.add_done_callback(lambda f: f.cancelled() or f.exception())
        old = self._history.get(rid)
        if old and old.timer:
            old.timer.cancel()
        p = Pending(rid, method, payload, handler, fut)
        self._history[rid] = p
        if self.settings.queue_enabled:
            self._queue.put_nowait(p)
            _LOG.debug("Queued   #%03d %s (queue length %d)", rid, method, self._queue.qsize())
        else:
            self._fire(p)
        self._update_counts()
        return fut

    def _send(self, p: Pending) -> None:
        if not self._transport:
            raise RuntimeError("UDP client not started")
        p.sends += 1
        p.sent_at = time.monotonic()
        self._transport.sendto(p.payload, (self.host, self.port))
        self.stats.sent += 1
        _LOG.debug("Sent     #%03d %s%s", p.req_id, p.method,
                   f" (attempt {p.sends})" if p.sends > 1 else "")
        _LOG.trace("UDP >> %s:%s %s", self.host, self.port, p.payload.decode())

    def _fire(self, p: Pending) -> None:
        self._send(p)
        p.timer = asyncio.get_running_loop().call_later(
            self.settings.timeout, self._fire_timeout, p
        )

    def _fire_timeout(self, p: Pending) -> None:
        p.timer = None
        if not p.future.done():
            self.stats.timeouts += 1
            _LOG.warning("Timeout  #%03d %s after %.1fs (fire mode, keeping previous data)",
                         p.req_id, p.method, self.settings.timeout)
            p.future.set_exception(RequestTimeout(p.method))
            self._update_counts()

    async def _queue_worker(self) -> None:
        while True:
            p = await self._queue.get()
            self._update_counts()
            s = self.settings
            if not s.queue_enabled:
                self._fire(p)
                continue
            attempts = 1 + max(0, s.retries)
            for attempt in range(attempts):
                if attempt:
                    self.stats.retries += 1
                    _LOG.warning("Retry    #%03d %s (%d/%d)", p.req_id, p.method, attempt, s.retries)
                self._send(p)
                try:
                    await asyncio.wait_for(asyncio.shield(p.future), s.timeout)
                    break
                except asyncio.TimeoutError:
                    self.stats.timeouts += 1
                    _LOG.warning("Timeout  #%03d %s after %.1fs", p.req_id, p.method, s.timeout)
                except Exception:  # noqa: BLE001  device error -> answered, stop
                    break
            else:
                if not p.future.done():
                    p.future.set_exception(RequestTimeout(p.method))
                if s.retries > 0:
                    self._on_watchdog(p)
            self._update_counts()
            if s.delay > 0:
                await asyncio.sleep(s.delay)

    # --------------------------------------------------------------- receiving
    def _on_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        _LOG.trace("UDP << %s:%s %s", addr[0], addr[1], data.decode(errors="replace"))
        if addr[0] != self.host:
            _LOG.debug("Ignoring datagram from %s (not the configured device)", addr[0])
            return
        try:
            msg, repaired = parse_datagram(data)
        except ParseError as err:
            _LOG.error("Unparsable answer from device: %s", err)
            return
        if repaired:
            self.stats.parse_repairs += 1
            _LOG.warning("Answer was not valid JSON and has been repaired: %s",
                         data.decode(errors="replace"))
        try:
            rid = int(msg.get("id"))
        except (TypeError, ValueError):
            _LOG.warning("Answer without usable id: %s", msg)
            return
        p = self._history.get(rid)
        if p is None:
            _LOG.warning("Answer for unknown id #%03d ignored: %s", rid, msg)
            return

        p.answers += 1
        self.stats.responses += 1
        self.stats.last_response = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rtt = (time.monotonic() - p.sent_at) * 1000.0
        self.stats.last_rtt_ms = round(rtt, 1)
        self._on_alive()

        if p.answers > 1:
            self.stats.duplicates += 1
            _LOG.debug("Duplicate answer #%03d %s ignored", rid, p.method)
            self._update_counts()
            return

        late = p.future.done()
        if late:
            self.stats.late += 1
            _LOG.warning("Late     #%03d %s answered after %.0f ms - processing anyway",
                         rid, p.method, rtt)
        if p.timer:
            p.timer.cancel()
            p.timer = None

        if "error" in msg:
            err = msg.get("error") or {}
            exc = DeviceError(err.get("code"), err.get("message"), err.get("data"))
            self.stats.errors += 1
            _LOG.error("Error    #%03d %s: %s", rid, p.method, exc)
            if not p.future.done():
                p.future.set_exception(exc)
            self._update_counts()
            return

        result = msg.get("result")
        if not isinstance(result, dict):
            result = {"value": result}
        _LOG.debug("Answer   #%03d %s in %.0f ms from %s", rid, p.method, rtt, msg.get("src"))
        if not p.future.done():
            p.future.set_result(result)
        if p.handler:
            asyncio.create_task(self._run_handler(p, result, late))
        self._update_counts()

    async def _run_handler(self, p: Pending, result: dict[str, Any], late: bool) -> None:
        try:
            await p.handler(result, late)  # type: ignore[misc]
        except Exception:  # noqa: BLE001
            _LOG.exception("Handler for #%03d %s failed", p.req_id, p.method)

    # ------------------------------------------------------------------ stats
    def _update_counts(self) -> None:
        self.stats.queue_length = self._queue.qsize()
        self.stats.pending = sum(
            1 for p in self._history.values() if p.sends and not p.future.done()
        )
        self._on_stats()
