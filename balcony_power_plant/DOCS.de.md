# BalconyPowerPlant – Dokumentation

🇬🇧 English version: [DOCS.md](DOCS.md)

Lokale Brücke zwischen einem **Marstek-Venus**-Speicher (Zielgerät: **Venus A**)
und Home Assistant. Die Kommunikation läuft über die **Marstek Open API Rev 2.0
(UDP, JSON-RPC)**, alle Daten gehen per **MQTT-Discovery** als Gerät
**BalconyPowerPlant** ins HA.

> Die Local API wird von Marstek „wie sie ist“ bereitgestellt. Nutzung auf eigenes Risiko.

## Voraussetzungen

1. Open API in der Marstek-App aktiviert (UDP-Port, Standard `30000`).
2. Gerät mit fester IP (DHCP-Reservierung).
3. MQTT-Broker (z. B. Mosquitto-Add-on) und MQTT-Integration im HA.

## Konfiguration

| Option | Standard | Beschreibung |
|---|---|---|
| `device_ip` | `192.168.0.45` | IP des Marstek-Geräts |
| `device_udp_port` | `30000` | Open-API-Port des Geräts |
| `device_ble_mac` | `""` | Leer lassen – wird nach dem ersten Auslesen automatisch zurückgesichert |
| `device_type` | `""` | Leer lassen – wird nach dem ersten Auslesen automatisch zurückgesichert |
| `udp_local_port` | `0` | Lokaler Port für Antworten. `0` = beliebiger freier Port. `30000`, falls das Gerät immer an seinen eigenen Port antwortet |
| `max_power` | `800` | Grenze des Passive-Leistungs-Sliders (`800` oder `1200` W) |
| `mqtt_*` | – | Broker, Discovery-Präfix, Basis-Topic, vorgeschlagener Bereich |
| `queue_enabled` | `true` | Anfrage-Queue ein/aus (siehe unten) |
| `request_delay` | `2` | Sekunden zwischen den Anfragen (Queue-Modus) |
| `request_timeout` | `5` | Sekunden Wartezeit auf eine Antwort |
| `request_retries` | `0` | `0` = kein Watchdog, `>0` = Watchdog (Queue-Modus) |
| `dod` | `88` | Entladetiefe 30–88 % |
| `led` | `true` | LED am Panel |
| `ble_adv` | `false` | Bluetooth-Advertising (standardmäßig aus) |
| `passive_power` | `0` | Leistung im Passive-Modus in W (+ einspeisen, − laden) |
| `passive_cd_time` | `300` | Nachlaufzeit im Passive-Modus in s |
| `query_*` | `true` | Welche Abfragen aktiv sind (legt ihre Entities an / entfernt sie) |
| `log_level` | `info` | `trace`, `debug`, `info`, `warning`, `error` |

### Eine einzige Wahrheit (Rückschreiben)

Der Konfigurations-Tab ist der **einzige** Speicherort. Alles, was du zur Laufzeit
im HA änderst (DOD, LED, Bluetooth, Passive-Leistung/-Nachlaufzeit,
Abfrage-Schalter, Kommunikationseinstellungen, Log-Level), wird über die
Supervisor-API in die Add-on-Konfiguration zurückgeschrieben. Das Add-on wird dabei
**nicht** neu gestartet. Nach einem Neustart arbeitet es mit genau diesen Werten weiter.

## Start / Initialisierung

1. `Communication established` → **INIT**
2. `Marstek.GetDevice` – bei Erfolg werden `device_type` und `device_ble_mac`
   zurückgesichert (falls leer)
3. `Communication established` → **ON**
4. DOD, LED und Bluetooth-Advertising werden mit den letzten Werten gesendet
5. Alle aktiven Abfragen werden einmal ausgeführt und an MQTT gesendet

Antwortet das Gerät nicht, geht der Status auf **FAIL**, und die Initialisierung
wird alle 30 s wiederholt. Mit dem Button **Reinitialize** startest du sie manuell neu.

## Kommunikation

Jede Anfrage bekommt eine eigene **ID 0…999** (danach wieder 0), global über alle
Methoden. `params.id` (Instance) zählt mit.

**Queue EIN** – Anfragen werden nacheinander gesendet:
senden → bis `request_timeout` warten → bis zu `request_retries` wiederholen →
`request_delay` warten → nächste Anfrage.
- `request_retries = 0`: kein Watchdog. Ein Timeout wird nur geloggt.
- `request_retries > 0`: Schlagen alle Wiederholungen fehl, geht
  `Communication established` auf **FAIL**. Die nächste Antwort setzt ihn wieder auf **ON**.

**Queue AUS (Fire-Modus)** – Anfragen werden sofort nacheinander rausgeschickt,
jede wartet bis `request_timeout` auf ihre ID. Nie FAIL – die letzten Daten
bleiben erhalten. Damit kannst du testen, ob das Gerät mehrere Anfragen gleichzeitig verarbeitet.

In beiden Modi werden **verspätete Antworten** (nach dem Timeout) trotzdem
verarbeitet. Ungültiges JSON vom Gerät (z. B. `ture`) wird repariert und als WARNING geloggt.

Im HA geänderte Kommunikationseinstellungen sind **vorgemerkt**, bis du
**Apply settings** (device *Communication*) drückst. Der Sensor *Active settings*
zeigt, was tatsächlich aktiv ist.

## Geräte und Entities

Alle Geräte liegen im Bereich `mqtt_suggested_area` (Standard **BalconyPowerPlant**).
Jede Abfrage hat ein eigenes Unter-Gerät, verknüpft mit dem Hauptgerät („Verbundene Geräte“).

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
   └─ BalconyPowerPlant Mode           Operating mode, CT connected                                       (ES.GetMode)
```

Jedes Abfrage-Gerät enthält zusätzlich **Enable query** (Schalter) und **Request** (Button).
Die Entity-Namen sind englisch (festgelegt).

**Wann werden Entities angelegt?**
- Hauptgerät, Control, Settings und Communication: beim Start (damit INIT / FAIL sichtbar ist).
- Abfrage-Geräte: erst nach **erfolgreicher Initialisierung**, ihre Sensoren erst nach der
  **ersten Antwort** dieser Abfrage.
- Abfrage deaktiviert: im Gerät bleibt nur *Enable query*.

Entity-IDs folgen dem Muster `<domain>.balconypowerplant_<gerät>_<name>`, z. B.
`sensor.balconypowerplant_battery_temperature`, `button.balconypowerplant_battery_request`.

## Abfragen aus Home Assistant

In dieser Version fragt die Bridge nicht selbst zyklisch ab. Löse die
*Request*-Buttons der Abfrage-Geräte per Automation aus, z. B. alle 10 s:

```yaml
alias: BalconyPowerPlant Polling
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

Passive-Steuerung (rechtzeitig vor Ablauf der Nachlaufzeit wiederholen):

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

Die tatsächlichen Entity-IDs bitte im HA prüfen.

## MQTT-Topics

| Topic | Inhalt |
|---|---|
| `<base>/availability` | `online` / `offline` |
| `<base>/state/<abfrage>` | JSON-Ergebnis (`device`, `wifi`, `ble`, `battery`, `pv`, `es_status`, `es_mode`) |
| `<base>/state/communication` | `INIT` / `ON` / `FAIL` |
| `<base>/state/stats` | Statistik als JSON |
| `<base>/control/<name>` | aktueller Wert eines Steuerelements |
| `<base>/cmd/<name>` | Befehle (`request/<abfrage>`, `request_all`, `apply_mode`, `dod`, `led`, …) |

## Logging

| Level | Farbe | Inhalt |
|---|---|---|
| TRACE | grau | rohe UDP-Pakete und MQTT-Payloads |
| DEBUG | cyan | eingereiht / gesendet / beantwortet mit Antwortzeit |
| INFO | grün | normaler Betrieb |
| WARNING | gelb | Timeouts, Retries, verspätete Antworten, repariertes JSON |
| ERROR | rot | Fehler, Watchdog, Gerätefehler |

Das Log-Level lässt sich zur Laufzeit über das Select *Log level* umstellen.

## Hinweise zur API (Rev 2.0)

- Für DOD, LED und Bluetooth gibt es keine Get-Methode. HA zeigt den zuletzt vom
  Gerät bestätigten Wert (`set_result: true`).
- `Ble.Adv` ist invertiert: `enable: 0` = ein, `1` = aus. Die Bridge berücksichtigt das.
- `total_pv_energy` (ES.GetStatus) wird von der Firmware um den Faktor 10 zu klein geliefert (Doku: [Wh]).
  *Total PV energy (corrected)* = Rohwert × 10; der Rohwert bleibt zum Vergleich erhalten.
- Der Passive-Modus fällt nach `cd_time` zurück, daher *Apply mode* rechtzeitig wiederholen.
- Falls `core-mosquitto` nicht auflösbar ist (Host-Netzwerk), `127.0.0.1` verwenden.

## Testen ohne Gerät

`tools/marstek_simulator.py` (im Repository) simuliert einen Venus A:

```bash
python3 tools/marstek_simulator.py --port 30000 --serial --delay 0.6 --drop 0.1 --typo
```
