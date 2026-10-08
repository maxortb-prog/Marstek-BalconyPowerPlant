"""Add-on options: loading and write-back (decision D).

EN: The add-on configuration tab is the single source of truth. Values changed
    at runtime in Home Assistant (DOD, LED, queue settings, ...) are written back
    to the add-on options via the Supervisor API. The add-on is NOT restarted by
    this. device_ble_mac / device_type are written back after the first read.
DE: Der Konfigurations-Tab des Add-ons ist die einzige Wahrheit. Zur Laufzeit im
    HA geänderte Werte werden über die Supervisor-API in die Add-on-Optionen
    zurückgeschrieben (kein Neustart). device_ble_mac / device_type werden nach
    dem ersten Auslesen zurückgesichert.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from typing import Any

from logger import get_logger

_LOG = get_logger("config")

OPTIONS_PATH = os.environ.get("BPP_OPTIONS", "/data/options.json")
SUPERVISOR_URL = "http://supervisor/addons/self/options"
WRITE_BACK_DELAY = 2.0  # seconds, coalesces several quick changes into one write

DEFAULTS: dict[str, Any] = {
    "device_ip": "192.168.0.45",
    "device_udp_port": 30000,
    "device_ble_mac": "",
    "device_type": "",
    "udp_local_port": 0,
    "max_power": "800",
    "mqtt_host": "core-mosquitto",
    "mqtt_port": 1883,
    "mqtt_username": "",
    "mqtt_password": "",
    "mqtt_discovery_prefix": "homeassistant",
    "mqtt_base_topic": "Marstek-Bridge-Control",
    "mqtt_suggested_area": "Marstek",
    "queue_enabled": True,
    "request_delay": 2.0,
    "request_timeout": 5.0,
    "request_retries": 0,
    "dod": 88,
    "led": True,
    "ble_adv": False,
    "passive_power": 0,
    "passive_cd_time": 300,
    "query_device": True,
    "query_wifi": True,
    "query_ble": True,
    "query_battery": True,
    "query_pv": True,
    "query_es_status": True,
    "query_es_mode": True,
    "log_level": "info",
}


class OptionsStore:
    """Holds the current options and writes changes back (debounced)."""

    def __init__(self, path: str = OPTIONS_PATH) -> None:
        self.path = path
        self.data: dict[str, Any] = dict(DEFAULTS)
        try:
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            self.data.update({k: v for k, v in loaded.items() if v is not None})
        except FileNotFoundError:
            _LOG.warning("Options file %s not found, using defaults", path)
        self._token = os.environ.get("SUPERVISOR_TOKEN")
        self._flush_handle: asyncio.TimerHandle | None = None

    # ------------------------------------------------------------------ access
    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    @property
    def max_power(self) -> int:
        try:
            return int(self.data.get("max_power", 800))
        except (TypeError, ValueError):
            return 800

    def set(self, key: str, value: Any) -> None:
        """Change a value and schedule a write-back to the add-on options."""
        if self.data.get(key) == value:
            return
        _LOG.debug("Option %s: %r -> %r (write-back scheduled)", key, self.data.get(key), value)
        self.data[key] = value
        loop = asyncio.get_running_loop()
        if self._flush_handle:
            self._flush_handle.cancel()
        self._flush_handle = loop.call_later(
            WRITE_BACK_DELAY, lambda: asyncio.ensure_future(self.flush())
        )

    # -------------------------------------------------------------- write-back
    @property
    def dirty(self) -> bool:
        return self._flush_handle is not None

    async def flush(self) -> None:
        if self._flush_handle:
            self._flush_handle.cancel()
        self._flush_handle = None
        if self._token:
            await asyncio.to_thread(self._post_supervisor)
        else:
            # local development: no Supervisor -> write the options file itself
            await asyncio.to_thread(self._write_local)

    def _post_supervisor(self) -> None:
        body = json.dumps({"options": self.data}).encode()
        req = urllib.request.Request(
            SUPERVISOR_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                _LOG.info("Options written back to add-on configuration (HTTP %s)", resp.status)
        except urllib.error.HTTPError as err:
            _LOG.error("Supervisor rejected options write-back: HTTP %s %s",
                       err.code, err.read().decode(errors="replace"))
        except Exception as err:  # noqa: BLE001
            _LOG.error("Options write-back failed: %s", err)

    def _write_local(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2)
            _LOG.info("Options written back to %s (no Supervisor, local mode)", self.path)
        except OSError as err:
            _LOG.error("Writing %s failed: %s", self.path, err)
