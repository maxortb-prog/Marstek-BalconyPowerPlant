# BalconyPowerPlant – Documentation

🇩🇪 German version: [DOCS.de.md](DOCS.de.md)

Local bridge between a **Marstek Venus** battery (tested target: **Venus A**) and
Home Assistant. It talks to the device over the **Marstek Open API Rev 2.0 (UDP,
JSON-RPC)** and publishes everything to HA via **MQTT discovery** as the device
**BalconyPowerPlant**.

> The Marstek Local API is provided "as is" by Marstek. Use at your own risk.

## Requirements

1. Open API enabled in the Marstek app (UDP port, default `30000`).
2. Device with a static IP (DHCP reservation).
3. MQTT broker (e.g. the Mosquitto add-on) and the MQTT integration in HA.

## Configuration

| Option | Default | Description |
|---|---|---|
| `device_ip` | `192.168.0.45` | IP of the Marstek device |
| `device_udp_port` | `30000` | Open API port of the device |
| `device_ble_mac` | `""` | Leave empty – written back automatically after the first read |
| `device_type` | `""` | Leave empty – written back automatically after the first read |
| `udp_local_port` | `0` | Local port for answers. `0` = any free port. Use `30000` if your device always answers to its own port |
| `max_power` | `800` | Limit of the passive power slider (`800` or `1200` W) |
| `mqtt_*` | – | Broker connection, discovery prefix, base topic, suggested area |
| `queue_enabled` | `true` | Request queue on/off (see below) |
| `request_delay` | `2` | Seconds between requests (queue mode) |
| `request_timeout` | `5` | Seconds to wait for an answer |
| `request_retries` | `0` | `0` = no watchdog, `>0` = watchdog (queue mode) |
| `dod` | `88` | Depth of discharge 30–88 % |
| `led` | `true` | Panel LED |
| `ble_adv` | `false` | Bluetooth advertising (default off) |
| `passive_power` | `0` | Passive mode power in W (+ feed in, − charge) |
| `passive_cd_time` | `300` | Passive mode countdown in s |
| `query_*` | `true` | Which queries are enabled (creates/removes their entities) |
| `log_level` | `info` | `trace`, `debug`, `info`, `warning`, `error` |

### Single source of truth (write-back)

The configuration tab is the **only** place where values are stored. Everything
you change at runtime in HA (DOD, LED, Bluetooth, passive power/countdown, query
switches, communication settings, log level) is written back into the add-on
configuration through the Supervisor API. The add-on is **not** restarted by
this. After a restart the add-on continues with exactly these values.

## Startup / initialization

1. `Communication established` → **INIT**
2. `Marstek.GetDevice` – on success `device_type` and `device_ble_mac` are
   written back (if empty)
3. `Communication established` → **ON**
4. DOD, LED and Bluetooth advertising are sent with the last values
5. All enabled queries are requested once and published to MQTT

If the device does not answer, the state goes to **FAIL** and the
initialization is repeated every 30 s. The button **Reinitialize** starts it again
manually.

## Communication

Every request gets its own **id 0…999** (then 0 again), global over all
methods. `params.id` (instance) counts along with it.

**Queue ON** – requests are sent one after another:
send → wait up to `request_timeout` → retry up to `request_retries` → wait
`request_delay` → next request.
- `request_retries = 0`: no watchdog. A timeout is only logged.
- `request_retries > 0`: if all retries fail, `Communication established` goes to
  **FAIL**. The next answer of the device sets it back to **ON**.

**Queue OFF (fire mode)** – requests are sent immediately one after another,
each one waits up to `request_timeout` for its id. Never FAIL – the last data
stays. Use this to test whether the device can handle several requests at once.

In both modes **late answers** (after the timeout) are still processed, and
invalid JSON from the device (e.g. `ture`) is repaired and logged as WARNING.

Communication settings changed in HA are **pending** until you press
**Apply settings** (device *Communication*). The sensor *Active settings*
shows what is really active.

## Devices and entities

All devices are placed in the area `mqtt_suggested_area` (default **BalconyPowerPlant**).
Every query has its own sub-device, linked to the main device ("Connected devices").

```
Area BalconyPowerPlant
└─ BalconyPowerPlant                  Communication established, Reinitialize, Request all, Log level
   ├─ BalconyPowerPlant Control        Operating mode, Passive power, Passive countdown, Apply mode
   ├─ BalconyPowerPlant Settings       Depth of discharge, LED, Bluetooth advertising
   ├─ BalconyPowerPlant Communication  Request queue/delay/timeout/retries, Apply settings, Active settings, statistics
   ├─ BalconyPowerPlant Device info    Model, Firmware, Bluetooth MAC, WiFi MAC, WiFi name, IP address   (Marstek.GetDevice)
   ├─ BalconyPowerPlant WiFi           SSID, Signal, IP address, Gateway, Subnet mask, DNS                (Wifi.GetStatus)
   ├─ BalconyPowerPlant Bluetooth      State, MAC                                                         (BLE.GetStatus)
   ├─ BalconyPowerPlant Battery        SOC, Temperature, Remaining/Rated capacity, Charging/Discharging allowed (Bat.GetStatus)
   ├─ BalconyPowerPlant PV             PV1–PV4 power, voltage, current, working                           (PV.GetStatus)
   ├─ BalconyPowerPlant Energy system  SOC, Battery capacity, PV/Grid/Off-grid/Battery power, energy counters (ES.GetStatus)
   └─ BalconyPowerPlant Mode           Operating mode, CT connected                                       (ES.GetMode)
```

Every query device additionally contains **Enable query** (switch) and **Request** (button).

**When are entities created?**
- Main device, Control, Settings and Communication: at start (so you can see INIT / FAIL).
- Query devices: only after a **successful initialization**, their sensors after the
  **first answer** of that query.
- Query disabled: only *Enable query* remains in its device.

Entity ids follow the pattern `<domain>.balconypowerplant_<device>_<name>`, e.g.
`sensor.balconypowerplant_battery_temperature`, `button.balconypowerplant_battery_request`.

## Polling from Home Assistant

In this version the bridge does not poll by itself. Press the *Request*
buttons of the query devices from an automation, e.g. every 10 s:

```yaml
alias: BalconyPowerPlant polling
triggers:
  - trigger: time_pattern
    seconds: "/10"
actions:
  - action: button.press
    target:
      entity_id:
        - button.balconypowerplant_energy_system_request
        - button.balconypowerplant_battery_request
```

Passive control (repeat before the countdown expires):

```yaml
actions:
  - action: select.select_option
    target: { entity_id: select.balconypowerplant_control_operating_mode }
    data: { option: Passive }
  - action: number.set_value
    target: { entity_id: number.balconypowerplant_control_passive_power }
    data: { value: 300 }
  - action: button.press
    target: { entity_id: button.balconypowerplant_control_apply_mode }
```

Check the real entity ids in HA – they depend on your language/installation.

## MQTT topics

| Topic | Content |
|---|---|
| `<base>/availability` | `online` / `offline` |
| `<base>/state/<query>` | JSON result (`device`, `wifi`, `ble`, `battery`, `pv`, `es_status`, `es_mode`) |
| `<base>/state/communication` | `INIT` / `ON` / `FAIL` |
| `<base>/state/stats` | statistics JSON |
| `<base>/control/<name>` | current value of a control |
| `<base>/cmd/<name>` | commands (`request/<query>`, `request_all`, `apply_mode`, `dod`, `led`, …) |

## Logging

| Level | Color | Content |
|---|---|---|
| TRACE | grey | raw UDP datagrams and MQTT payloads |
| DEBUG | cyan | queued / sent / answered requests with round-trip time |
| INFO | green | normal operation |
| WARNING | yellow | timeouts, retries, late answers, repaired JSON |
| ERROR | red | failures, watchdog, device errors |

The log level can be changed at runtime with the select *Log level*.

## Notes on the API (Rev 2.0)

- There is no getter for DOD, LED and Bluetooth – HA shows the last value
  confirmed by the device (`set_result: true`).
- `Ble.Adv` is inverted: `enable: 0` = on, `1` = off. The bridge handles this.
- Passive mode falls back after `cd_time` – repeat *Apply mode* in time.
- If `core-mosquitto` cannot be resolved (host network), use `127.0.0.1`.

## Testing without a device

`tools/marstek_simulator.py` (in the repository) simulates a Venus A:

```bash
python3 tools/marstek_simulator.py --port 30000 --serial --delay 0.6 --drop 0.1 --typo
```
