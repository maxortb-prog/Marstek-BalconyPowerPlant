# Marstek BalconyPowerPlant – Home Assistant Add-on

🇩🇪 Deutsche Version: [README.de.md](README.de.md)

Home Assistant add-on that connects a **Marstek Venus** battery (Venus A/C/D/E)
to Home Assistant via the local **Marstek Open API Rev 2.0** (UDP) and **MQTT
discovery**. Everything appears in the area **BalconyPowerPlant**: one main device plus one sub-device per query.

## Features

- Queries: `Marstek.GetDevice`, `Wifi.GetStatus`, `BLE.GetStatus`, `Bat.GetStatus`,
  `PV.GetStatus`, `ES.GetStatus`, `ES.GetMode` – each one can be enabled/disabled
- Control: `ES.SetMode` (Auto / AI / UPS / Passive) with an *Apply mode* button
- Settings: `DOD.SET`, `Led.Ctrl`, `Ble.Adv`
- **Load following**: event driven zero-export regulation via Passive mode from a grid meter MQTT topic (e.g. Shelly)
- Request queue with delay / timeout / retries / watchdog, or fire mode
- Communication state INIT / ON / FAIL and request statistics
- Runtime changes are written back to the add-on configuration
- Colored logging with TRACE level

## Installation

1. Home Assistant → **Settings → Add-ons → Add-on Store → ⋮ → Repositories**
2. Add `https://github.com/maxortb-prog/Marstek-BalconyPowerPlant`
3. Install **BalconyPowerPlant**, adjust the configuration, start it.

Full documentation: [balcony_power_plant/DOCS.md](balcony_power_plant/DOCS.md)

## Repository layout

```
repository.yaml               add-on repository definition
balcony_power_plant/          the add-on (config, Dockerfile, app/)
tools/marstek_simulator.py    Venus A simulator for tests without hardware
```

## Versioning

[Semantic Versioning](https://semver.org). Changes: [CHANGELOG.md](balcony_power_plant/CHANGELOG.md)

## Disclaimer

The Marstek Local API is provided "as is" by Marstek. This project is not affiliated
with Marstek. Use at your own risk.
