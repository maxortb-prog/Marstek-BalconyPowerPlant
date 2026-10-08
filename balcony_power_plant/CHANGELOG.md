# Changelog

All notable changes to this add-on. / Alle Änderungen an diesem Add-on.
Format: [Keep a Changelog](https://keepachangelog.com), [Semantic Versioning](https://semver.org).

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
