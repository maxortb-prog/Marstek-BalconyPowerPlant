# Changelog

All notable changes to this add-on. / Alle Änderungen an diesem Add-on.
Format: [Keep a Changelog](https://keepachangelog.com), [Semantic Versioning](https://semver.org).

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
