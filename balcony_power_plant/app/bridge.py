"""MQTT side of the bridge: discovery, states and command handling.

EN: Creates the Home Assistant device "BalconyPowerPlant" via MQTT discovery,
    publishes the device answers and executes commands coming from HA.
DE: Legt das HA-Gerät "BalconyPowerPlant" per MQTT-Discovery an, veröffentlicht
    die Antworten des Geräts und führt die Befehle aus HA aus.

Topics (B = mqtt_base_topic):
    B/availability             online / offline (LWT)
    B/state/<query>            JSON result of a query (retained)
    B/state/communication      INIT / ON / FAIL
    B/state/stats              request statistics (JSON)
    B/control/<name>           current value of a control entity
    B/cmd/<name>               commands from HA
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiomqtt

from config import OptionsStore
from load_following import (EXPORT_REACTIONS, NUMBERS as LF_NUMBERS, OBSOLETE_ENTITIES,
                             STATES as LF_STATES, SWITCHES as LF_SWITCHES, LoadFollower)
from logger import get_logger, set_level
from marstek_api import (
    MODES,
    QUERIES,
    Query,
    ble_adv_params,
    dod_params,
    led_params,
    normalize,
    query_params,
    set_mode_params,
)
from udp_client import CommSettings, DeviceError, Pending, RequestTimeout, UdpClient
from version import VERSION

_LOG = get_logger("bridge")

NODE_ID = "balcony_power_plant"
DEVICE_NAME = "BalconyPowerPlant"
# After a failed initialization the bridge waits lf_cd_time before the next attempt,
# so that a still running Passive command has expired and the device is in a defined
# state (usually Auto). / Nach Fehlschlag wird lf_cd_time abgewartet.
INIT_ORDER = ("device", "wifi", "ble", "battery", "pv", "es_mode", "es_status")  # es_status last
STATS_INTERVAL = 1.0      # seconds, max. publish rate of the statistics
LOG_LEVELS = ["trace", "debug", "info", "warning", "error"]

COMM_INIT, COMM_ON, COMM_FAIL = "INIT", "ON", "FAIL"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "ture", "on", "yes")


def _on_off(value: bool) -> str:
    return "ON" if value else "OFF"


class Bridge:
    def __init__(self, opts: OptionsStore) -> None:
        self.opts = opts
        self.base = str(opts["mqtt_base_topic"]).strip("/")
        self.prefix = str(opts["mqtt_discovery_prefix"]).strip("/")
        self.client: aiomqtt.Client | None = None
        self.retained: dict[str, str] = {}
        self.comm_state = COMM_INIT
        self.initialized = False
        self.device_info: dict[str, Any] = {}
        self.device_mode: str | None = None       # mode reported by ES.GetMode
        self.mode_select: str | None = None       # mode chosen in HA (select)
        self.comm_pending = self._comm_from_options()
        self._stats_dirty = True
        self._has_data: set[str] = set()   # queries that answered at least once
        self._init_task: asyncio.Task | None = None
        self._bg: set[asyncio.Task] = set()
        self.udp = UdpClient(
            host=str(opts["device_ip"]),
            port=int(opts["device_udp_port"]),
            local_port=int(opts["udp_local_port"]),
            settings=CommSettings(**self.comm_pending.__dict__),
            on_alive=self._on_alive,
            on_watchdog=self._on_watchdog,
            on_stats=self._on_stats,
        )
        self.lf = LoadFollower(self)
        self.udp.hold_queries = lambda: self.lf.regulating

    # ================================================================ helpers
    def _comm_from_options(self) -> CommSettings:
        return CommSettings(
            queue_enabled=bool(self.opts["queue_enabled"]),
            delay=float(self.opts["request_delay"]),
            timeout=float(self.opts["request_timeout"]),
            retries=int(self.opts["request_retries"]),
        )

    def t_state(self, name: str) -> str:
        return f"{self.base}/state/{name}"

    def t_ctrl(self, name: str) -> str:
        return f"{self.base}/control/{name}"

    def t_cmd(self, name: str) -> str:
        return f"{self.base}/cmd/{name}"

    @property
    def t_sensor(self) -> str:
        return str(self.opts.get("lf_sensor_topic") or "").strip()

    @property
    def t_avail(self) -> str:
        return f"{self.base}/availability"

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    def query_enabled(self, key: str) -> bool:
        return bool(self.opts.get(f"query_{key}", True))

    # ================================================================ publish
    async def publish(self, topic: str, payload: Any, retain: bool = True) -> None:
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload)
        payload = "" if payload is None else str(payload)
        if retain:
            self.retained[topic] = payload
        if self.client is None:
            return
        try:
            await self.client.publish(topic, payload, qos=0, retain=retain)
            _LOG.trace("MQTT >> %s %s", topic, payload)
        except aiomqtt.MqttError as err:
            _LOG.warning("MQTT publish to %s failed: %s", topic, err)

    async def _publish_discovery(self, component: str, key: str, config: dict | None) -> None:
        topic = f"{self.prefix}/{component}/{NODE_ID}/{key}/config"
        payload = "" if config is None else json.dumps(config)
        if self.client is None:
            return
        try:
            await self.client.publish(topic, payload, qos=0, retain=True)
            _LOG.trace("MQTT >> %s %s", topic, payload)
        except aiomqtt.MqttError as err:
            _LOG.warning("MQTT discovery publish failed: %s", err)

    # ============================================================== discovery
    # Device structure (v0.2.0) / Gerätestruktur:
    #   BalconyPowerPlant                 main device (status, general)
    #   ├─ BalconyPowerPlant Control       operating mode / passive control
    #   ├─ BalconyPowerPlant Settings      DOD, LED, Bluetooth advertising
    #   ├─ BalconyPowerPlant Communication queue settings + statistics
    #   └─ BalconyPowerPlant <query>       one sub-device per Open API query
    # Query devices are published only after a successful initialization,
    # their sensors after the first answer of that query.
    # Abfrage-Geräte erst nach erfolgreicher Initialisierung, ihre Sensoren
    # erst nach der ersten Antwort dieser Abfrage.

    def _device_block(self, group: str | None = None, label: str | None = None,
                      method: str | None = None) -> dict[str, Any]:
        model = self.opts.get("device_type") or self.device_info.get("device") or "Marstek Venus"
        block: dict[str, Any] = {"mf": "Marstek"}
        if group is None:
            block.update({"ids": [NODE_ID], "name": DEVICE_NAME, "mdl": model})
            if self.device_info.get("ver") is not None:
                block["sw"] = str(self.device_info["ver"])
        else:
            block.update({
                "ids": [f"{NODE_ID}_{group}"],
                "name": f"{DEVICE_NAME} {label}",
                "mdl": f"{model} · {method}" if method else model,
                "via_device": NODE_ID,
            })
        if self.opts.get("mqtt_suggested_area"):
            block["sa"] = self.opts["mqtt_suggested_area"]
        return block

    def _base_cfg(self, key: str, name: str, dev: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": name,
            "uniq_id": f"bpp_{key}",
            "avty_t": self.t_avail,
            "dev": dev,
            "o": {"name": DEVICE_NAME, "sw": VERSION,
                  "url": "https://github.com/maxortb-prog/Marstek-BalconyPowerPlant"},
        }

    # ---------------------------------------------------------- query devices
    def _query_device(self, q: Query) -> dict[str, Any]:
        return self._device_block(q.key, q.device, q.method)

    def _query_sensor_entities(self, q: Query) -> list[tuple[str, str, dict]]:
        dev = self._query_device(q)
        out: list[tuple[str, str, dict]] = []
        for f in q.fields:
            key = f"{q.key}_{f.key}"
            cfg = self._base_cfg(key, f.name, dev)
            cfg["stat_t"] = self.t_state(q.key)
            if f.component == "binary_sensor":
                cfg["val_tpl"] = (
                    f"{{% if value_json.{f.key} is none %}}None"
                    f"{{% elif value_json.{f.key} %}}ON{{% else %}}OFF{{% endif %}}"
                )
            else:
                cfg["val_tpl"] = f"{{{{ value_json.{f.key} }}}}"
                if f.unit:
                    cfg["unit_of_meas"] = f.unit
                if f.state_class:
                    cfg["stat_cla"] = f.state_class
            if f.device_class:
                cfg["dev_cla"] = f.device_class
            if f.diagnostic:
                cfg["ent_cat"] = "diagnostic"
            if f.icon:
                cfg["ic"] = f.icon
            out.append((f.component, key, cfg))
        return out

    def _query_request_button(self, q: Query) -> tuple[str, str, dict]:
        cfg = self._base_cfg(f"request_{q.key}", "Request", self._query_device(q))
        cfg.update({"cmd_t": self.t_cmd(f"request/{q.key}"), "pl_prs": "PRESS",
                    "ic": "mdi:refresh"})
        return ("button", f"request_{q.key}", cfg)

    def _query_enable_switch(self, q: Query) -> tuple[str, str, dict]:
        cfg = self._base_cfg(f"query_{q.key}", "Enable query", self._query_device(q))
        cfg.update({"cmd_t": self.t_cmd(f"enable/{q.key}"),
                    "stat_t": self.t_ctrl(f"query_{q.key}"),
                    "ent_cat": "config", "ic": "mdi:toggle-switch"})
        return ("switch", f"query_{q.key}", cfg)

    async def publish_query_discovery(self, q: Query) -> int:
        """Publish/remove the entities of one query device according to its state."""
        if not self.initialized:
            return 0
        enabled = self.query_enabled(q.key)
        count = 0
        for component, key, cfg in [self._query_enable_switch(q)]:
            await self._publish_discovery(component, key, cfg)
            count += 1
        component, key, cfg = self._query_request_button(q)
        await self._publish_discovery(component, key, cfg if enabled else None)
        count += 1 if enabled else 0
        show_sensors = enabled and q.key in self._has_data
        for component, key, cfg in self._query_sensor_entities(q):
            await self._publish_discovery(component, key, cfg if show_sensors else None)
            count += 1 if show_sensors else 0
        return count

    # ------------------------------------------------------- fixed devices
    def _control_entities(self) -> list[tuple[str, str, dict]]:
        ents: list[tuple[str, str, dict]] = []
        mp = self.opts.max_power
        main = self._device_block()
        control = self._device_block("control", "Control", "ES.SetMode")
        settings = self._device_block("settings", "Settings", "DOD.SET / Led.Ctrl / Ble.Adv")
        comm = self._device_block("communication", "Communication", "UDP")

        def add(dev: dict, component: str, key: str, name: str, **extra: Any) -> None:
            cfg = self._base_cfg(key, name, dev)
            cfg.update(extra)
            ents.append((component, key, cfg))

        # --- main device ----------------------------------------------------
        add(main, "sensor", "communication", "Communication established",
            stat_t=self.t_state("communication"), dev_cla="enum",
            ops=[COMM_INIT, COMM_ON, COMM_FAIL], ic="mdi:lan-connect")
        add(main, "button", "reinitialize", "Reinitialize",
            cmd_t=self.t_cmd("reinitialize"), pl_prs="PRESS", ent_cat="config",
            ic="mdi:restart")
        add(main, "button", "request_all", "Request all",
            cmd_t=self.t_cmd("request_all"), pl_prs="PRESS", ic="mdi:refresh-circle")
        add(main, "select", "log_level", "Log level",
            cmd_t=self.t_cmd("log_level"), stat_t=self.t_ctrl("log_level"),
            ops=LOG_LEVELS, ent_cat="config", ic="mdi:math-log")

        # --- control --------------------------------------------------------
        add(control, "select", "mode_select", "Operating mode",
            cmd_t=self.t_cmd("mode_select"), stat_t=self.t_ctrl("mode_select"),
            ops=list(MODES), ic="mdi:cog-transfer")
        add(control, "number", "passive_power", "Passive power",
            cmd_t=self.t_cmd("passive_power"), stat_t=self.t_ctrl("passive_power"),
            min=-mp, max=mp, step=10, unit_of_meas="W", dev_cla="power",
            mode="slider", ic="mdi:transmission-tower-export")
        add(control, "number", "passive_cd_time", "Passive countdown",
            cmd_t=self.t_cmd("passive_cd_time"), stat_t=self.t_ctrl("passive_cd_time"),
            min=0, max=86400, step=1, unit_of_meas="s", mode="box", ic="mdi:timer")
        add(control, "button", "apply_mode", "Apply mode",
            cmd_t=self.t_cmd("apply_mode"), pl_prs="PRESS", ic="mdi:send")

        # --- settings (no getter in the API -> last confirmed value) --------
        add(settings, "number", "dod", "Depth of discharge",
            cmd_t=self.t_cmd("dod"), stat_t=self.t_ctrl("dod"),
            min=30, max=88, step=1, unit_of_meas="%", mode="slider",
            ic="mdi:battery-arrow-down")
        add(settings, "switch", "led", "LED",
            cmd_t=self.t_cmd("led"), stat_t=self.t_ctrl("led"), ic="mdi:led-on")
        add(settings, "switch", "ble_adv", "Bluetooth advertising",
            cmd_t=self.t_cmd("ble_adv"), stat_t=self.t_ctrl("ble_adv"),
            ic="mdi:bluetooth-settings")

        # --- communication (pending until "Apply settings") ----------------
        add(comm, "switch", "queue_enabled", "Request queue",
            cmd_t=self.t_cmd("queue_enabled"), stat_t=self.t_ctrl("queue_enabled"),
            ent_cat="config", ic="mdi:tray-full")
        add(comm, "number", "request_delay", "Request delay",
            cmd_t=self.t_cmd("request_delay"), stat_t=self.t_ctrl("request_delay"),
            min=0, max=60, step=0.5, unit_of_meas="s", mode="box", ent_cat="config")
        add(comm, "number", "request_timeout", "Request timeout",
            cmd_t=self.t_cmd("request_timeout"), stat_t=self.t_ctrl("request_timeout"),
            min=1, max=120, step=0.5, unit_of_meas="s", mode="box", ent_cat="config")
        add(comm, "number", "request_retries", "Request retries",
            cmd_t=self.t_cmd("request_retries"), stat_t=self.t_ctrl("request_retries"),
            min=0, max=20, step=1, mode="box", ent_cat="config")
        add(comm, "button", "apply_comm", "Apply settings",
            cmd_t=self.t_cmd("apply_comm"), pl_prs="PRESS", ent_cat="config",
            ic="mdi:content-save-cog")
        add(comm, "sensor", "comm_active", "Active settings",
            stat_t=self.t_state("comm_active"), ic="mdi:cog")
        stats = [
            ("sent", "Requests sent", None, "mdi:upload"),
            ("responses", "Responses received", None, "mdi:download"),
            ("timeouts", "Timeouts", None, "mdi:timer-alert"),
            ("retries", "Retries", None, "mdi:repeat"),
            ("late", "Late responses", None, "mdi:clock-alert"),
            ("duplicates", "Duplicate responses", None, "mdi:content-duplicate"),
            ("errors", "Device errors", None, "mdi:alert-circle"),
            ("parse_repairs", "Repaired responses", None, "mdi:wrench"),
            ("slow_responses", "Slow responses", None, "mdi:snail"),
            ("queue_length", "Queue length", None, "mdi:tray-full"),
            ("pending", "Pending requests", None, "mdi:timer-sand"),
            ("last_rtt_ms", "Round-trip time", "ms", "mdi:timer-outline"),
        ]
        for key, name, unit, icon in stats:
            extra: dict[str, Any] = {
                "stat_t": self.t_state("stats"),
                "val_tpl": f"{{{{ value_json.{key} }}}}",
                "ent_cat": "diagnostic", "ic": icon, "stat_cla": "measurement",
            }
            if unit:
                extra["unit_of_meas"] = unit
            add(comm, "sensor", f"stats_{key}", name, **extra)
        add(comm, "sensor", "stats_last_response", "Last response",
            stat_t=self.t_state("stats"), val_tpl="{{ value_json.last_response }}",
            dev_cla="timestamp", ent_cat="diagnostic")
        ents += self._lf_entities()
        return ents

    def _lf_entities(self) -> list[tuple[str, str, dict]]:
        dev = self._device_block("load_following", "Load following", "ES.SetMode Passive")
        ents: list[tuple[str, str, dict]] = []

        def add(component: str, key: str, name: str, **extra: Any) -> None:
            cfg = self._base_cfg(key, name, dev)
            cfg.update(extra)
            ents.append((component, key, cfg))

        add("switch", "lf_enabled", "Load following",
            cmd_t=self.t_cmd("lf_enabled"), stat_t=self.t_ctrl("lf_enabled"),
            ic="mdi:home-lightning-bolt")
        add("select", "lf_export_reaction", "Export reaction",
            cmd_t=self.t_cmd("lf_export_reaction"), stat_t=self.t_ctrl("lf_export_reaction"),
            ops=EXPORT_REACTIONS, ent_cat="config", ic="mdi:transmission-tower-off")
        for key, (name, _default) in LF_SWITCHES.items():
            add("switch", key, name, cmd_t=self.t_cmd(key), stat_t=self.t_ctrl(key),
                ent_cat="config", ic="mdi:pause-octagon-outline")
        for key, (name, lo, hi, step, unit, _default) in LF_NUMBERS.items():
            extra: dict[str, Any] = {"cmd_t": self.t_cmd(key), "stat_t": self.t_ctrl(key),
                                     "min": lo, "max": hi, "step": step, "mode": "box",
                                     "ent_cat": "config"}
            if unit:
                extra["unit_of_meas"] = unit
            add("number", key, name, **extra)

        st = self.t_state("load_following")
        add("sensor", "lf_state", "State", stat_t=st, val_tpl="{{ value_json.state }}",
            dev_cla="enum", ops=LF_STATES,
            ic="mdi:state-machine")
        for key, name, icon in (
            ("grid_raw", "Grid power", "mdi:transmission-tower"),
            ("grid_avg_up", "Grid power average up", "mdi:chart-bell-curve"),
            ("grid_avg_down", "Grid power average down", "mdi:chart-bell-curve"),
            ("load_estimated", "Estimated load", "mdi:home-lightning-bolt-outline"),
            ("setpoint", "Active setpoint", "mdi:target"),
            ("setpoint_target", "Calculated setpoint", "mdi:calculator"),
            ("power_limit", "Power limit", "mdi:arrow-collapse-up"),
            ("reference", "Takeover reference", "mdi:import"),
        ):
            add("sensor", f"lf_{key}", name, stat_t=st,
                val_tpl=f"{{{{ value_json.{key} }}}}", unit_of_meas="W",
                dev_cla="power", stat_cla="measurement", ic=icon)
        add("sensor", "lf_last_reason", "Last command reason", stat_t=st,
            val_tpl="{{ value_json.last_reason }}", ic="mdi:comment-text-outline")
        add("sensor", "lf_last_command", "Last command", stat_t=st,
            val_tpl="{{ value_json.last_command }}", dev_cla="timestamp")
        add("sensor", "lf_hold_for", "Hold elapsed", stat_t=st,
            val_tpl="{{ value_json.hold_for }}", unit_of_meas="s", ic="mdi:pause-circle-outline")
        add("sensor", "lf_last_dropout_duration", "Last dropout duration", stat_t=st,
            val_tpl="{{ value_json.last_dropout_duration }}", unit_of_meas="s",
            ic="mdi:timer-sand-complete")
        add("sensor", "lf_dropout_interval", "Dropout interval", stat_t=st,
            val_tpl="{{ value_json.dropout_interval }}", unit_of_meas="s",
            stat_cla="measurement", ic="mdi:timer-sync-outline")
        add("sensor", "lf_jitter_in", "Next jitter in", stat_t=st,
            val_tpl="{{ value_json.jitter_in }}", unit_of_meas="s", ic="mdi:sine-wave")
        add("sensor", "lf_keepalive_in", "Keepalive in", stat_t=st,
            val_tpl="{{ value_json.keepalive_in }}", unit_of_meas="s",
            ic="mdi:timer-refresh-outline")
        for key, name, icon in (
            ("commands", "Setpoint commands", "mdi:counter"),
            ("keepalives", "Keepalives sent", "mdi:counter"),
            ("export_events", "Export events", "mdi:transmission-tower-export"),
            ("suspected_dropouts", "Suspected dropouts", "mdi:alert"),
            ("sensor_values", "Sensor values received", "mdi:counter"),
            ("invalid_values", "Invalid sensor values", "mdi:alert-circle-outline"),
            ("dropouts", "Dropouts detected", "mdi:power-plug-off"),
            ("dropout_recoveries", "Dropout recoveries", "mdi:power-plug"),
            ("dropout_timeouts", "Dropout hold timeouts", "mdi:timer-off-outline"),
            ("jitter_probes", "Jitter probes", "mdi:sine-wave"),
            ("jitter_followed", "Jitter followed", "mdi:check-circle-outline"),
            ("jitter_not_followed", "Jitter not followed", "mdi:close-circle-outline"),
            ("jitter_inconclusive", "Jitter inconclusive", "mdi:help-circle-outline"),
        ):
            add("sensor", f"lf_{key}", name, stat_t=st,
                val_tpl=f"{{{{ value_json.{key} }}}}", stat_cla="total_increasing",
                ent_cat="diagnostic", ic=icon)
        return ents

    async def publish_all_discovery(self) -> None:
        count = 0
        for component, key, cfg in self._control_entities():
            await self._publish_discovery(component, key, cfg)
            count += 1
        for q in QUERIES.values():
            count += await self.publish_query_discovery(q)
        for component, key in OBSOLETE_ENTITIES:      # entities of older versions
            await self._publish_discovery(component, key, None)
        _LOG.info("MQTT discovery published (%d entities)%s", count,
                  "" if self.initialized else " - query devices follow after initialization")

    # ================================================================ states
    async def publish_initial_states(self) -> None:
        await self.publish(self.t_state("communication"), self.comm_state)
        await self.publish(self.t_ctrl("passive_power"), int(self.opts["passive_power"]))
        await self.publish(self.t_ctrl("passive_cd_time"), int(self.opts["passive_cd_time"]))
        await self.publish(self.t_ctrl("dod"), int(self.opts["dod"]))
        await self.publish(self.t_ctrl("led"), _on_off(bool(self.opts["led"])))
        await self.publish(self.t_ctrl("ble_adv"), _on_off(bool(self.opts["ble_adv"])))
        await self.publish(self.t_ctrl("log_level"), str(self.opts["log_level"]).lower())
        await self._publish_comm_pending()
        await self.publish(self.t_state("comm_active"), self.udp.settings.describe())
        for q in QUERIES.values():
            await self.publish(self.t_ctrl(f"query_{q.key}"), _on_off(self.query_enabled(q.key)))
        if self.mode_select:
            await self.publish(self.t_ctrl("mode_select"), self.mode_select)
        await self.publish(self.t_ctrl("lf_enabled"), _on_off(self.lf.enabled))
        await self.publish(self.t_ctrl("lf_export_reaction"), self.lf.export_reaction)
        for key in LF_NUMBERS:
            await self.publish(self.t_ctrl(key), self.lf.o(key))
        for key in LF_SWITCHES:
            await self.publish(self.t_ctrl(key), _on_off(self.lf.sw(key)))
        await self.publish(self.t_state("load_following"), self.lf.snapshot())

    async def _publish_comm_pending(self) -> None:
        c = self.comm_pending
        await self.publish(self.t_ctrl("queue_enabled"), _on_off(c.queue_enabled))
        await self.publish(self.t_ctrl("request_delay"), c.delay)
        await self.publish(self.t_ctrl("request_timeout"), c.timeout)
        await self.publish(self.t_ctrl("request_retries"), c.retries)

    async def on_connected(self, client: aiomqtt.Client) -> None:
        """Called after every (re)connect to the broker."""
        self.client = client
        await client.subscribe(f"{self.base}/cmd/#")
        await client.subscribe(f"{self.prefix}/status")
        if self.t_sensor:
            await client.subscribe(self.t_sensor)
            _LOG.info("Load following: grid sensor topic '%s'", self.t_sensor)
        self.lf.start()
        await self.publish(self.t_avail, "online")
        await self.publish_all_discovery()
        await self.publish_initial_states()
        for topic, payload in list(self.retained.items()):  # re-send cached states
            await self.publish(topic, payload)
        if self._init_task is None:
            self.start_init()

    async def stop(self) -> None:
        if self._init_task:
            self._init_task.cancel()
        for task in list(self._bg):
            task.cancel()
        await self.lf.stop()
        await self.udp.stop()

    def on_disconnected(self) -> None:
        self.client = None

    async def set_comm(self, state: str) -> None:
        if state != self.comm_state:
            _LOG.info("Communication established: %s -> %s", self.comm_state, state)
        self.comm_state = state
        await self.publish(self.t_state("communication"), state)

    # ======================================================== initialization
    def start_init(self) -> None:
        if self._init_task and not self._init_task.done():
            self._init_task.cancel()
        self._init_task = asyncio.create_task(self._initialize(), name="init")

    async def _initialize(self) -> None:
        """Initialization as a strict sequence. Every step waits for its answer.
        A timeout at ANY step aborts: FAIL -> wait lf_cd_time -> start again from
        the beginning. Nothing is skipped and nothing else is sent meanwhile."""
        self.initialized = False
        self.lf.reset()
        attempt = 0
        while True:
            attempt += 1
            await self.set_comm(COMM_INIT)
            _LOG.info("Initialization attempt %d -> %s:%s", attempt,
                      self.opts["device_ip"], self.opts["device_udp_port"])
            failed = await self._init_sequence()
            if failed is None:
                break
            wait = int(self.lf.o("lf_cd_time"))
            _LOG.error("Initialization aborted: no answer to %s - waiting lf_cd_time %d s "
                       "before the next attempt", failed, wait)
            await self.set_comm(COMM_FAIL)
            for remaining in range(wait, 0, -1):
                if remaining % 30 == 0 or remaining == 10:
                    _LOG.info("Next initialization attempt in %d s", remaining)
                await asyncio.sleep(1)

        self.initialized = True
        await self.set_comm(COMM_ON)
        for qq in QUERIES.values():
            await self.publish_query_discovery(qq)
        _LOG.info("Initialization done (attempt %d), query devices published", attempt)
        self.lf.evaluate()

    async def _init_sequence(self) -> str | None:
        """Run all init steps. Returns None on success or the name of the step
        that timed out."""
        ble = lambda: str(self.opts.get("device_ble_mac") or "")  # noqa: E731

        async def step(name: str, fut: asyncio.Future) -> dict[str, Any] | None | bool:
            try:
                return await fut
            except RequestTimeout:
                return False
            except DeviceError as err:      # device answered -> communication is fine
                _LOG.error("Initialization step %s: device error %s - continuing", name, err)
                return None
            except asyncio.CancelledError:
                if fut.cancelled():
                    return None
                raise

        # 1. device info (always)
        q = QUERIES["device"]
        res = await step(q.method, self.udp.submit(
            q.method, lambda i: query_params(q, i, ble()), handler=self._query_handler(q)))
        if res is False:
            return q.method
        # 2. settings, sent with the last values
        if await step("DOD.SET", self.send_dod(int(self.opts["dod"]))) is False:
            return "DOD.SET"
        if await step("Led.Ctrl", self.send_led(bool(self.opts["led"]))) is False:
            return "Led.Ctrl"
        if await step("Ble.Adv", self.send_ble_adv(bool(self.opts["ble_adv"]))) is False:
            return "Ble.Adv"
        # 3. enabled queries, ES.GetStatus last (reference for load following)
        for key in INIT_ORDER[1:]:
            q = QUERIES[key]
            needed = self.query_enabled(key) or (key == "es_status" and self.lf.enabled)
            if not needed:
                continue
            handler = self._query_handler(q) if self.query_enabled(key) else None
            res = await step(q.method, self.udp.submit(
                q.method, lambda i, q=q: query_params(q, i, ble()), handler=handler))
            if res is False:
                return q.method
            if key == "es_status" and isinstance(res, dict):
                self.lf.set_reference(res.get("ongrid_power"), "initialization")
        return None

    # ===================================================== watchdog / stats
    def _on_alive(self) -> None:
        if self.initialized and self.comm_state == COMM_FAIL:
            _LOG.info("Device answers again - communication restored")
            self._spawn(self.set_comm(COMM_ON))

    def _on_watchdog(self, p: Pending) -> None:
        _LOG.error("Watchdog: no answer for #%03d %s after %d retries",
                   p.req_id, p.method, self.udp.settings.retries)
        if self.comm_state != COMM_FAIL:
            self._spawn(self.set_comm(COMM_FAIL))

    def _on_stats(self) -> None:
        self._stats_dirty = True

    async def stats_loop(self) -> None:
        while True:
            await asyncio.sleep(STATS_INTERVAL)
            if self._stats_dirty and self.client is not None:
                self._stats_dirty = False
                await self.publish(self.t_state("stats"), self.udp.stats.as_dict())

    # ============================================================== queries
    def _query_handler(self, q: Query):
        async def handler(result: dict[str, Any], late: bool) -> None:
            data = normalize(q, result)
            await self.publish(self.t_state(q.key), data)
            if q.key not in self._has_data:
                self._has_data.add(q.key)
                if self.initialized and self.query_enabled(q.key):
                    await self.publish_query_discovery(q)
                    _LOG.info("%s: entities created in Home Assistant", q.device)
            _LOG.info("%s received%s", q.label, " (late)" if late else "")
            if q.key == "device":
                await self._on_device_info(data)
            elif q.key == "es_mode":
                await self._on_device_mode(data.get("mode"))
        return handler

    def ready(self, what: str) -> bool:
        if not self.initialized:
            _LOG.warning("%s ignored - communication not established (%s)", what, self.comm_state)
            return False
        return True

    def request(self, key: str) -> None:
        q = QUERIES[key]
        if not self.ready(f"Request {q.label}"):
            return
        if not self.query_enabled(key):
            _LOG.warning("Request %s ignored - query is disabled", q.label)
            return
        ble = str(self.opts.get("device_ble_mac") or "")
        self.udp.submit(q.method, lambda i: query_params(q, i, ble), handler=self._query_handler(q))

    def request_all(self, skip: tuple[str, ...] = ()) -> None:
        if not self.ready("Request all"):
            return
        for key in QUERIES:
            if key not in skip and self.query_enabled(key):
                self.request(key)

    async def _on_device_info(self, data: dict[str, Any]) -> None:
        old_model, old_ver = self.device_info.get("device"), self.device_info.get("ver")
        self.device_info = data
        if not self.opts.get("device_type") and data.get("device"):
            _LOG.info("Device type %s detected - writing back to configuration", data["device"])
            self.opts.set("device_type", str(data["device"]))
        if not self.opts.get("device_ble_mac") and data.get("ble_mac"):
            _LOG.info("BLE MAC %s detected - writing back to configuration", data["ble_mac"])
            self.opts.set("device_ble_mac", str(data["ble_mac"]))
        if (old_model, old_ver) != (data.get("device"), data.get("ver")):
            await self.publish_all_discovery()   # update model / firmware in the device card

    async def _on_device_mode(self, mode: Any) -> None:
        if mode is None:
            return
        match = next((m for m in MODES if m.lower() == str(mode).lower()), None)
        self.device_mode = match or str(mode)
        if self.mode_select is None and match:
            self.mode_select = match
            await self.publish(self.t_ctrl("mode_select"), match)

    # ============================================================= settings
    def _set_handler(self, what: str, on_ok):
        async def handler(result: dict[str, Any], late: bool) -> None:
            if _truthy(result.get("set_result")):
                _LOG.info("%s confirmed by device%s", what, " (late)" if late else "")
                await on_ok()
            else:
                _LOG.error("%s rejected by device: %s", what, result)
        return handler

    def send_dod(self, value: int) -> asyncio.Future:
        value = max(30, min(88, int(value)))

        async def ok() -> None:
            self.opts.set("dod", value)
            await self.publish(self.t_ctrl("dod"), value)
        return self.udp.submit("DOD.SET", lambda i: dod_params(i, value),
                        handler=self._set_handler(f"DOD {value}%", ok))

    def send_led(self, on: bool) -> asyncio.Future:
        async def ok() -> None:
            self.opts.set("led", on)
            await self.publish(self.t_ctrl("led"), _on_off(on))
        return self.udp.submit("Led.Ctrl", lambda i: led_params(i, on),
                        handler=self._set_handler(f"LED {_on_off(on)}", ok))

    def send_ble_adv(self, on: bool) -> asyncio.Future:
        async def ok() -> None:
            self.opts.set("ble_adv", on)
            await self.publish(self.t_ctrl("ble_adv"), _on_off(on))
        return self.udp.submit("Ble.Adv", lambda i: ble_adv_params(i, on),
                        handler=self._set_handler(f"Bluetooth advertising {_on_off(on)}", ok))

    def apply_mode(self) -> None:
        mode = self.mode_select
        if mode is None:
            _LOG.warning("Apply mode ignored - no operating mode selected yet")
            return
        if self.lf.enabled:
            if mode == "Passive":
                # load following owns the device: Passive power is only the upper limit
                _LOG.info("Apply mode: load following active - power limit now %d W",
                          self.lf.cap)
                self.lf.evaluate()
                return
            self.lf.set_enabled(False, "mode change")
            self._spawn(self.publish(self.t_ctrl("lf_enabled"), "OFF"))
        mp = self.opts.max_power
        power = max(-mp, min(mp, int(self.opts["passive_power"])))
        cd_time = int(self.opts["passive_cd_time"])
        what = f"Mode {mode}" + (f" ({power} W, {cd_time} s)" if mode == "Passive" else "")

        async def ok() -> None:
            if self.device_mode != mode and self.query_enabled("es_mode"):
                self.request("es_mode")       # read back the new mode once
        self.udp.submit("ES.SetMode", lambda i: set_mode_params(i, mode, power, cd_time),
                        handler=self._set_handler(what, ok))
        _LOG.info("%s requested", what)

    # ============================================================= commands
    async def handle_message(self, message: aiomqtt.Message) -> None:
        topic = message.topic.value
        payload = message.payload.decode(errors="replace").strip() if isinstance(
            message.payload, (bytes, bytearray)) else str(message.payload or "")
        _LOG.trace("MQTT << %s %s", topic, payload)

        if topic == f"{self.prefix}/status":
            if payload == "online":
                _LOG.info("Home Assistant restarted - republishing discovery and states")
                await self.publish_all_discovery()
                for t, p in list(self.retained.items()):
                    await self.publish(t, p)
            return

        if self.t_sensor and topic == self.t_sensor:
            try:
                self.lf.on_sensor(payload)
            except Exception:  # noqa: BLE001  never let the regulator kill the bridge
                _LOG.exception("Load following: error while processing sensor value %r", payload)
            return

        prefix = f"{self.base}/cmd/"
        if not topic.startswith(prefix):
            return
        cmd = topic[len(prefix):]
        try:
            await self._dispatch(cmd, payload)
        except (ValueError, KeyError) as err:
            _LOG.error("Invalid command %s = %r: %s", cmd, payload, err)

    async def _dispatch(self, cmd: str, payload: str) -> None:
        on = payload.upper() in ("ON", "1", "TRUE")
        if cmd.startswith("request/"):
            self.request(cmd.split("/", 1)[1])
        elif cmd == "request_all":
            self.request_all()
        elif cmd.startswith("enable/"):
            key = cmd.split("/", 1)[1]
            q = QUERIES[key]
            self.opts.set(f"query_{key}", on)
            await self.publish(self.t_ctrl(f"query_{key}"), _on_off(on))
            await self.publish_query_discovery(q)
            _LOG.info("%s %s", q.label, "enabled" if on else "disabled")
        elif cmd == "lf_enabled":
            self.lf.set_enabled(on, "Home Assistant")
            await self.publish(self.t_ctrl("lf_enabled"), _on_off(self.lf.enabled))
        elif cmd == "lf_export_reaction":
            if payload not in EXPORT_REACTIONS:
                raise ValueError("unknown export reaction")
            self.opts.set("lf_export_reaction", payload)
            await self.publish(self.t_ctrl("lf_export_reaction"), payload)
            _LOG.info("Load following: export reaction %s", payload)
        elif cmd in LF_SWITCHES:
            self.opts.set(cmd, on)
            await self.publish(self.t_ctrl(cmd), _on_off(on))
            _LOG.info("Load following: %s %s", LF_SWITCHES[cmd][0], _on_off(on))
        elif cmd in LF_NUMBERS:
            _name, lo, hi, _step, _unit, _d = LF_NUMBERS[cmd]
            value = max(lo, min(hi, float(payload)))
            value = int(value) if float(value).is_integer() else value
            self.opts.set(cmd, value)
            await self.publish(self.t_ctrl(cmd), value)
            _LOG.info("Load following: %s = %s", LF_NUMBERS[cmd][0], value)
        elif cmd == "mode_select":
            match = next((m for m in MODES if m.lower() == payload.lower()), None)
            if match is None:
                raise ValueError("unknown mode")
            self.mode_select = match
            await self.publish(self.t_ctrl("mode_select"), match)
        elif cmd == "passive_power":
            mp = self.opts.max_power
            value = max(-mp, min(mp, int(round(float(payload)))))
            self.opts.set("passive_power", value)
            await self.publish(self.t_ctrl("passive_power"), value)
            if self.lf.enabled:
                self.lf.evaluate()
        elif cmd == "passive_cd_time":
            value = max(0, min(86400, int(round(float(payload)))))
            self.opts.set("passive_cd_time", value)
            await self.publish(self.t_ctrl("passive_cd_time"), value)
        elif cmd == "apply_mode":
            if self.ready("Apply mode"):
                self.apply_mode()
        elif cmd == "dod":
            if self.ready("DOD"):
                self.send_dod(int(round(float(payload))))
        elif cmd == "led":
            if self.ready("LED"):
                self.send_led(on)
        elif cmd == "ble_adv":
            if self.ready("Bluetooth advertising"):
                self.send_ble_adv(on)
        elif cmd == "queue_enabled":
            self.comm_pending.queue_enabled = on
            await self._publish_comm_pending()
        elif cmd == "request_delay":
            self.comm_pending.delay = max(0.0, min(60.0, float(payload)))
            await self._publish_comm_pending()
        elif cmd == "request_timeout":
            self.comm_pending.timeout = max(1.0, min(120.0, float(payload)))
            await self._publish_comm_pending()
        elif cmd == "request_retries":
            self.comm_pending.retries = max(0, min(20, int(round(float(payload)))))
            await self._publish_comm_pending()
        elif cmd == "apply_comm":
            c = self.comm_pending
            self.udp.apply_settings(CommSettings(**c.__dict__))
            self.opts.set("queue_enabled", c.queue_enabled)
            self.opts.set("request_delay", c.delay)
            self.opts.set("request_timeout", c.timeout)
            self.opts.set("request_retries", c.retries)
            await self.publish(self.t_state("comm_active"), self.udp.settings.describe())
        elif cmd == "log_level":
            level = payload.lower()
            if level not in LOG_LEVELS:
                raise ValueError("unknown log level")
            set_level(level)
            self.opts.set("log_level", level)
            await self.publish(self.t_ctrl("log_level"), level)
            _LOG.info("Log level set to %s", level.upper())
        elif cmd == "reinitialize":
            _LOG.info("Reinitialization requested from Home Assistant")
            self.start_init()
        else:
            _LOG.warning("Unknown command topic %s", cmd)
