#!/usr/bin/env python3
"""A macOS Terminal companion for questions shared with Claude Deck."""

import argparse
import base64
import json
import select
import sys
import time
import urllib.error
import urllib.request

try:
    from .deck_auth import load_token
except ImportError:
    from deck_auth import load_token


BASE_URL = "http://127.0.0.1:19842"


def clean(value):
    return "".join(char for char in str(value or "") if char >= " " and char != "\x7f")


def request(path, data=None):
    headers = {"X-Claude-Deck-Token": load_token()}
    if data is not None:
        data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def submit(request_id, index):
    try:
        request("/question_answer", {"id": request_id, "index": index})
        return True
    except urllib.error.HTTPError as error:
        if error.code == 409:
            return False
        raise


def pending(request_id):
    try:
        return bool(request("/question_status?id=" + request_id).get("pending"))
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--payload", required=True)
    args = parser.parse_args()
    payload = json.loads(base64.urlsafe_b64decode(args.payload.encode("ascii")))
    options = payload.get("options", [])

    print("\n╭──────────────── Claude Deck ────────────────╮")
    print("│ 同一问题也在实体副屏上，可从任一端先完成。    │")
    print("╰─────────────────────────────────────────────╯")
    print(f"\n{clean(payload.get('header', 'QUESTION'))}\n{clean(payload.get('question'))}\n")
    for number, option in enumerate(options, start=1):
        label = clean(option.get("label") if isinstance(option, dict) else option)
        description = clean(option.get("description", "") if isinstance(option, dict) else "")
        print(f"  {number}. {label}")
        if description:
            print(f"     {description}")
    print("\n  0. 改在 Claude 原生问题框输入")
    print("\n输入编号后按 Enter；副屏先回答时此窗口会自动失效。")
    sys.stdout.write("> ")
    sys.stdout.flush()

    deadline = time.monotonic() + max(5, int(payload.get("timeout", 120)))
    while time.monotonic() < deadline:
        ready, _, _ = select.select([sys.stdin], [], [], 0.25)
        if ready:
            line = sys.stdin.readline()
            if not line:
                return
            try:
                choice = int(line.strip())
            except ValueError:
                sys.stdout.write("请输入数字：")
                sys.stdout.flush()
                continue
            if not 0 <= choice <= len(options):
                sys.stdout.write("编号不在选项范围内：")
                sys.stdout.flush()
                continue
            if submit(args.request_id, choice - 1):
                print("已提交。Claude 将继续执行。")
            else:
                print("该问题已在副屏完成。")
            return
        if not pending(args.request_id):
            print("\n该问题已在副屏完成。")
            return
    print("\n问题已超时，请回到 Claude 终端继续。")


if __name__ == "__main__":
    main()
