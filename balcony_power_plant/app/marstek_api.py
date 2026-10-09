"""Marstek Open API (Rev 2.0) definitions and a tolerant JSON parser.

EN: Method names, parameter builders and the list of readable fields per query.
DE: Methodennamen, Parameter-Builder und die lesbaren Felder je Abfrage.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Readable queries ("functions"). Each one can be enabled/disabled in HA.
# Lesbare Abfragen. Jede kann im HA ein-/ausgeschaltet werden.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    key: str                      # key in the device result
    name: str                     # HA entity name (English)
    component: str = "sensor"     # sensor | binary_sensor
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    diagnostic: bool = False
    icon: str | None = None
    numeric: bool = True
    source: str | None = None     # derived field: read this key instead of `key`
    factor: float | None = None   # derived field: multiply the source value


@dataclass(frozen=True)
class Query:
    key: str                      # internal key, also option name query_<key>
    method: str                   # Open API method
    label: str                    # human readable (English), used in logs
    device: str                   # name of the HA sub-device ("BalconyPowerPlant <device>")
    fields: tuple[Field, ...] = field(default_factory=tuple)


def _pv_fields() -> tuple[Field, ...]:
    out: list[Field] = []
    for i in range(1, 5):
        out += [
            Field(f"pv{i}_power", f"PV{i} power", unit="W", device_class="power",
                  state_class="measurement"),
            Field(f"pv{i}_voltage", f"PV{i} voltage", unit="V", device_class="voltage",
                  state_class="measurement", diagnostic=True),
            Field(f"pv{i}_current", f"PV{i} current", unit="A", device_class="current",
                  state_class="measurement", diagnostic=True),
            Field(f"pv{i}_state", f"PV{i} working", component="binary_sensor",
                  device_class="running", diagnostic=True),
        ]
    return tuple(out)


QUERIES: dict[str, Query] = {
    "device": Query("device", "Marstek.GetDevice", "Device info", "Device info", (
        Field("device", "Model", diagnostic=True, numeric=False, icon="mdi:battery-charging-high"),
        Field("ver", "Firmware", diagnostic=True, numeric=False, icon="mdi:chip"),
        Field("ble_mac", "Bluetooth MAC", diagnostic=True, numeric=False, icon="mdi:bluetooth"),
        Field("wifi_mac", "WiFi MAC", diagnostic=True, numeric=False, icon="mdi:wifi"),
        Field("wifi_name", "WiFi name", diagnostic=True, numeric=False, icon="mdi:wifi"),
        Field("ip", "IP address", diagnostic=True, numeric=False, icon="mdi:ip-network"),
    )),
    "wifi": Query("wifi", "Wifi.GetStatus", "WiFi status", "WiFi", (
        Field("ssid", "SSID", diagnostic=True, numeric=False, icon="mdi:wifi"),
        Field("rssi", "Signal", unit="dBm", device_class="signal_strength",
              state_class="measurement", diagnostic=True),
        Field("sta_ip", "IP address", diagnostic=True, numeric=False, icon="mdi:ip-network"),
        Field("sta_gate", "Gateway", diagnostic=True, numeric=False, icon="mdi:router-network"),
        Field("sta_mask", "Subnet mask", diagnostic=True, numeric=False, icon="mdi:ip-network"),
        Field("sta_dns", "DNS", diagnostic=True, numeric=False, icon="mdi:dns"),
    )),
    "ble": Query("ble", "BLE.GetStatus", "Bluetooth status", "Bluetooth", (
        Field("state", "State", diagnostic=True, numeric=False, icon="mdi:bluetooth-connect"),
        Field("ble_mac", "MAC", diagnostic=True, numeric=False, icon="mdi:bluetooth"),
    )),
    "battery": Query("battery", "Bat.GetStatus", "Battery status", "Battery", (
        Field("soc", "SOC", unit="%", device_class="battery", state_class="measurement"),
        Field("bat_temp", "Temperature", unit="°C", device_class="temperature",
              state_class="measurement"),
        Field("bat_capacity", "Remaining capacity", unit="Wh",
              device_class="energy_storage", state_class="measurement"),
        Field("rated_capacity", "Rated capacity", unit="Wh",
              device_class="energy_storage", diagnostic=True),
        Field("charg_flag", "Charging allowed", component="binary_sensor", diagnostic=True),
        Field("dischrg_flag", "Discharging allowed", component="binary_sensor", diagnostic=True),
    )),
    "pv": Query("pv", "PV.GetStatus", "PV status", "PV", _pv_fields()),
    "es_status": Query("es_status", "ES.GetStatus", "Energy system status", "Energy system", (
        Field("bat_soc", "SOC", unit="%", device_class="battery", state_class="measurement"),
        Field("bat_cap", "Battery capacity", unit="Wh", device_class="energy_storage",
              diagnostic=True),
        Field("pv_power", "PV power", unit="W", device_class="power", state_class="measurement"),
        Field("ongrid_power", "Grid power", unit="W", device_class="power", state_class="measurement"),
        Field("offgrid_power", "Off-grid power", unit="W", device_class="power",
              state_class="measurement"),
        Field("bat_power", "Battery power", unit="W", device_class="power", state_class="measurement"),
        Field("total_pv_energy", "Total PV energy", unit="Wh", device_class="energy",
              state_class="total_increasing"),
        # Firmware reports total_pv_energy 10x too small (API doc says [Wh]).
        # Firmware liefert total_pv_energy um Faktor 10 zu klein (laut Doku [Wh]).
        Field("total_pv_energy_corrected", "Total PV energy (corrected)", unit="Wh",
              device_class="energy", state_class="total_increasing",
              source="total_pv_energy", factor=10),
        Field("total_grid_output_energy", "Total grid output energy", unit="Wh",
              device_class="energy", state_class="total_increasing"),
        Field("total_grid_input_energy", "Total grid input energy", unit="Wh",
              device_class="energy", state_class="total_increasing"),
        Field("total_load_energy", "Total load energy", unit="Wh", device_class="energy",
              state_class="total_increasing"),
    )),
    "es_mode": Query("es_mode", "ES.GetMode", "Energy system mode", "Mode", (
        Field("mode", "Operating mode", numeric=False, icon="mdi:cog-transfer"),
        Field("ct_state", "CT connected", component="binary_sensor", diagnostic=True),
    )),
}

MODES = ("Auto", "AI", "UPS", "Passive")

# ---------------------------------------------------------------------------
# Parameter builders. "instance" is params.id (counts globally like the request id).
# ---------------------------------------------------------------------------


def query_params(query: Query, instance: int, ble_mac: str) -> dict[str, Any]:
    if query.method == "Marstek.GetDevice":
        return {"ble_mac": ble_mac or "0"}
    return {"id": instance}


def set_mode_params(instance: int, mode: str, power: int, cd_time: int) -> dict[str, Any]:
    if mode == "Auto":
        cfg: dict[str, Any] = {"mode": "Auto", "auto_cfg": {"enable": 1}}
    elif mode == "AI":
        cfg = {"mode": "AI", "ai_cfg": {"enable": 1}}
    elif mode == "UPS":
        cfg = {"mode": "UPS", "ups_cfg": {"enable": 1}}
    elif mode == "Passive":
        cfg = {"mode": "Passive", "passive_cfg": {"power": int(power), "cd_time": int(cd_time)}}
    else:
        raise ValueError(f"Unknown mode {mode!r}")
    return {"id": instance, "config": cfg}


def dod_params(instance: int, value: int) -> dict[str, Any]:
    return {"id": instance, "value": int(value)}


def led_params(instance: int, on: bool) -> dict[str, Any]:
    # Led.Ctrl: 1 = on, 0 = off
    return {"id": instance, "state": 1 if on else 0}


def ble_adv_params(instance: int, advertising: bool) -> dict[str, Any]:
    # Ble.Adv: INVERTED per API doc -> 0 = enable, 1 = disable
    return {"id": instance, "enable": 0 if advertising else 1}


# ---------------------------------------------------------------------------
# Tolerant parser (the API doc contains "ture", missing commas, full-width colons)
# ---------------------------------------------------------------------------

_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bture\b"), "true"),
    (re.compile(r"\bflase\b"), "false"),
    (re.compile("："), ":"),          # full-width colon
    (re.compile("，"), ","),          # full-width comma
    # missing comma between a value and the next key:  "a": 1 \n "b"
    (re.compile(r'("|\d|true|false|null|\}|\])(\s*\n\s*)(")'), r"\1,\2\3"),
    # trailing comma before } or ]
    (re.compile(r",(\s*[}\]])"), r"\1"),
)


class ParseError(Exception):
    pass


def parse_datagram(data: bytes) -> tuple[dict[str, Any], bool]:
    """Return (message, repaired). Raises ParseError if not recoverable."""
    text = data.decode("utf-8", errors="replace").strip().strip("\x00")
    try:
        msg = json.loads(text)
        repaired = False
    except json.JSONDecodeError:
        fixed = text
        for pattern, repl in _FIXES:
            fixed = pattern.sub(repl, fixed)
        try:
            msg = json.loads(fixed)
        except json.JSONDecodeError as err:
            raise ParseError(f"{err} in {text!r}") from err
        repaired = True
    if not isinstance(msg, dict):
        raise ParseError(f"JSON is not an object: {text!r}")
    return msg, repaired


def _to_number(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    try:
        num = float(str(value).strip())
        return int(num) if num.is_integer() else num
    except ValueError:
        return value


def normalize(query: Query, result: dict[str, Any]) -> dict[str, Any]:
    """Make sure every known field exists (null if missing) and numbers are numbers.

    Unknown extra fields are kept, so nothing the device sends gets lost.
    """
    out = dict(result)
    for f in query.fields:
        val = result.get(f.source or f.key)
        if f.factor is not None:
            num = _to_number(val)
            if isinstance(num, (int, float)) and not isinstance(num, bool):
                val = round(num * f.factor, 3)
            else:
                val = None
        if f.component == "binary_sensor":
            if val is None:
                out[f.key] = None
            else:
                out[f.key] = bool(_to_number(val))
        elif f.numeric:
            out[f.key] = _to_number(val)
        else:
            out[f.key] = None if val is None else str(val)
    return out
