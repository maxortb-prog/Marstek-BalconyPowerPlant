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
| `lf_*` | – | Load following, see below / siehe unten |
| `log_level` | `info` | `trace`, `debug`, `info`, `warning`, `error` |

### Single source of truth (write-back)

The configuration tab is the **only** place where values are stored. Everything
you change at runtime in HA (DOD, LED, Bluetooth, passive power/countdown, query
switches, communication settings, log level) is written back into the add-on
configuration through the Supervisor API. The add-on is **not** restarted by
this. After a restart the add-on continues with exactly these values.

## Startup / initialization

The initialization is a **strict sequence** – every step waits for its answer:

1. `Communication established` → **INIT**
2. `Marstek.GetDevice` – `device_type` / `device_ble_mac` are written back (if empty)
3. DOD, LED and Bluetooth advertising are sent with the last values
4. All enabled queries: WiFi, Bluetooth, Battery, PV, Mode
5. **ES.GetStatus last** – `ongrid_power` is the reference for the load following takeover
6. `Communication established` → **ON**, query devices are created in HA

A **timeout at any step aborts** the initialization: state **FAIL**, the bridge
waits **lf_cd_time** (so a still running Passive command has expired and the device
is in a defined state, usually Auto) and then starts again from step 1. Nothing is
skipped. While INIT / FAIL, requests and commands from HA are ignored (logged).
The button **Reinitialize** starts it again manually.

After the initialization, a timeout of a single request only keeps the previous data.

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
   ├─ BalconyPowerPlant Energy system  SOC, Battery capacity, PV/Grid/Off-grid/Battery power, energy counters, Total PV energy (corrected) (ES.GetStatus)
   ├─ BalconyPowerPlant Mode           Operating mode, CT connected, Grid-tied / Off-grid power, SOC, CT values   (ES.GetMode)
   └─ BalconyPowerPlant Load following  Load following, settings, state, diagnostics (ES.SetMode Passive)
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

## Load following

Regulates the battery output to the grid power measured by a meter (e.g. a Shelly
on phase C) so that peaks are covered from the battery and **nothing is fed into
the grid**. It uses `ES.SetMode` Passive with power and `cd_time`.

### Sensor

The grid power is sent **event driven** to the MQTT topic `lf_sensor_topic`
(default `Shelly Phase C RAW`): plain number in W, positive = import, negative =
export (JSON like `{"power": 123}` works as well). Example automation:

```yaml
alias: BalconyPowerPlant Shelly Phase C RAW
triggers:
  - trigger: state
    entity_id: sensor.shelly_phase_c
  - trigger: time_pattern      # heartbeat, keeps the sensor timeout quiet
    seconds: /5
conditions:
  - condition: template
    value_template: "{{ states('sensor.shelly_phase_c') not in ['unknown','unavailable','none',''] }}"
actions:
  - action: mqtt.publish
    data:
      topic: Shelly Phase C RAW
      payload: "{{ states('sensor.shelly_phase_c') | float(0) | round(0) }}"
      qos: 0
      retain: false
mode: queued
```

### How it works

The meter sees `grid = load − battery output`. The regulator therefore
reconstructs the load from the setpoint it has sent and does not mistake its own
changes for load changes (no overshoot):

```
estimated load = grid (averaged) + active setpoint
setpoint       = estimated load − target grid power        limited to [0 | minimum … limit]
```

| Grid power (averaged) | Action |
|---|---|
| above **Deadband upper** (15 W) | regulate up – average over *Averaging time regulate up* (12 s), max. *Ramp up step* (100 W) per step |
| between lower and upper | idle – only keep-alive |
| below **Deadband lower** (0 W) | regulate down immediately – average over *Averaging time regulate down* (5 s) |

- Averages are **time weighted** (a value is held until the next one arrives), so a
  sensor that only reports changes works correctly.
- Regulation target is **Target grid power** (8 W, slight import).
- A setpoint below **Minimum power** (50 W) is sent as **0 W**.
- **Export reaction**: *Calculated* (default) jumps directly to the calculated
  setpoint, *Minimum* drops to the minimum power and regulates up again.
- After every setpoint change the regulator waits **Settle time** (5 s).
- **Keep-alive**: if nothing has to be changed, the same setpoint is re-sent at
  **Keepalive at** (50 %) of **Countdown cd_time** (120 s).
- **Upper limit** = *Passive power* (device Control). Your SOC dependent automation
  can keep setting *Passive power* + *Apply mode*: while load following is on this
  only changes the limit, nothing is sent to the device. If the limit drops below
  the active setpoint, the setpoint is reduced immediately.
- **Takeover** (start and switching on): the current device output is read with
  `ES.GetStatus` (`ongrid_power`, positive = output) and sent unchanged as Passive
  setpoint – bumpless, the load estimate is correct from the first step. If the
  read fails it is repeated every 10 s; nothing is sent until then.
- **Interlock**: *Apply mode* with Auto / AI / UPS switches load following off.
  Switching load following off sends **nothing**: without keep-alive the device
  falls back by itself (usually to Auto) when `cd_time` expires.
- **Sensor timeout** (30 s) without a value → minimum power, state `SENSOR_LOST`.
- The device is trusted to execute the setpoint (no read-back). A plausibility
  check only **logs** a *suspected dropout* if the grid power does not drop after
  two setpoint increases.
- Setpoint commands have **priority** in the request queue (only the newest one
  is sent). Queries (e.g. ES/Bat polling from HA) are held back while the
  regulator is settling (max. 30 s).

Disable your old automation that sends Passive cyclically while load following is on.

### Dropout hold

The device sporadically falls out of regulation and drops its output to 0 W
(observed about every 2 min). Without countermeasure the regulator would chase
this (regulate up) and overshoot into export when the device comes back.

**Detection** (at least 3 s of settled grid values after the last setpoint change):
the grid power jumps up by about the **active setpoint** within one sample
(compared with the time weighted average of the 10 s before).
Tolerance = **max(Dropout tolerance W, Dropout tolerance percent × setpoint)**,
default max(15 W, 10 %) → ±15 W at 40 W, ±50 W at 500 W. The band is asymmetric:
the jump may be up to **tolerance + 15 W below** the setpoint, because the device does
not go exactly to 0 W (observed: jump = setpoint − 10…15 W). A partial jump (output
still ramping down) is confirmed within up to 3 s before regulating up.

**HOLD**: no regulating up; the setpoint is re-sent immediately (*Dropout resend
setpoint*), keep-alive continues.
- grid returns to the level before the jump → *recovered*, averaging restarts
- *Dropout hold time* (30 s) without recovery → real load change, normal regulation
- export during HOLD → leave HOLD and regulate down immediately

The sensor **Dropout interval** shows the time between the last two dropouts
(observed: very stable ~123 s, independent of our commands → internal cycle of the
device). Answers slower than 500 ms are logged as INFO and counted (*Slow responses*).

Every dropout is logged with the seconds since the last command / setpoint
change – useful to find out whether it is linked to keep-alive or cd_time.

**Quiet hold** (*Dropout resend setpoint* off): during HOLD nothing is sent at all – not
even a keep-alive (only if cd_time would otherwise expire within 10 s). The device's own
check runs undisturbed; the log shows "recovered … by itself". If it does not come back
within *Dropout hold time*, the setpoint − 1 W is sent once to wake it up, then the
regulator settles and averages fresh before regulating (no overshoot on wake-up).

**Reset suspected**: an ES.SetMode answer slower than 2 s or a timeout is logged as
"Device reset suspected" with the time since the last one, and counted (*Resets
suspected*, *Last reset suspected* in device Communication). No action is taken – the LED
switching back on stays your visible sign of a device reset.

### Device test guard

The device sporadically runs its own sensor test / reset (observed: ES.SetMode not answered
or after ~3 s, then several hundred W extra feed-in and/or charging, ~60 s in total, LED may
come back on). Reacting to it only causes a step afterwards (e.g. our 0 W → import).

**Triggers** (switch *Device test guard*, default on):
- ES.SetMode answer slower than 2 s or timeout ("Device reset suspected"), or
- **export larger than the active setpoint + 30 W** – impossible by load changes (the grid can
  drop at most to −setpoint), so the device feeds in on its own.

**State DEVICE_TEST**: everything is frozen – no regulation (also no export reaction), no
commands (except the cd_time guard). If we changed the setpoint within the last 15 s (a
reaction to the test itself), the previous setpoint is restored once.
**End**: grid back at the level before for 5 s, no deviation within 30 s (false alarm), or
*Device test hold time* (75 s). Then settle + fresh averaging.
Diagnostics: *Device tests*, *Device test elapsed*, *Last device test duration*.

### Keepalive jitter (test function)

Hypothesis: the device's own regulation (it is also coupled to the Shelly) might
misbehave when the output is very constant. With **Keepalive jitter** on, a probe runs
every **Jitter interval** seconds (default 120 s, independent of the keep-alive; its
commands refresh cd_time as well). Sensor *Next jitter in* shows the countdown:

1. send **setpoint − Jitter amount %** (only downwards, default 50 %)
2. wait *Settle time*, then average over *Averaging time regulate up*
3. evaluate: grid rose by about the jitter → *followed*; less than half → *NOT followed*
   (warning); much more → *inconclusive* (load change or dropout)
4. send the previous setpoint again, wait *Settle time*, normal operation

During the jitter nothing is regulated up; the fast export protection and the dropout
detection stay active (a dropout during the jitter aborts it and starts HOLD).
Also **at the limit** (LIMITED, downwards) and **at 0 W** (e.g. battery empty / limit 0 W
from HA): then the output is raised so that the import is compensated down to the target
(grid ≈ 8 W, no export), **max. 100 W**, and set back to 0 – also when the limit from HA is
0 W (skipped below 10 W import). No settings needed; the timing follows the keep-alive
(*Keepalive at* % of *Countdown cd_time*). No jitter in HOLD.
Example: 500 W → 250 W for ~17 s; at 0 W with 60 W import → +52 W, with 300 W import → +100 W.
Counters: *Jitter probes / followed / not followed / inconclusive*. Watch
*Dropout interval* to see whether the ~123 s cycle changes.

### States

| State | Meaning |
|---|---|
| OFF | switched off |
| WAITING | waiting for initialization / first sensor value / takeover reference |
| REGULATING | setpoint changed, settling / averaging |
| IDLE | grid power inside the deadband (or setpoint 0 W) |
| LIMITED | more import, but the setpoint is at the limit |
| JITTER | keep-alive jitter test running |
| DEVICE_TEST | device runs its own sensor test / reset, regulation frozen |
| HOLD | dropout detected, holding the setpoint and waiting for recovery |
| SENSOR_LOST | no sensor value for *Sensor timeout* |

Diagnostic entities: grid power (raw / average up / average down), estimated
load, active and calculated setpoint, power limit, takeover reference, last command (reason / time),
keep-alive countdown, counters for setpoint commands, keep-alives, export events,
suspected dropouts and sensor values.

## MQTT topics

| Topic | Content |
|---|---|
| `<base>/availability` | `online` / `offline` |
| `<base>/state/<query>` | JSON result (`device`, `wifi`, `ble`, `battery`, `pv`, `es_status`, `es_mode`) |
| `<base>/state/communication` | `INIT` / `ON` / `FAIL` |
| `<base>/state/load_following` | load following state (JSON) |
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
- `total_pv_energy` (ES.GetStatus) is reported 10× too small by the firmware (doc: [Wh]).
  *Total PV energy (corrected)* = raw value × 10; the raw value stays available for comparison.
- Passive mode falls back after `cd_time` – repeat *Apply mode* in time.
- If `core-mosquitto` cannot be resolved (host network), use `127.0.0.1`.

## Testing without a device

`tools/marstek_simulator.py` (in the repository) simulates a Venus A:

```bash
python3 tools/marstek_simulator.py --port 30000 --serial --delay 0.6 --drop 0.1 --typo
```
