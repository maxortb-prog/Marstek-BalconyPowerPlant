#!/usr/bin/env python3
"""Marstek Venus A simulator for testing the BalconyPowerPlant bridge.

EN: Answers the Open API methods over UDP like a Venus A would. Useful to test
    queue / fire mode, timeouts, late and malformed answers without hardware.
DE: Beantwortet die Open-API-Methoden per UDP wie ein Venus A. Zum Testen von
    Queue-/Fire-Modus, Timeouts, verspäteten und fehlerhaften Antworten.

Usage / Aufruf:
    python3 marstek_simulator.py --port 30000 --delay 0.3 --serial --drop 0.1 --typo
Options:
    --delay  s     answer delay per request                (Antwortverzögerung)
    --serial       process requests one after another       (nacheinander abarbeiten)
    --drop   p     probability to not answer (0..1)         (Antwort-Verlustrate)
    --typo         send "ture" like in the API doc          (ungültiges JSON senden)
    --reply-port   answer to this fixed port instead of the source port
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time


class VenusA:
    def __init__(self) -> None:
        self.mode = "Auto"
        self.passive_power = 0
        self.passive_until = 0.0
        self.dod = 88
        self.led = 1
        self.ble_adv = 1
        self.soc = 63
        self.t0 = time.time()

    def handle(self, method: str, params: dict) -> dict:
        if self.mode == "Passive" and time.time() > self.passive_until:
            self.mode = "Auto"   # countdown expired -> fall back
        if method == "Marstek.GetDevice":
            return {"device": "VenusA", "ver": 147, "ble_mac": "a1b2c3d4e5f6",
                    "wifi_mac": "f6e5d4c3b2a1", "wifi_name": "MY_HOME", "ip": "192.168.0.45"}
        if method == "Wifi.GetStatus":
            return {"id": params.get("id"), "wifi_mac": "f6e5d4c3b2a1", "ssid": "MY_HOME",
                    "rssi": -58, "sta_ip": "192.168.0.45", "sta_gate": "192.168.0.1",
                    "sta_mask": "255.255.255.0", "sta_dns": "192.168.0.1"}
        if method == "BLE.GetStatus":
            return {"id": params.get("id"), "state": "disconnect", "ble_mac": "a1b2c3d4e5f6"}
        if method == "Bat.GetStatus":
            return {"id": params.get("id"), "soc": str(self.soc), "charg_flag": True,
                    "dischrg_flag": True, "bat_temp": 24.5, "bat_capacity": 1420.0,
                    "rated_capacity": 2240.0}
        if method == "PV.GetStatus":
            out = {"id": params.get("id")}
            for i in range(1, 5):
                p = 120 * i if i <= 2 else 0
                out.update({f"pv{i}_power": p, f"pv{i}_voltage": 38 if p else 9,
                            f"pv{i}_current": round(p / 38, 2) if p else 0,
                            f"pv{i}_state": 1 if p else 0})
            return out
        if method == "ES.GetStatus":
            bat = -self.passive_power if self.mode == "Passive" else 0
            return {"id": params.get("id"), "bat_soc": self.soc, "bat_cap": 2240,
                    "pv_power": 360, "ongrid_power": self.passive_power if self.mode == "Passive" else 0,
                    "offgrid_power": 0, "bat_power": bat, "total_pv_energy": 12345,
                    "total_grid_output_energy": 2548, "total_grid_input_energy": 3273,
                    "total_load_energy": 0}
        if method == "ES.GetMode":
            return {"id": params.get("id"), "mode": self.mode, "ongrid_power": 0,
                    "offgrid_power": 0, "bat_soc": self.soc, "ct_state": 0}
        if method == "ES.SetMode":
            cfg = params.get("config", {})
            self.mode = cfg.get("mode", self.mode)
            if self.mode == "Passive":
                pc = cfg.get("passive_cfg", {})
                self.passive_power = int(pc.get("power", 0))
                self.passive_until = time.time() + int(pc.get("cd_time", 0))
            return {"id": params.get("id"), "set_result": True}
        if method == "DOD.SET":
            v = int(params.get("value", 0))
            ok = 30 <= v <= 88
            if ok:
                self.dod = v
            return {"id": params.get("id"), "set_result": ok}
        if method == "Led.Ctrl":
            self.led = int(params.get("state", 1))
            return {"id": params.get("id"), "set_result": True}
        if method == "Ble.Adv":
            self.ble_adv = int(params.get("enable", 1))
            return {"id": params.get("id"), "set_result": True}
        raise KeyError(method)


class Proto(asyncio.DatagramProtocol):
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.dev = VenusA()
        self.lock = asyncio.Lock()

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        asyncio.ensure_future(self.process(data, addr))

    async def process(self, data: bytes, addr) -> None:
        if self.args.serial:
            async with self.lock:
                await self._process(data, addr)
        else:
            await self._process(data, addr)

    async def _process(self, data: bytes, addr) -> None:
        try:
            req = json.loads(data)
        except json.JSONDecodeError:
            resp = {"id": 0, "src": "VenusA-a1b2c3d4e5f6",
                    "error": {"code": -32700, "message": "Parse error", "data": 402}}
        else:
            rid, method = req.get("id"), req.get("method")
            print(f"<< #{rid} {method} {req.get('params')}", flush=True)
            if random.random() < self.args.drop:
                print(f"   #{rid} dropped (simulated loss)", flush=True)
                return
            try:
                resp = {"id": rid, "src": "VenusA-a1b2c3d4e5f6",
                        "result": self.dev.handle(method, req.get("params") or {})}
            except KeyError:
                resp = {"id": rid, "src": "VenusA-a1b2c3d4e5f6",
                        "error": {"code": -32601, "message": "Method not found"}}
        await asyncio.sleep(self.args.delay)
        text = json.dumps(resp)
        if self.args.typo:
            text = text.replace("true", "ture")
        target = (addr[0], self.args.reply_port or addr[1])
        self.transport.sendto(text.encode(), target)
        print(f">> #{resp.get('id')} {text[:110]}", flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=30000)
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--serial", action="store_true")
    ap.add_argument("--drop", type=float, default=0.0)
    ap.add_argument("--typo", action="store_true")
    ap.add_argument("--reply-port", type=int, default=0)
    args = ap.parse_args()
    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(lambda: Proto(args), local_addr=(args.host, args.port))
    print(f"Venus A simulator listening on {args.host}:{args.port}", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
