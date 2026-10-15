#!/usr/bin/env python3
"""Relay one native Claude Code question from the Deck back into its PTY."""

import hashlib
import json
import os
import socket
import sys
import urllib.error
import urllib.request

try:
    from .claude_hook import local_headers, normalize_question_payload
except ImportError:
    from claude_hook import local_headers, normalize_question_payload


BASE_URL = "http://127.0.0.1:19842"


def question_id(request_key, index):
    return hashlib.sha256(f"{request_key}:{index}".encode()).hexdigest()[:16]


def post_question(question, request_id, timeout):
    payload = normalize_question_payload(question)
    payload.update(
        {
            "id": request_id,
            "timeout": timeout,
            "launchTerminal": False,
        }
    )
    request = urllib.request.Request(
        BASE_URL + "/question",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=local_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout + 5) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.HTTPError):
        return None


def send_to_pty(socket_path, message):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        client.sendto(json.dumps(message).encode("utf-8"), socket_path)
    finally:
        client.close()


def main():
    payload = json.load(sys.stdin)
    socket_path = str(payload.get("socket") or "")
    request_key = str(payload.get("request_key") or "")
    questions = payload.get("questions") or []
    timeout = int(payload.get("timeout") or 120)
    if not socket_path or not request_key or not questions:
        return 2

    for index, question in enumerate(questions):
        response = post_question(question, question_id(request_key, index), timeout)
        if not response or not response.get("answered"):
            return 0
        indexes = response.get("indexes")
        if indexes is None:
            indexes = [response.get("index")]
        send_to_pty(
            socket_path,
            {
                "type": "question_answer",
                "indexes": [value for value in indexes if value is not None],
                "multiSelect": bool(question.get("multiSelect")),
                "questionIndex": index,
                "questionCount": len(questions),
                "isLast": index == len(questions) - 1,
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
