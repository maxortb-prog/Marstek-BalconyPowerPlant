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
| `lf_*` | – | Load following, see below / siehe unten |
| `log_level` | `info` | `trace`, `debug`, `info`, `warning`, `error` |

### Eine einzige Wahrheit (Rückschreiben)

Der Konfigurations-Tab ist der **einzige** Speicherort. Alles, was du zur Laufzeit
im HA änderst (DOD, LED, Bluetooth, Passive-Leistung/-Nachlaufzeit,
Abfrage-Schalter, Kommunikationseinstellungen, Log-Level), wird über die
Supervisor-API in die Add-on-Konfiguration zurückgeschrieben. Das Add-on wird dabei
**nicht** neu gestartet. Nach einem Neustart arbeitet es mit genau diesen Werten weiter.

## Start / Initialisierung

Die Initialisierung ist eine **feste Sequenz** – jeder Schritt wartet auf seine Antwort:

1. `Communication established` → **INIT**
2. `Marstek.GetDevice` – `device_type` / `device_ble_mac` werden zurückgesichert (falls leer)
3. DOD, LED und Bluetooth-Advertising werden mit den letzten Werten gesendet
4. Alle aktiven Abfragen: WiFi, Bluetooth, Battery, PV, Mode
5. **ES.GetStatus als letzte** – `ongrid_power` ist der Bezugspunkt für die Übernahme durch Load following
6. `Communication established` → **ON**, Abfrage-Geräte werden in HA angelegt

Ein **Timeout bei irgendeinem Schritt bricht ab**: Status **FAIL**, die Bridge
wartet **lf_cd_time** (ein noch laufender Passive-Befehl ist dann abgelaufen und das
Gerät in einem definierten Zustand, meist Auto) und startet wieder bei Schritt 1.
Es wird nichts übersprungen. Während INIT / FAIL werden Abfragen und Befehle aus HA
ignoriert (geloggt). Mit **Reinitialize** startest du sie manuell neu.

Nach der Initialisierung behält ein Timeout einer einzelnen Abfrage nur die bisherigen Daten.

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
   ├─ BalconyPowerPlant Mode           Operating mode, CT connected, Grid-tied / Off-grid power, SOC, CT values   (ES.GetMode)
   └─ BalconyPowerPlant Load following  Load following, settings, state, diagnostics (ES.SetMode Passive)
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

## Load following (Lastfolgeregelung)

Regelt die Batterieausgabe nach der vom Zähler gemessenen Netzleistung (z. B.
Shelly auf Phase C), sodass Spitzen aus der Batterie abgefedert werden und
**nichts ins Netz eingespeist** wird. Verwendet `ES.SetMode` Passive mit Leistung
und `cd_time`.

### Sensor

Die Netzleistung wird **ereignisgesteuert** an das MQTT-Topic `lf_sensor_topic`
(Standard `Shelly Phase C RAW`) gesendet: reine Zahl in W, positiv = Bezug,
negativ = Einspeisung (JSON wie `{"power": 123}` geht auch). Beispiel-Automation:

```yaml
alias: BalconyPowerPlant Shelly Phase C RAW
triggers:
  - trigger: state
    entity_id: sensor.shelly_phase_c
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

### Funktionsweise

Der Zähler misst `Netz = Last − Batterieausgabe`. Der Regler rekonstruiert daher
die Last aus dem gesendeten Sollwert und hält eigene Sollwertänderungen nicht für
Lastwechsel (keine Überschwinger):

```
geschätzte Last = Netz (gemittelt) + aktiver Sollwert
Sollwert        = geschätzte Last − Ziel-Netzleistung       begrenzt auf [0 | Minimum … Grenze]
```

| Netzleistung (gemittelt) | Aktion |
|---|---|
| über **Deadband upper** (15 W) | hochregeln – Mittelwert über *Averaging time regulate up* (12 s), max. *Ramp up step* (100 W) je Schritt |
| zwischen unten und oben | Ruhe – nur Keepalive |
| unter **Deadband lower** (0 W) | sofort runterregeln – Mittelwert über *Averaging time regulate down* (5 s) |

- Regelziel ist **Target grid power** (8 W, leichter Bezug).
- Ein Sollwert unter **Minimum power** (50 W) wird als **0 W** gesendet.
- **Export reaction**: *Calculated* (Standard) springt direkt auf den berechneten
  Sollwert, *Minimum* fällt auf die Minimum-Leistung und regelt wieder hoch.
- Nach jeder Sollwertänderung wartet der Regler die **Settle time** (5 s).
- **Keepalive**: Muss nichts geändert werden, wird derselbe Sollwert bei
  **Keepalive at** (50 %) der **Countdown cd_time** (120 s) erneut gesendet.
- **Obergrenze** = *Passive power* (Gerät Control). Deine SOC-abhängige Automation
  kann weiter *Passive power* + *Apply mode* setzen: Bei aktivem Load following
  ändert das nur die Grenze, ans Gerät wird nichts gesendet. Fällt die Grenze unter
  den aktiven Sollwert, wird sofort reduziert.
- **Übernahme** (Start und Einschalten): Die aktuelle Ausgabe des Geräts wird mit
  `ES.GetStatus` (`ongrid_power`, positiv = Ausgabe) gelesen und unverändert als
  Passive-Sollwert gesendet – stoßfrei, die Lastschätzung stimmt ab dem ersten
  Schritt. Schlägt das Lesen fehl, wird es alle 10 s wiederholt; bis dahin wird nichts gesendet.
- **Verriegelung**: *Apply mode* mit Auto / AI / UPS schaltet Load following ab.
  Beim Ausschalten wird **nichts** gesendet: Ohne Keepalive fällt das Gerät nach
  Ablauf der `cd_time` selbst zurück (meist Auto).
- **Sensor timeout** (30 s) ohne Wert → Minimum-Leistung, Zustand `SENSOR_LOST`.
- Es wird darauf vertraut, dass das Gerät den Sollwert umsetzt (kein Rücklesen).
  Eine Plausibilitätsprüfung **loggt** nur einen *suspected dropout*, wenn der
  Netzbezug nach zwei Sollwerterhöhungen nicht sinkt.
- Regelbefehle haben **Vorrang** in der Queue (nur der neueste wird gesendet).
  Abfragen (z. B. ES/Bat-Polling aus HA) werden zurückgehalten, solange der Regler
  einschwingt (max. 30 s).

Deine alte Automation, die Passive zyklisch sendet, bei aktivem Load following deaktivieren.

### Zustände

| Zustand | Bedeutung |
|---|---|
| OFF | ausgeschaltet |
| WAITING | wartet auf Initialisierung / ersten Sensorwert / Übernahme-Bezugspunkt |
| REGULATING | Sollwert geändert, Einschwingen / Mittelwertbildung |
| IDLE | Netzleistung im Totband (oder Sollwert 0 W) |
| LIMITED | mehr Bezug, aber Sollwert an der Grenze |
| SENSOR_LOST | kein Sensorwert seit *Sensor timeout* |

Diagnose-Entities: Netzleistung (roh / Mittel hoch / Mittel runter), geschätzte
Last, aktiver und berechneter Sollwert, Leistungsgrenze, Übernahme-Bezugspunkt, letzter Befehl (Grund /
Zeit), Keepalive-Countdown, Zähler für Sollwertbefehle, Keepalives,
Einspeise-Ereignisse, vermutete Dropouts und Sensorwerte.

## MQTT-Topics

| Topic | Inhalt |
|---|---|
| `<base>/availability` | `online` / `offline` |
| `<base>/state/<abfrage>` | JSON-Ergebnis (`device`, `wifi`, `ble`, `battery`, `pv`, `es_status`, `es_mode`) |
| `<base>/state/communication` | `INIT` / `ON` / `FAIL` |
| `<base>/state/load_following` | load following state (JSON) |
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
