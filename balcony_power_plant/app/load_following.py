"""Load following (Lastfolgeregelung) via ES.SetMode Passive.

EN:
  Goal: cover the household load with battery power while NEVER feeding into
  the grid. The grid power measured by a meter (e.g. Shelly, phase C) arrives
  event driven via MQTT (positive = import, negative = export).

  Decoupling: the meter sees  grid = load - battery output.  The regulator
  therefore reconstructs the load from the setpoint it has sent:
        load_est = grid_filtered + active_setpoint
        target   = load_est - target_grid_power
  so its own setpoint changes are not mistaken for load changes (no overshoot).
  The device is trusted to execute the setpoint (no read-back).

  Asymmetric:
    grid > deadband upper : regulate up   (average over avg_up s, max ramp step)
    grid < deadband lower : regulate down (average over avg_down s, immediately)
    in between            : idle, only keep-alive
  Setpoint < minimum power -> 0 W.  Upper limit = "Passive power" (Control).
  After every setpoint change the regulator waits settle_time.
  Keep-alive: the same setpoint is re-sent at keepalive_pct % of cd_time.

DE:
  Ziel: Hausverbrauch mit Batterieleistung decken, ohne jemals einzuspeisen.
  Der Netzwert (z. B. Shelly Phase C) kommt ereignisgesteuert per MQTT
  (positiv = Bezug, negativ = Einspeisung). Die Last wird aus dem gesendeten
  Sollwert rekonstruiert, damit eigene Sollwertänderungen nicht als
  Lastwechsel erscheinen. Hochregeln langsam (Mittelwert, Rampe), runterregeln
  sofort. Sollwert unter Minimum -> 0 W. Obergrenze = "Passive power".
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from logger import get_logger
from marstek_api import set_mode_params

if TYPE_CHECKING:
    from bridge import Bridge

_LOG = get_logger("loadfollow")

# states
OFF = "OFF"
WAITING = "WAITING"            # enabled, waiting for initialization / sensor
REGULATING = "REGULATING"      # setpoint changed, waiting for settle / averaging
IDLE = "IDLE"                  # grid inside deadband
LIMITED = "LIMITED"            # import above deadband but setpoint = upper limit
SENSOR_LOST = "SENSOR_LOST"
HOLD = "HOLD"                  # device dropout suspected: hold setpoint, wait for recovery
JITTER = "JITTER"              # keep-alive jitter test running
DEVICE_TEST = "DEVICE_TEST"    # device runs its own sensor test / reset: regulation frozen
STATES = [OFF, WAITING, REGULATING, IDLE, LIMITED, HOLD, JITTER, DEVICE_TEST, SENSOR_LOST]

EXPORT_CALCULATED = "Calculated"
EXPORT_MINIMUM = "Minimum"
EXPORT_REACTIONS = [EXPORT_CALCULATED, EXPORT_MINIMUM]

DROPOUT_UNDERSHOOT = 15  # W, the jump may be this much more BELOW the setpoint (device
                         # does not go exactly to 0 W - observed jump = setpoint - 10..15 W)
DROPOUT_MIN_REF = 3.0    # s of settled grid values needed as reference before a jump
JITTER_ZERO_MAX = 100    # W, jitter at 0 W: compensate the import (minus target), max. 100 W
OBSOLETE_ENTITIES = [("number", "lf_jitter_zero_w")]   # removed from HA on discovery
TEST_DEVIATION_WAIT = 30.0  # s, device test: no grid deviation within this time -> false alarm
TEST_EXPORT_MARGIN = 30.0  # W, export larger than setpoint + this is impossible by load
                           # changes -> the device feeds in on its own (sensor test / reset)
TEST_REVERT_WINDOW = 15.0  # s, guard starts this soon after our own change -> revert it
TEST_STABLE = 5.0        # s, device test: grid back at the pre-test level this long -> end
CD_TIME_GUARD = 10.0     # s, quiet HOLD: send a keep-alive anyway if cd_time ends sooner
CANDIDATE_TIME = 6.0     # s to confirm a partial jump as dropout (output ramping down)
REFERENCE_RETRY = 10     # s between reference reads (ES.GetStatus) at takeover
TICK = 1.0                 # s, timer for timeouts / keep-alive
SAMPLE_HISTORY = 120.0     # s of sensor samples kept
DROPOUT_MIN_STEP = 50      # W, only setpoint increases >= this are checked
DROPOUT_WINDOW = 3.0       # s after settle used for the plausibility check
DROPOUT_RATIO = 0.3        # grid must drop by at least 30 % of the step
DROPOUT_STRIKES = 2        # consecutive failed checks -> "suspected dropout"

# option key -> (HA name, min, max, step, unit, default)
NUMBERS: dict[str, tuple[str, float, float, float, str | None, float]] = {
    "lf_target_power":    ("Target grid power", -50, 200, 1, "W", 8),
    "lf_deadband_upper":  ("Deadband upper", -50, 300, 1, "W", 15),
    "lf_deadband_lower":  ("Deadband lower", -100, 200, 1, "W", 0),
    "lf_min_power":       ("Minimum power", 0, 400, 5, "W", 50),
    "lf_ramp_step":       ("Ramp up step", 10, 1200, 10, "W", 100),
    "lf_settle_time":     ("Settle time", 1, 60, 1, "s", 5),
    "lf_settle_per_100w": ("Settle time per 100 W", 0, 30, 0.5, "s", 5),
    "lf_avg_up":          ("Averaging time regulate up", 1, 60, 1, "s", 12),
    "lf_avg_down":        ("Averaging time regulate down", 1, 60, 1, "s", 5),
    "lf_cd_time":         ("Countdown cd_time", 30, 3600, 10, "s", 120),
    "lf_keepalive_pct":   ("Keepalive at", 10, 90, 5, "%", 50),
    "lf_sensor_timeout":  ("Sensor timeout", 5, 300, 5, "s", 30),
    "lf_dropout_tolerance":     ("Dropout tolerance", 0, 200, 1, "W", 15),
    "lf_dropout_tolerance_pct": ("Dropout tolerance percent", 0, 50, 1, "%", 10),
    "lf_dropout_hold":          ("Dropout hold time", 5, 120, 1, "s", 30),
    "lf_jitter_pct":            ("Jitter amount", 5, 90, 5, "%", 50),
    "lf_jitter_interval":       ("Jitter interval", 30, 3600, 10, "s", 120),
    "lf_device_test_hold":      ("Device test hold time", 30, 300, 5, "s", 75),
    "lf_resend_interval":       ("Resend interval", 0, 60, 1, "s", 5),
}

# option key -> (HA name, default)
SWITCHES: dict[str, tuple[str, bool]] = {
    "lf_dropout_detect": ("Dropout detection", True),
    "lf_dropout_resend": ("Dropout resend setpoint", True),
    "lf_jitter": ("Keepalive jitter", False),
    "lf_device_test_guard": ("Device test guard", True),
}


class LoadFollower:
    def __init__(self, bridge: "Bridge") -> None:
        self.b = bridge
        self.opts = bridge.opts
        self.samples: deque[tuple[float, float]] = deque()
        self.state = OFF
        self.setpoint: int | None = None        # last sent setpoint (trusted)
        self.last_change: float = 0.0           # monotonic time of last setpoint change
        self.last_send: float = 0.0             # monotonic time of last command (incl. keep-alive)
        self.settle_until: float = 0.0          # device ramps until then (scales with the step)
        self._last_dir = 0                      # direction of the last setpoint change (+1/-1)
        self.last_reason = "-"
        self.last_cmd_ts: str | None = None
        self.avg_up: float | None = None
        self.avg_down: float | None = None
        self.load_est: float | None = None
        self.target_sp: float | None = None
        self.in_export = False
        self.counters = {"commands": 0, "keepalives": 0, "export_events": 0,
                         "suspected_dropouts": 0, "sensor_values": 0, "invalid_values": 0,
                         "dropouts": 0, "dropout_recoveries": 0, "dropout_timeouts": 0,
                         "jitter_probes": 0, "jitter_followed": 0, "jitter_not_followed": 0,
                         "jitter_inconclusive": 0, "device_tests": 0, "resends": 0}
        # repeated resend during DEVICE_TEST / HOLD
        self._event_start: float | None = None
        self._event_resends = 0
        self._event_answered: float | None = None
        # device test guard
        self.test_start: float | None = None
        self.test_level: float | None = None
        self._test_deviated = False
        self._test_back_since: float | None = None
        self.last_device_test_s: float | None = None
        self._sp_before_change: int | None = None    # setpoint before the last change
        self.jitter: dict[str, Any] | None = None    # running keep-alive jitter test
        self._last_jitter = 0.0                      # monotonic start of the last jitter
        # dropout hold
        self.hold_start: float | None = None
        self.hold_level: float | None = None    # grid level before the jump
        self.last_dropout_s: float | None = None
        self._candidate_until: float | None = None
        self._last_dropout_at: float | None = None
        self.dropout_interval: int | None = None
        self._candidate_level: float | None = None
        self._expected_grid: float | None = None
        self._check: dict[str, Any] | None = None     # pending plausibility check
        self._strikes = 0
        self._dirty = True
        self._task: asyncio.Task | None = None
        self._last_invalid_log = 0.0
        # reference = current device output (ES.GetStatus ongrid_power, + = output)
        self.reference: float | None = None
        self._ref_pending = False
        self._ref_next_try = 0.0

    # ------------------------------------------------------------- options
    def o(self, key: str) -> float:
        try:
            return float(self.opts.get(key, NUMBERS[key][5]))
        except (TypeError, ValueError):
            return float(NUMBERS[key][5])

    @property
    def enabled(self) -> bool:
        return bool(self.opts.get("lf_enabled", False))

    @property
    def export_reaction(self) -> str:
        v = str(self.opts.get("lf_export_reaction", EXPORT_CALCULATED))
        return v if v in EXPORT_REACTIONS else EXPORT_CALCULATED

    @property
    def cap(self) -> int:
        """Upper limit = Passive power (Control), limited to max_power, >= 0."""
        try:
            p = int(self.opts.get("passive_power", 0))
        except (TypeError, ValueError):
            p = 0
        return max(0, min(p, self.opts.max_power))

    @property
    def regulating(self) -> bool:
        """True while a new setpoint is settling -> queries are held back."""
        return (self.enabled and self.setpoint is not None
                and time.monotonic() < self.settle_until)

    # ----------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="load-following")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(TICK)
            try:
                self.evaluate()
            except Exception:  # noqa: BLE001
                _LOG.exception("Load following evaluation failed")
            if self.enabled:
                self._dirty = True               # keep-alive countdown changes every tick
            if self._dirty and self.b.client is not None:
                self._dirty = False
                await self.b.publish(self.b.t_state("load_following"), self.snapshot())

    def reset(self) -> None:
        """Forget setpoint and reference (new initialization / switched on).
        The next takeover reads the device output first."""
        self.setpoint = None
        self.reference = None
        self._ref_pending = False
        self._ref_next_try = 0.0
        self._check, self._strikes = None, 0
        self.hold_start = self.hold_level = None
        self.jitter = None
        self.test_start = None
        self._last_jitter = time.monotonic()        # first jitter one interval after start
        self.state = WAITING if self.enabled else OFF
        self._dirty = True

    def set_enabled(self, on: bool, reason: str) -> None:
        if on == self.enabled:
            return
        self.opts.set("lf_enabled", on)
        self.reset()
        if on:
            _LOG.info("Load following ENABLED (%s) - reading ES.GetStatus as reference, "
                      "limit %d W", reason, self.cap)
            self.evaluate()
        else:
            # nothing is sent: without keep-alive the device falls back by itself
            # (usually to Auto) when cd_time expires
            _LOG.info("Load following DISABLED (%s) - no more keep-alive, device falls back "
                      "after cd_time", reason)
        self._dirty = True

    # ------------------------------------------------------------ reference
    def set_reference(self, ongrid_power: Any, source: str) -> None:
        """Current device output from ES.GetStatus (ongrid_power, positive = output)."""
        try:
            value = float(ongrid_power)
        except (TypeError, ValueError):
            _LOG.warning("Reference from %s unusable: ongrid_power=%r", source, ongrid_power)
            return
        self.reference = value
        self._ref_pending = False
        _LOG.info("Reference from %s: device output (ongrid_power) %.0f W", source, value)
        self._dirty = True

    def _request_reference(self, now: float) -> None:
        if self._ref_pending or now < self._ref_next_try:
            return
        self._ref_pending = True
        _LOG.info("Reading ES.GetStatus as reference for the takeover")
        from marstek_api import QUERIES, query_params   # local import, avoids a cycle
        q = QUERIES["es_status"]
        handler = self.b._query_handler(q) if self.b.query_enabled("es_status") else None
        fut = self.b.udp.submit("ES.GetStatus", lambda i: query_params(q, i, ""),
                                handler=handler, priority=True)

        def done(f: asyncio.Future) -> None:
            if f.cancelled():
                self._ref_pending = False
                return
            if f.exception() is not None:
                self._ref_pending = False
                self._ref_next_try = time.monotonic() + REFERENCE_RETRY
                _LOG.warning("Reference read failed (%s) - next try in %d s",
                             f.exception().__class__.__name__, REFERENCE_RETRY)
                return
            self.set_reference(f.result().get("ongrid_power"), "ES.GetStatus")
            self.evaluate()
        fut.add_done_callback(done)

    # -------------------------------------------------------------- sensor
    def on_sensor(self, payload: str) -> None:
        value = self._parse(payload)
        now = time.monotonic()
        if value is None:
            self.counters["invalid_values"] += 1
            if now - self._last_invalid_log > 60:
                self._last_invalid_log = now
                _LOG.warning("Grid sensor: invalid payload %r ignored", payload)
            return
        self.samples.append((now, value))
        # keep the newest sample older than the history: it is the held value
        while len(self.samples) > 1 and now - self.samples[1][0] > SAMPLE_HISTORY:
            self.samples.popleft()
        self.counters["sensor_values"] += 1
        _LOG.trace("Grid sensor %.0f W", value)
        self._dirty = True
        if self.enabled:
            self.evaluate()            # event driven

    @staticmethod
    def _parse(payload: str) -> float | None:
        text = payload.strip()
        try:
            return float(text)
        except ValueError:
            pass
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(data, (int, float)) and not isinstance(data, bool):
            return float(data)
        if isinstance(data, dict):
            for key in ("power", "value", "act_power", "apower", "state"):
                try:
                    return float(data[key])
                except (KeyError, TypeError, ValueError):
                    continue
        return None

    def _integrate(self, t0: float, t1: float) -> float | None:
        """Time weighted average of the grid power between t0 and t1. A value is
        held until the next one arrives (sample and hold), so a sensor that only
        reports changes still gives a correct average."""
        held: float | None = None
        seg_start = t0
        area = 0.0
        begin = None
        for t, v in self.samples:
            if t <= t0:
                held = v
                continue
            if t >= t1:
                break
            if held is not None:
                area += held * (t - seg_start)
                if begin is None:
                    begin = seg_start
            seg_start = t
            held = v
        if held is None:
            return None
        area += held * (t1 - seg_start)
        if begin is None:
            begin = seg_start
        return area / (t1 - begin) if t1 > begin else held

    def _avg(self, window: float, since: float, now: float) -> float | None:
        """Time weighted average over the last `window` s. None if the window
        starts before `since` (still settling)."""
        start = now - window
        if start < since:
            return None
        return self._integrate(start, now)

    # ---------------------------------------------------------- regulation
    def evaluate(self) -> None:
        if not self.enabled:
            if self.state != OFF:
                self.state, self._dirty = OFF, True
            return
        now = time.monotonic()
        if not self.b.initialized:
            self._set_state(WAITING)
            return

        # --- sensor watchdog ----------------------------------------------
        timeout = self.o("lf_sensor_timeout")
        if not self.samples or now - self.samples[-1][0] > timeout:
            if self.state != SENSOR_LOST and self.samples:
                _LOG.error("Grid sensor: no value for %.0f s - falling back to minimum power",
                           timeout)
            if self.setpoint is not None:
                self._set_state(SENSOR_LOST)
                fallback = min(int(self.o("lf_min_power")), self.cap)
                if self.setpoint != fallback:
                    self._send(fallback, "sensor lost", change=True)
                self._keepalive(now)
            else:
                self._set_state(WAITING)
            return

        if self.setpoint is None:                  # takeover (start / switched on)
            if self.reference is None:
                self._set_state(WAITING)
                self._request_reference(now)
                return
            value = int(round(max(0.0, min(self.reference, float(self.cap)))))
            _LOG.info("Takeover: device output %.0f W -> Passive %d W (limit %d W)",
                      self.reference, value, self.cap)
            self._send(value, f"takeover {self.reference:.0f} W", change=True)
            self._set_state(REGULATING)
            return

        # --- device runs its own sensor test / reset: freeze -------------------
        if self.test_start is not None and self._device_test_step(now):
            return

        # --- upper limit lowered (Passive power from HA) ------------------
        cap = self.cap
        if self.jitter is not None and self.jitter["base"] > cap:
            self.jitter = None                    # limit lowered: no return to the old base
        jitter_up = self.jitter is not None and self.jitter["dir"] > 0
        if self.setpoint > cap and not jitter_up:
            self._send(cap, f"limit {cap} W", change=True)
            self._set_state(REGULATING)
            return

        if self.jitter is not None and self._jitter_step(now):
            return

        settle_end = self.settle_until
        self._plausibility(now, settle_end)
        if now < settle_end:
            # still ramping: no regulating up - but after an UP step an export can only
            # come from a real load drop -> react immediately (export protection)
            if self._last_dir > 0 and self._settle_export(now):
                return
            self._set_state(REGULATING)
            self._keepalive(now)
            return

        self.avg_up = self._avg(self.o("lf_avg_up"), settle_end, now)
        self.avg_down = self._avg(self.o("lf_avg_down"), settle_end, now)
        upper, lower = self.o("lf_deadband_upper"), self.o("lf_deadband_lower")
        target = self.o("lf_target_power")
        min_p = int(self.o("lf_min_power"))

        # --- dropout hold -----------------------------------------------------
        if self.hold_start is not None:
            if self._hold(now, lower):
                return
        elif self._detect_dropout(now, settle_end):
            return

        # --- export larger than our setpoint: the device acts on its own -------
        latest_raw = self.samples[-1][1]
        if (self.sw("lf_device_test_guard") and self.test_start is None
                and latest_raw < -(self.setpoint + TEST_EXPORT_MARGIN)):
            self.device_test_start(f"export {latest_raw:+.0f} W larger than setpoint "
                                   f"{self.setpoint} W")
            if self.test_start is not None:
                return

        # --- regulate down (export side, fast) ----------------------------
        if self.avg_down is not None and self.avg_down < lower:
            latest = self.samples[-1][1]
            grid = min(self.avg_down, latest)      # err on the safe side (more import)
            self.load_est = grid + self.setpoint
            self.target_sp = self.load_est - target
            if grid < 0 and not self.in_export:
                self.in_export = True
                self.counters["export_events"] += 1
                _LOG.warning("Export detected: %.0f W (avg %.1f s)", grid, self.o("lf_avg_down"))
            if self.export_reaction == EXPORT_MINIMUM and grid < 0:
                new = min_p
            else:
                new = int(round(self.target_sp))
            new = self._limit(new, min_p, cap)
            if new < self.setpoint:
                self._send(new, f"down {grid:+.0f} W", change=True)
                self._set_state(REGULATING)
                return
            self._set_state(IDLE)              # already at 0 / nothing lower possible
            self._keepalive(now)
            return
        if self.avg_down is not None and self.avg_down >= 0:
            self.in_export = False

        # --- regulate up (import side, slow + ramp) -----------------------
        if self.avg_up is not None and self.avg_up > upper:
            self.load_est = self.avg_up + self.setpoint
            self.target_sp = self.load_est - target
            new = self._limit(int(round(self.target_sp)), min_p, cap)
            new = min(new, self.setpoint + int(self.o("lf_ramp_step")))
            if new > self.setpoint:
                self._start_check(new - self.setpoint, self.avg_up, now)
                self._send(new, f"up {self.avg_up:+.0f} W", change=True)
                self._set_state(REGULATING)
                return
            self._set_state(LIMITED if self.setpoint >= cap and cap > 0 else IDLE)
            self._keepalive(now)
            return

        # --- inside deadband ------------------------------------------------
        if self.avg_up is not None:
            self.load_est = self.avg_up + self.setpoint
            self.target_sp = self.load_est - target
        self._set_state(IDLE if self.avg_up is not None else REGULATING)
        self._keepalive(now)

    # --------------------------------------------------- device test guard
    def device_test_start(self, reason: str) -> None:
        """Called when the device answers ES.SetMode very late / not at all: it is
        about to run its own sensor test (±several hundred W) or to reset. Our
        commands are ignored then anyway and reacting would only cause a step
        afterwards (e.g. 0 W -> import). So: freeze everything until it is over."""
        if (not self.enabled or not self.sw("lf_device_test_guard") or self.setpoint is None
                or not self.samples):
            return
        now = time.monotonic()
        if self.test_start is not None:
            return
        t_last = self.samples[-1][0]
        level = self._integrate(t_last - 12.0, t_last - 2.0)   # before the slow answer
        self.test_start = now
        self.test_level = level if level is not None else self.samples[-1][1]
        self._test_deviated = False
        self._test_back_since = None
        self.jitter = None
        from_hold = self.hold_start is not None
        self.hold_start = self.hold_level = None
        self._candidate_until = None
        self.counters["device_tests"] += 1
        if not (from_hold and self._event_start is not None):
            self._event_begin(now)      # HOLD -> DEVICE_TEST keeps the event start
        if (now - self.last_change < TEST_REVERT_WINDOW and self._sp_before_change is not None
                and self._sp_before_change != self.setpoint):
            # our last change was a reaction to the test itself -> undo it
            _LOG.info("Device test guard: reverting our last change %s W -> %s W",
                      self.setpoint, self._sp_before_change)
            self._send(int(self._sp_before_change), "device test revert", change=False)
        _LOG.warning("Device test guard: %s - regulation frozen (grid level before %+.0f W, "
                     "setpoint %s W, max. %.0f s)", reason, self.test_level, self.setpoint,
                     self.o("lf_device_test_hold"))
        self._set_state(DEVICE_TEST)
        self._dirty = True

    def _device_test_step(self, now: float) -> bool:
        """Returns True while the device test is running (nothing is regulated)."""
        assert self.test_start is not None and self.test_level is not None
        elapsed = now - self.test_start
        latest = self.samples[-1][1]
        tol = max(25.0, self._tolerance())
        back = abs(latest - self.test_level) <= tol
        if not back:
            self._test_deviated = True
            self._test_back_since = None
        elif self._test_back_since is None:
            self._test_back_since = now
        end_reason = None
        if self._test_deviated and self._test_back_since is not None \
                and now - self._test_back_since >= TEST_STABLE:
            end_reason = "grid back at the level before"
        elif not self._test_deviated and elapsed >= TEST_DEVIATION_WAIT:
            end_reason = "no deviation (false alarm)"
        elif elapsed >= self.o("lf_device_test_hold"):
            end_reason = "hold time over"
        if end_reason:
            self.last_device_test_s = round(elapsed, 1)
            _LOG.info("Device test over after %.0f s (%s, grid %+.0f W%s) - regulating again",
                      elapsed, end_reason, latest, self._event_summary(now))
            self._event_start = None
            self.test_start = self.test_level = None
            self.last_change = now                # settle + fresh averaging windows
            self.settle_until = now + self.o("lf_settle_time")
            self._set_state(REGULATING)
            self._dirty = True
            return True
        self._set_state(DEVICE_TEST)
        if self._resend(now, "device test resend"):
            return True
        if now - self.last_send >= self.o("lf_cd_time") - CD_TIME_GUARD and self.setpoint is not None:
            self.counters["keepalives"] += 1
            self._send(self.setpoint, "keepalive (cd_time guard)", change=False)
        return True

    # ------------------------------------------------------ repeated resend
    def _event_begin(self, now: float) -> None:
        self._event_start, self._event_resends, self._event_answered = now, 0, None

    def _event_summary(self, now: float) -> str:
        if self._event_start is None:
            return ""
        ans = ("not answered" if self._event_answered is None
               else f"device answered again after {self._event_answered - self._event_start:.0f} s")
        return f", {self._event_resends} resends, {ans}"

    def _resend(self, now: float, reason: str, force: bool = False) -> bool:
        """Send the held setpoint again every lf_resend_interval s (0 = off).
        The device seems to resume its output only after a SetMode it answers."""
        interval = self.o("lf_resend_interval")
        if self.setpoint is None:
            return False
        if not force and (interval <= 0 or now - self.last_send < interval):
            return False
        self._event_resends += 1
        self.counters["resends"] += 1

        def answered() -> None:
            if self._event_start is not None and self._event_answered is None:
                self._event_answered = time.monotonic()
                _LOG.info("Device answers again %.0f s after the start of the event - output should "
                          "follow within its soft start", self._event_answered - self._event_start)
        self._send(self.setpoint, reason, change=False, on_answer=answered)
        return True

    # ---------------------------------------------------- keep-alive jitter
    def _start_jitter(self, now: float) -> None:
        """Keep-alive as a probe. Setpoint > 0 (also at the limit): lower it by
        lf_jitter_pct %. Setpoint 0 W: raise it so the import is compensated down to the
        target (no export), max. JITTER_ZERO_MAX W, even if the limit is 0.
        Then check that the device follows and return to the previous setpoint."""
        base = int(self.setpoint or 0)
        before = self._integrate(now - self.o("lf_avg_up"), now)
        if before is None:
            before = self.samples[-1][1]
        if base > 0:
            direction = -1
            amount = int(round(base * self.o("lf_jitter_pct") / 100.0))
        else:
            direction = 1
            room = before - self.o("lf_target_power")
            amount = int(min(JITTER_ZERO_MAX, max(0.0, room)))
            if amount < 10:
                _LOG.debug("Jitter at 0 W skipped - import %.0f W too small", before)
                amount = 0
        if amount <= 0:
            return                                   # nothing to probe now
        self.jitter = {"phase": "down", "dir": direction, "base": base, "amount": amount,
                       "before": before, "t": now}
        self.counters["jitter_probes"] += 1
        self._set_state(JITTER)
        sign = "+" if direction > 0 else "-"
        self._send(base + direction * amount, f"jitter {sign}{amount} W", change=True,
                   count=False)
        self.jitter["su"] = self.settle_until

    def _jitter_step(self, now: float) -> bool:
        """Advance the running jitter test. Returns True while it is running."""
        j = self.jitter
        assert j is not None
        avg = self.o("lf_avg_up")
        su = j.get("su", j["t"] + self.o("lf_settle_time"))     # end of settling this phase
        # export protection stays active (fast average)
        avg_down = self._integrate(now - self.o("lf_avg_down"), now)
        if (j["phase"] == "down" and now >= su and avg_down is not None
                and avg_down < self.o("lf_deadband_lower")):
            _LOG.warning("Export during jitter (%+.0f W) - jitter aborted", avg_down)
            self.jitter = None
            return False
        if j["phase"] == "down":
            if now >= su and self._detect_dropout(now, su):
                _LOG.info("Jitter aborted - dropout during jitter")
                self.counters["jitter_inconclusive"] += 1
                self.jitter = None
                return True
            if now < su + avg:
                self._set_state(JITTER)
                return True
            measured = self._integrate(su, now)
            # expected grid change: +amount (output lowered) or -amount (output raised)
            rise = ((measured or 0.0) - j["before"]) * (-j["dir"])
            amt = j["amount"]
            if rise < 0.5 * amt:
                result, key = "NOT followed", "jitter_not_followed"
            elif rise <= 1.5 * amt + 15:
                result, key = "followed", "jitter_followed"
            else:
                result, key = "inconclusive (load change or dropout)", "jitter_inconclusive"
            self.counters[key] += 1
            log = _LOG.warning if key == "jitter_not_followed" else _LOG.info
            log("Jitter %s%d W: grid %+.0f -> %+.0f W (expected change %+d W, seen %+.0f W)"
                " - device %s", "+" if j["dir"] > 0 else "-", amt, j["before"],
                measured or 0.0, -j["dir"] * amt, (measured or 0.0) - j["before"], result)
            j["phase"], j["t"] = "return", now
            self._send(j["base"], "jitter return", change=True, count=False)
            j["su"] = self.settle_until
            self._set_state(JITTER)
            return True
        # phase "return": wait for the settle time, then normal operation
        if now < su:
            self._set_state(JITTER)
            return True
        self.jitter = None
        self.settle_until = now                  # averaging windows restart now
        self._set_state(REGULATING)
        return True

    # ------------------------------------------------------- dropout hold
    def sw(self, key: str) -> bool:
        return bool(self.opts.get(key, SWITCHES[key][1]))

    def _tolerance(self) -> float:
        return max(self.o("lf_dropout_tolerance"),
                   (self.setpoint or 0) * self.o("lf_dropout_tolerance_pct") / 100.0)

    def _detect_dropout(self, now: float, settle_end: float) -> bool:
        """Device fell out of regulation: the grid power jumps up by about the
        active setpoint while nothing was commanded. Returns True if HOLD starts."""
        if not self.sw("lf_dropout_detect") or self.hold_start is not None:
            return False
        sp = self.setpoint or 0
        tol = self._tolerance()
        if sp <= tol or len(self.samples) < 2:
            return False
        t_last, latest = self.samples[-1]
        # reference level: up to 10 s before the last sample, but only after settling;
        # at least DROPOUT_MIN_REF s are needed
        before_start = max(t_last - 10.0, settle_end)
        if t_last - before_start < DROPOUT_MIN_REF:
            return False
        before = self._integrate(before_start, t_last)
        # shortly after settling the reference window may already contain the jump
        # (dropout right at the end of the settle time) -> also use the grid level
        # expected from the last setpoint change
        if (before is not None and self._expected_grid is not None
                and t_last - settle_end < 10.0):
            before = min(before, self._expected_grid + DROPOUT_UNDERSHOOT)
        if self._candidate_until is not None and self._candidate_level is not None:
            before = self._candidate_level      # compare with the level before the ramp
        if before is None:
            return False
        jump = latest - before
        low, high = sp - tol - DROPOUT_UNDERSHOOT, sp + tol      # asymmetric band
        if not low <= jump <= high:
            # partial jump towards the setpoint (output still ramping down):
            # wait up to CANDIDATE_TIME for confirmation before regulating up
            if 0.3 * sp <= jump < low:
                if self._candidate_until is None:
                    self._candidate_until = now + CANDIDATE_TIME
                    self._candidate_level = before
                    _LOG.debug("Possible dropout: jump %+.0f W of %d W - waiting", jump, sp)
                if now < self._candidate_until:
                    return True
            self._candidate_until = None
            return False
        self._candidate_until = None
        self.hold_start, self.hold_level = now, before
        self._event_begin(now)
        self.counters["dropouts"] += 1
        interval = None if self._last_dropout_at is None else now - self._last_dropout_at
        self._last_dropout_at = now
        self.dropout_interval = None if interval is None else round(interval)
        _LOG.warning(
            "Dropout detected: grid %+.0f -> %+.0f W (jump %+.0f W, setpoint %d W, band %.0f..%.0f W)"
            " - %.0f s after last command, %.0f s after last setpoint change, %s since last dropout"
            " - HOLD",
            before, latest, jump, sp, low, high, now - self.last_send, now - self.last_change,
            "-" if interval is None else f"{interval:.0f} s")
        self._set_state(HOLD)
        self._dirty = True
        if self.sw("lf_dropout_resend"):
            self._resend(now, "dropout resend", force=True)
        return True

    def _hold(self, now: float, lower: float) -> bool:
        """In HOLD: wait for the device to come back. Returns True while holding."""
        assert self.hold_start is not None and self.hold_level is not None
        latest = self.samples[-1][1]
        held_for = now - self.hold_start
        if abs(latest - self.hold_level) <= self._tolerance():
            self.last_dropout_s = round(held_for, 1)
            self.counters["dropout_recoveries"] += 1
            _LOG.info("Dropout recovered after %.0f s (grid %+.0f W)%s%s - restarting averaging",
                      held_for, latest,
                      "" if self.sw("lf_dropout_resend") else " by itself (quiet hold)",
                      self._event_summary(now))
            self._event_start = None
            self.hold_start = self.hold_level = None
            self.last_change = now            # settle + fresh averaging windows
            self.settle_until = now + self.o("lf_settle_time")
            self._set_state(IDLE)
            self._dirty = True
            return True
        if held_for >= self.o("lf_dropout_hold"):
            self.last_dropout_s = round(held_for, 1)
            self.counters["dropout_timeouts"] += 1
            _LOG.warning("Dropout hold timed out after %.0f s%s - treating it as a real load "
                         "change, regulating normally", held_for, self._event_summary(now))
            self._event_start = None
            self.hold_start = self.hold_level = None
            self._dirty = True
            if not self.sw("lf_dropout_resend") and self.setpoint:
                # quiet hold: nothing was sent - wake the device with the setpoint once,
                # then settle and average fresh before regulating (no overshoot on wake-up)
                # -1 W: a changed value is more likely to be processed than an identical one
                self._send(max(0, self.setpoint - 1), "hold timeout resend (-1 W)",
                           change=True, count=False)
                self.settle_until = now + self.settle_for(self.setpoint)   # ramp 0 -> sp
                self._set_state(REGULATING)
                return True
            return False
        avg_down = self._integrate(now - self.o("lf_avg_down"), now)
        if avg_down is not None and avg_down < lower:
            _LOG.warning("Export during HOLD (%+.0f W) - leaving HOLD, regulating down", avg_down)
            self.hold_start = self.hold_level = None
            return False
        self._set_state(HOLD)
        if self.sw("lf_dropout_resend"):
            if not self._resend(now, "dropout resend (repeat)"):
                self._keepalive(now)
        elif now - self.last_send >= self.o("lf_cd_time") - CD_TIME_GUARD and self.setpoint is not None:
            # quiet hold: only send if cd_time would otherwise expire
            self.counters["keepalives"] += 1
            self._send(self.setpoint, "keepalive (cd_time guard)", change=False)
        return True

    def settle_for(self, delta: float) -> float:
        """Settle time for a setpoint step: the device ramps, bigger steps take longer."""
        return self.o("lf_settle_time") + self.o("lf_settle_per_100w") * abs(delta) / 100.0

    def _settle_export(self, now: float) -> bool:
        """Export while the device is still ramping up after an UP step."""
        start = max(now - self.o("lf_avg_down"), self.last_change + 1.0)
        if now - start < 1.5:
            return False
        avg = self._integrate(start, now)
        if avg is None or avg >= self.o("lf_deadband_lower"):
            return False
        grid = min(avg, self.samples[-1][1])
        self.load_est = grid + (self.setpoint or 0)
        self.target_sp = self.load_est - self.o("lf_target_power")
        new = self._limit(int(round(self.target_sp)), int(self.o("lf_min_power")), self.cap)
        if new >= (self.setpoint or 0):
            return False
        if grid < 0 and not self.in_export:
            self.in_export = True
            self.counters["export_events"] += 1
        _LOG.warning("Export %+.0f W while ramping up - regulating down immediately", grid)
        self._send(new, f"down {grid:+.0f} W (during ramp)", change=True)
        self._set_state(REGULATING)
        return True

    @staticmethod
    def _limit(value: int, min_p: int, cap: int) -> int:
        if value < min_p:
            return 0                     # below minimum power -> 0 W
        return max(0, min(value, cap))

    def _set_state(self, state: str) -> None:
        if state != self.state:
            _LOG.debug("State %s -> %s", self.state, state)
            self.state = state
            self._dirty = True

    def _keepalive(self, now: float) -> None:
        if self.setpoint is None:
            return
        # jitter test: own interval (lf_jitter_interval), independent of the keep-alive;
        # its commands refresh cd_time as well
        if (self.sw("lf_jitter") and self.state in (IDLE, LIMITED)
                and self.hold_start is None and self.jitter is None
                and now - self._last_jitter >= self.o("lf_jitter_interval")):
            self._last_jitter = now
            self._start_jitter(now)
            if self.jitter is not None:
                return
        interval = self.o("lf_cd_time") * self.o("lf_keepalive_pct") / 100.0
        if now - self.last_send >= interval:
            self.counters["keepalives"] += 1
            self._send(self.setpoint, "keepalive", change=False)

    def keepalive_in(self) -> float | None:
        if self.setpoint is None or not self.enabled:
            return None
        interval = self.o("lf_cd_time") * self.o("lf_keepalive_pct") / 100.0
        return max(0.0, round(interval - (time.monotonic() - self.last_send), 1))

    def _send(self, power: int, reason: str, change: bool, count: bool = True,
              on_answer=None) -> None:
        cd_time = int(self.o("lf_cd_time"))
        now = time.monotonic()
        old = self.setpoint
        self.setpoint = power
        self.last_send = now
        if change:
            if old != power:
                self._sp_before_change = old
            # expected grid level once the device has followed this change
            # (reference for the dropout detection right after settling)
            if self.samples:
                self._expected_grid = self.samples[-1][1] + (old or 0) - power
            delta = power - (old or 0)
            self._last_dir = (delta > 0) - (delta < 0)
            self.settle_until = now + self.settle_for(delta)
            self.last_change = now
            if count:
                self.counters["commands"] += 1
        self.last_reason = reason
        self.last_cmd_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._dirty = True
        log = _LOG.debug if reason == "keepalive" else _LOG.info
        log("Setpoint %s -> %d W (%s, cd_time %d s)",
            "-" if old is None else f"{old} W", power, reason, cd_time)

        async def handler(result: dict[str, Any], late: bool) -> None:
            if str(result.get("set_result")).lower() not in ("true", "1", "ture"):
                _LOG.error("Setpoint %d W rejected by device: %s", power, result)
            elif on_answer is not None:
                on_answer()

        self.b.udp.submit(
            "ES.SetMode",
            lambda i: set_mode_params(i, "Passive", power, cd_time),
            handler=handler, priority=True, coalesce="lf_setpoint",
        )

    # ------------------------------------------- plausibility (log only)
    def _start_check(self, step: int, grid_before: float, now: float) -> None:
        if step >= DROPOUT_MIN_STEP:
            self._check = {"step": step, "before": grid_before, "t": now}
        else:
            self._check = None

    def _plausibility(self, now: float, settle_end: float) -> None:
        c = self._check
        if not c:
            return
        if self.last_change - c["t"] > 0.5:     # another setpoint change since -> discard
            self._check = None
            return
        if now < settle_end + DROPOUT_WINDOW:
            return
        self._check = None
        vals = [v for t, v in self.samples if settle_end <= t <= settle_end + DROPOUT_WINDOW]
        if not vals:
            return
        after = sum(vals) / len(vals)
        drop = c["before"] - after
        if drop < DROPOUT_RATIO * c["step"]:
            self._strikes += 1
            _LOG.debug("Plausibility: setpoint +%d W, grid only %.0f -> %.0f W (strike %d)",
                       c["step"], c["before"], after, self._strikes)
            if self._strikes >= DROPOUT_STRIKES:
                self.counters["suspected_dropouts"] += 1
                _LOG.warning(
                    "Suspected dropout: device did not follow the last %d setpoint increases "
                    "(last +%d W, grid %.0f -> %.0f W, setpoint %d W, commands %d, keepalives %d)"
                    " - logged only, no action",
                    self._strikes, c["step"], c["before"], after, self.setpoint or 0,
                    self.counters["commands"], self.counters["keepalives"])
                self._strikes = 0
                self._dirty = True
        else:
            self._strikes = 0

    # ---------------------------------------------------------------- state
    def snapshot(self) -> dict[str, Any]:
        def r(v: float | None) -> float | None:
            return None if v is None else round(v, 1)
        return {
            "state": self.state,
            "grid_raw": r(self.samples[-1][1]) if self.samples else None,
            "grid_avg_up": r(self.avg_up),
            "grid_avg_down": r(self.avg_down),
            "load_estimated": r(self.load_est),
            "setpoint": self.setpoint,
            "setpoint_target": r(self.target_sp),
            "power_limit": self.cap,
            "reference": None if self.reference is None else round(self.reference),
            "hold_for": None if self.hold_start is None else round(time.monotonic() - self.hold_start),
            "last_dropout_duration": self.last_dropout_s,
            "dropout_interval": self.dropout_interval,
            "jitter_phase": None if self.jitter is None else self.jitter["phase"],
            "device_test_for": (None if self.test_start is None
                                else round(time.monotonic() - self.test_start)),
            "last_device_test_duration": self.last_device_test_s,
            "jitter_in": (None if not self.sw("lf_jitter") or self.setpoint is None else
                          max(0, round(self.o("lf_jitter_interval")
                                       - (time.monotonic() - self._last_jitter)))),
            "last_reason": self.last_reason,
            "last_command": self.last_cmd_ts,
            "keepalive_in": self.keepalive_in(),
            **self.counters,
        }
