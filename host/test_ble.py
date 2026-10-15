import asyncio
import json
import uuid

try:
    from .deck_auth import load_device_id
except ImportError:
    from deck_auth import load_device_id

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    BleakClient = None
    BleakScanner = None

SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
CHAR_UUID_RX = "beb5483e-36e1-4688-b7f5-ea07361b26a8"  # Write
CHAR_UUID_TX = "beb5483f-36e1-4688-b7f5-ea07361b26a8"  # Notify

response_received = asyncio.Event()
received_data = {}


def parse_deck_notification(text):
    """Parse ASCII token feedback from Claude-Deck firmware (e.g. ALLOW:<id>, DENY:<id>, SELECT:<id>:<idx>)."""
    text = text.strip()
    upper = text.upper()
    if upper.startswith("SELECT:"):
        parts = text.split(":")
        if len(parts) == 3:
            return {"decision": "select", "id": parts[1].lower(), "index": int(parts[2])}
    if ":" in text:
        token, req_id = text.split(":", 1)
    else:
        token, req_id = text, ""
    token = token.strip().upper()
    if token == "ALLOW":
        return {"decision": "allow", "id": req_id.lower()}
    if token == "DENY":
        return {"decision": "deny", "id": req_id.lower()}
    return {"raw": text}


def notification_handler(sender, data):
    global received_data
    try:
        text = data.decode("utf-8", errors="ignore").strip()
        print(f"\n[Deck 回传] 收到屏幕触控反馈: {text}")
        received_data = parse_deck_notification(text)
        response_received.set()
    except Exception as e:
        print(f"解析失败: {e}")


async def run_test():
    pinned_id = load_device_id()
    print("正在寻找已绑定的 Claude-Deck...")
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: d.address == pinned_id and d.name == "Claude-Deck",
        timeout=8.0,
    )
    if not device:
        print("未找到 Claude-Deck 设备，请确认板子是否通电且正在广播。")
        return

    print(f"连接到设备: {device.name} ({device.address})...")
    async with BleakClient(device) as client:
        print(f"已连接: {client.is_connected}")

        await client.start_notify(CHAR_UUID_TX, notification_handler)
        print("已订阅屏幕触控反馈通知。")

        req_id = uuid.uuid4().hex[:16]
        payload = {
            "type": "prompt",
            "id": req_id,
            "badge": "[BASH EXEC]",
            "tag": "HIGH RISK",
            "title": "终端命令执行",
            "desc": "执行: git status && git log -n 1",
            "options": ["允许 (Y)", "拒绝 (N)"],
            "timeout": 60,
        }
        raw_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        print(f"正在向屏幕推送测试问题卡片: [{req_id}] {payload['desc']}")
        await client.write_gatt_char(CHAR_UUID_RX, raw_bytes, response=True)

        print("\n>>> 请观察屏幕！屏幕上应该已经亮起黄色警示并显示 [允许] [拒绝] 按钮。")
        print(">>> 请用手指或触摸笔在屏幕上点击任一按钮（等待 60 秒）...")

        try:
            await asyncio.wait_for(response_received.wait(), timeout=60.0)
            print(f"\n[测试成功!] 收到用户的触摸选择: {received_data}")
        except asyncio.TimeoutError:
            print("\n等待触摸超时 (60s)，请检查屏幕或重试。")


if __name__ == "__main__":
    asyncio.run(run_test())
