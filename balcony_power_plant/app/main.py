"""BalconyPowerPlant - Marstek Open API <-> Home Assistant (MQTT) bridge.

EN: Entry point. Loads the options, starts the UDP client and keeps the MQTT
    connection alive (automatic reconnect).
DE: Einstiegspunkt. Lädt die Optionen, startet den UDP-Client und hält die
    MQTT-Verbindung (automatischer Reconnect).
"""

from __future__ import annotations

import asyncio
import signal
import sys

import aiomqtt

from config import OptionsStore
from logger import get_logger, setup_logging
from version import VERSION

MQTT_RECONNECT_INTERVAL = 5  # seconds


async def run() -> None:
    opts = OptionsStore()
    setup_logging(str(opts["log_level"]))
    log = get_logger("main")
    log.info("BalconyPowerPlant v%s starting", VERSION)
    log.info("Device %s:%s, type %s, BLE MAC %s", opts["device_ip"], opts["device_udp_port"],
             opts.get("device_type") or "(not yet known)",
             opts.get("device_ble_mac") or "(not yet known)")

    from bridge import Bridge  # imported after logging setup

    bridge = Bridge(opts)
    await bridge.udp.start()
    stats_task = asyncio.create_task(bridge.stats_loop(), name="stats")

    will = aiomqtt.Will(bridge.t_avail, "offline", qos=0, retain=True)
    try:
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=str(opts["mqtt_host"]),
                    port=int(opts["mqtt_port"]),
                    username=opts.get("mqtt_username") or None,
                    password=opts.get("mqtt_password") or None,
                    identifier="balcony_power_plant",
                    will=will,
                    keepalive=60,
                ) as client:
                    log.info("Connected to MQTT broker %s:%s", opts["mqtt_host"], opts["mqtt_port"])
                    await bridge.on_connected(client)
                    async for message in client.messages:
                        await bridge.handle_message(message)
            except aiomqtt.MqttError as err:
                bridge.on_disconnected()
                log.error("MQTT connection to %s:%s lost/failed: %s - retry in %ds",
                          opts["mqtt_host"], opts["mqtt_port"], err, MQTT_RECONNECT_INTERVAL)
                await asyncio.sleep(MQTT_RECONNECT_INTERVAL)
    finally:
        stats_task.cancel()
        await bridge.stop()
        if bridge.client is not None:
            try:
                await bridge.client.publish(bridge.t_avail, "offline", retain=True)
            except aiomqtt.MqttError:
                pass
        if opts.dirty:
            await opts.flush()


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(run())
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, task.cancel)
        except NotImplementedError:
            pass
    try:
        loop.run_until_complete(task)
    except asyncio.CancelledError:
        pass
    finally:
        get_logger("main").info("BalconyPowerPlant stopped")
        loop.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
