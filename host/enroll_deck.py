#!/usr/bin/env python3
"""Pair with a physical deck and pin its CoreBluetooth identity on this Mac."""

import asyncio
import json
import os
import secrets

from bleak import BleakClient, BleakScanner

from deck_auth import DEVICE_ID_PATH, load_token


CHAR_UUID_RX = "beb5483e-36e1-4688-b7f5-ea07361b26a8"


async def main():
    load_token(create=True)
    try:
        discovered = await asyncio.wait_for(BleakScanner.discover(timeout=8), timeout=15)
    except asyncio.TimeoutError:
        raise SystemExit("Mac 蓝牙扫描未就绪；请检查蓝牙是否开启，并允许终端使用蓝牙。") from None
    devices = [d for d in discovered if d.name == "Claude-Deck"]
    if not devices:
        raise SystemExit("未发现 Claude-Deck；请确认副屏已上电。")
    for index, device in enumerate(devices, 1):
        print(f"{index}. {device.name} — {device.address}")
    choice = int(input("选择要绑定的副屏编号: ")) - 1
    if not 0 <= choice < len(devices):
        raise SystemExit("无效编号")
    device = devices[choice]

    print("即将触发蓝牙配对。请只输入真实副屏屏幕上显示的六位数字。")
    async with BleakClient(device) as client:
        payload = json.dumps({"type": "status", "status": "idle"}).encode()
        await client.write_gatt_char(CHAR_UUID_RX, payload, response=True)
    confirmed = input("确认 Mac 已用副屏显示的数字完成配对，且副屏显示 ONLINE？输入 yes: ").strip()
    if confirmed != "yes":
        raise SystemExit("未确认实体副屏，绑定已取消。")

    temp = DEVICE_ID_PATH.with_name(f"device-id.{secrets.token_hex(8)}.tmp")
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(device.address + "\n")
        os.replace(temp, DEVICE_ID_PATH)
    finally:
        temp.unlink(missing_ok=True)
    print(f"已绑定 {device.address}。现在可以启动 host/start_deck.sh。")


if __name__ == "__main__":
    asyncio.run(main())
