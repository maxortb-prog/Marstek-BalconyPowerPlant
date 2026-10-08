# Marstek BalconyPowerPlant – Home-Assistant-Add-on

🇬🇧 English version: [README.md](README.md)

Home-Assistant-Add-on, das einen **Marstek-Venus**-Speicher (Venus A/C/D/E) über die
lokale **Marstek Open API Rev 2.0** (UDP) und **MQTT-Discovery** mit Home Assistant
verbindet. Alles erscheint im Bereich **BalconyPowerPlant**: ein Hauptgerät plus ein Unter-Gerät je Abfrage.

## Funktionen

- Abfragen: `Marstek.GetDevice`, `Wifi.GetStatus`, `BLE.GetStatus`, `Bat.GetStatus`,
  `PV.GetStatus`, `ES.GetStatus`, `ES.GetMode` – jede einzeln ein-/ausschaltbar
- Steuerung: `ES.SetMode` (Auto / AI / UPS / Passive) mit Button *Apply mode*
- Einstellungen: `DOD.SET`, `Led.Ctrl`, `Ble.Adv`
- Anfrage-Queue mit Delay / Timeout / Retries / Watchdog oder Fire-Modus
- Kommunikationsstatus INIT / ON / FAIL und Anfrage-Statistik
- Laufzeitänderungen werden in die Add-on-Konfiguration zurückgeschrieben
- Farbiges Logging mit TRACE-Level

## Installation

1. Home Assistant → **Einstellungen → Add-ons → Add-on Store → ⋮ → Repositories**
2. `https://github.com/maxortb-prog/Marstek-BalconyPowerPlant` hinzufügen
3. **BalconyPowerPlant** installieren, Konfiguration anpassen, starten.

Ausführliche Doku: [balcony_power_plant/DOCS.de.md](balcony_power_plant/DOCS.de.md)

## Aufbau des Repositorys

```
repository.yaml               Definition des Add-on-Repositorys
balcony_power_plant/          das Add-on (Config, Dockerfile, app/)
tools/marstek_simulator.py    Venus-A-Simulator zum Testen ohne Hardware
```

## Versionierung

[Semantic Versioning](https://semver.org). Änderungen: [CHANGELOG.md](balcony_power_plant/CHANGELOG.md)

## Haftungsausschluss

Die Local API wird von Marstek „wie sie ist“ bereitgestellt. Dieses Projekt steht in
keiner Verbindung zu Marstek. Nutzung auf eigenes Risiko.
