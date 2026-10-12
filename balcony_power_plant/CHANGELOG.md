# Changelog

All notable changes to this add-on. / Alle Änderungen an diesem Add-on.
Format: [Keep a Changelog](https://keepachangelog.com), [Semantic Versioning](https://semver.org).

## [0.3.9] – 2026-10-12

### Added / Neu
- **Device test guard**: freezes the regulation while the device runs its own sensor test /
  reset. Triggered by a slow/missing ES.SetMode answer or by an export larger than the
  active setpoint + 30 W (physically impossible by load changes). Reverts our own change of
  the last 15 s, ends when the grid is back at the previous level (max. *Device test hold
  time*, 75 s). New state DEVICE_TEST, switch, number and diagnostics.
  / Regelung einfrieren, während das Gerät seinen eigenen Sensortest / Reset fährt.
- Simulator: `--test-at`, `--test-every` (device sensor test as observed on 2026-10-12 16:20)

## [0.3.8] – 2026-10-12

### Added / Neu
- **Quiet hold**: with *Dropout resend setpoint* off nothing is sent during HOLD (keep-alive
  only if cd_time would expire within 10 s); on hold timeout the setpoint − 1 W is sent once
  to wake the device, then settle + fresh averaging. For the A/B test whether our commands
  disturb the device's own sensor check. / Stilles Halten für den A/B-Test.
- **Reset suspected** logging: ES.SetMode slower than 2 s or timeout -> WARNING with time
  since the last one, counter *Resets suspected*, sensor *Last reset suspected*.
  / Vermuteter Geräte-Reset wird geloggt und gezählt (keine Aktion).

## [0.3.7] – 2026-10-10

### Added / Neu
- *Jitter interval* (s, default 120): the jitter test has its own timing, independent of
  the keep-alive; sensor *Next jitter in*. / Eigenes Jitter-Intervall, unabhängig vom Keepalive.

### Fixed / Behoben
- An exception while processing a grid sensor value can no longer stop the whole bridge
  (it is logged instead). / Fehler beim Verarbeiten eines Sensorwerts beendet die Bridge nicht mehr.

## [0.3.6] – 2026-10-10

### Changed / Geändert
- Jitter at 0 W: fixed rule instead of a setting – the import is compensated down to the
  target (no export), max. 100 W. Number *Jitter amount at 0 W* removed (also from HA).
  / Jitter bei 0 W: Bezug wird bis aufs Regelziel ausgeglichen, max. 100 W; Slider entfernt.
- Options write-back sends only options known to this version.

## [0.3.5] – 2026-10-10

### Added / Neu
- Keepalive jitter also at **0 W** (upwards by *Jitter amount at 0 W*, default 50 W, limited
  to import − target, also when the limit from HA is 0 W) and **at the limit** (LIMITED,
  downwards) – the output is constant there as well.
  / Jitter auch bei 0 W (nach oben, begrenzt auf Bezug − Ziel) und am Deckel (nach unten)

## [0.3.4] – 2026-10-10

### Added / Neu
- **Keepalive jitter** (test function, default off): every keep-alive lowers the setpoint
  by *Jitter amount* % (default 50 %), checks via the grid sensor whether the device follows
  (followed / not followed / inconclusive) and returns to the previous setpoint. New state
  JITTER, switch *Keepalive jitter*, number *Jitter amount*, four counters.
  / Jeder Keepalive senkt den Sollwert um x %, prüft ob das Gerät folgt, und kehrt zurück.

### Changed / Geändert
- Dropout detection works from 3 s of settled values after any setpoint change (before:
  only when idle for 10 s), also during the jitter (a dropout aborts the jitter -> HOLD)
  / Dropout-Erkennung schon 3 s nach dem Einschwingen, auch während des Jitters

## [0.3.3] – 2026-10-10

### Changed / Geändert
- Dropout detection: asymmetric band – the jump may be up to tolerance + 15 W below the
  setpoint (device does not drop exactly to 0 W; a dropout at 152 W with a +136 W jump
  was missed in 0.3.2) / Asymmetrisches Erkennungsband (Sprung bis Toleranz + 15 W unter
  dem Sollwert)

### Added / Neu
- Sensor *Dropout interval* (time between the last two dropouts) and interval in the log
  / Abstand zwischen zwei Dropouts
- Answers slower than 500 ms are logged as INFO, sensor *Slow responses*
  / Langsame Antworten (> 500 ms) werden geloggt und gezählt
- Simulator: `--dropout-residual`

## [0.3.2] – 2026-10-10

### Added / Neu
- Load following **Dropout hold**: detects the device dropping its output to 0 W
  (grid jumps by ~setpoint, tolerance max(15 W, 10 %)), re-sends the setpoint immediately
  and holds instead of regulating up; recovery / hold timeout (30 s) / export handling.
  New state HOLD, switches *Dropout detection*, *Dropout resend setpoint*, numbers
  *Dropout tolerance*, *Dropout tolerance percent*, *Dropout hold time*, diagnostics
  (dropouts, recoveries, timeouts, last dropout duration, hold elapsed).
  / Erkennt Aussetzer des Geräts, sendet den Sollwert sofort erneut und hält statt
  hochzuregeln. Log mit Sekunden seit letztem Befehl zur Ursachenanalyse.
- Simulator: `--dropout-every`

### Fixed / Behoben
- Averages are now time weighted with sample-and-hold. Before, a sensor that only sends
  on change produced empty averaging windows (state flapping IDLE/REGULATING, delayed
  reactions). / Mittelwerte zeitgewichtet; vorher leere Fenster bei Sensor, der nur bei
  Änderung sendet.

## [0.3.1] – 2026-10-10

### Changed / Geändert
- Initialization is a strict sequence (GetDevice, DOD, LED, BLE, enabled queries,
  **ES.GetStatus last**). A timeout at any step aborts: FAIL -> wait **lf_cd_time** ->
  start again. Nothing is skipped; requests/commands from HA are ignored meanwhile.
  / Initialisierung als feste Sequenz, ES.GetStatus zuletzt. Timeout bei jedem Schritt:
  FAIL -> lf_cd_time warten -> neu starten. Nichts wird übersprungen.
- Load following **takeover**: start reference = device output (`ongrid_power` from
  ES.GetStatus), sent unchanged as Passive setpoint (bumpless). No more start at 0 W.
  / Stoßfreie Übernahme mit der aktuellen Geräteausgabe statt Start bei 0 W.
- Switching load following off sends nothing anymore; the device falls back after
  cd_time. / Beim Ausschalten wird nichts mehr gesendet.

### Added / Neu
- Device *Mode*: raw values from ES.GetMode – Grid-tied power, Off-grid power, SOC,
  CT phase A/B/C / total power, CT input/output energy (×0.1)
  / Rohwerte aus ES.GetMode
- Load following: sensor *Takeover reference*
- Simulator: `--auto-output`, `--hang-after` / `--hang-for`

## [0.3.0] – 2026-10-09

### Added / Neu
- **Load following** (Lastfolgeregelung): event driven regulation of the battery output
  via `ES.SetMode` Passive to the grid power from an MQTT topic (e.g. Shelly phase C),
  no feed-in. New sub-device *BalconyPowerPlant Load following*.
  / Ereignisgesteuerte Regelung der Batterieausgabe nach der Netzleistung aus einem
  MQTT-Topic, ohne Einspeisung. Neues Unter-Gerät *BalconyPowerPlant Load following*.
  - Load reconstruction from the sent setpoint (no overshoot) / Lastrekonstruktion
  - Asymmetric deadband and averaging (up 12 s / down 5 s), ramp up step, settle time
  - Setpoint below minimum power -> 0 W; upper limit = *Passive power*
  - Keep-alive at % of cd_time, sensor timeout, plausibility check (log only)
  - Diagnostic entities: state, averages, estimated load, setpoints, counters
- Request queue: priority for control commands, newest setpoint replaces a queued one,
  queries are held back while regulating (max. 30 s)
  / Queue: Vorrang für Steuerbefehle, Abfragen werden beim Nachregeln zurückgehalten
- Simulator: household / grid meter simulation (`--grid-topic`, `--load-steps`, `--dropout-at`)

### Changed / Geändert
- *Apply mode* while load following is on: Passive only updates the power limit,
  Auto / AI / UPS switches load following off
  / *Apply mode* bei aktivem Load following: Passive ändert nur die Grenze,
  Auto / AI / UPS schaltet Load following ab

## [0.2.1] – 2026-10-09

### Added / Neu
- Sensor *Total PV energy (corrected)* in device *Energy system*: `total_pv_energy` × 10 (Wh),
  usable in the energy dashboard. The firmware reports this counter 10× too small.
  / Sensor *Total PV energy (corrected)*: `total_pv_energy` × 10 (Wh), fürs Energie-Dashboard
  geeignet. Die Firmware liefert diesen Zähler um Faktor 10 zu klein.
- Raw sensor *Total PV energy* is kept for comparison (e.g. after a firmware fix)
  / Rohwert-Sensor *Total PV energy* bleibt zum Vergleich erhalten

## [0.2.0] – 2026-10-09

### Changed / Geändert
- Entities are grouped into sub-devices per query, linked to the main device
  *BalconyPowerPlant* / Entities nach Abfragen in Unter-Geräte gegliedert
  (Control, Settings, Communication, Device info, WiFi, Bluetooth, Battery, PV,
  Energy system, Mode)
- *Enable query* and *Request* moved into the respective query device
  / *Enable query* und *Request* liegen jetzt im jeweiligen Abfrage-Gerät
- Query devices are created only after a successful initialization, their sensors
  after the first answer / Abfrage-Geräte erst nach erfolgreicher Initialisierung,
  Sensoren erst nach der ersten Antwort
- Default area `mqtt_suggested_area` is now **BalconyPowerPlant**
- Shorter entity names inside the devices – **entity ids change**
  / Kürzere Entity-Namen – **Entity-IDs ändern sich**

### Migration from 0.1.0 / Umstieg von 0.1.0
1. Update the add-on, then stop it / Add-on aktualisieren, dann stoppen
2. Delete the device *BalconyPowerPlant* in HA (Settings → Devices → MQTT)
   / Gerät *BalconyPowerPlant* in HA löschen
3. Start the add-on – all devices are created again in the area BalconyPowerPlant
   / Add-on starten – alle Geräte werden im Bereich BalconyPowerPlant neu angelegt
4. Adjust automations to the new entity ids / Automationen an neue Entity-IDs anpassen

## [0.1.0] – 2026-10-08

### Added / Neu
- Initial release for testing with Venus A / Erste Version zum Testen mit dem Venus A
- Queries / Abfragen: Marstek.GetDevice, Wifi.GetStatus, BLE.GetStatus, Bat.GetStatus,
  PV.GetStatus, ES.GetStatus, ES.GetMode (each enable/disable / einzeln schaltbar)
- ES.SetMode (Auto, AI, UPS, Passive) with *Apply mode* button
- DOD.SET (default 88 %), Led.Ctrl, Ble.Adv (default off), sent at every start
- Initialization with *Communication established* INIT / ON / FAIL
- Write-back of device_type / device_ble_mac and runtime changes to the add-on options
- Global request id 0–999, params.id counts along
- Queue mode (delay, timeout, retries, watchdog) and fire mode, switchable at runtime
- Late answers are still processed, invalid JSON (e.g. `ture`) is repaired
- Request statistics entities, colored logging with TRACE level
- Venus A simulator (`tools/marstek_simulator.py`)
