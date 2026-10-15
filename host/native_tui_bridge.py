#!/usr/bin/env python3
"""Run the real Claude Code TUI and accept Claude Deck selections over a PTY."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import socket
import struct
import sys
import termios
import time
import tty


QUESTION_INSTRUCTION = (
    "When you need the user to choose, confirm, clarify, or provide interactive input, "
    "you MUST use the AskUserQuestion tool instead of printing numbered questions as plain text."
)


def copy_window_size(source_fd, target_fd):
    try:
        size = fcntl.ioctl(source_fd, termios.TIOCGWINSZ, b"\0" * 8)
        fcntl.ioctl(target_fd, termios.TIOCSWINSZ, size)
    except OSError:
        pass


def selection_key_events(
    indexes, multi_select=False, *, is_last=True, question_count=1
):
    """Translate a Deck answer into paced Claude Code TUI input events."""
    clean = sorted({int(index) for index in indexes if int(index) >= 0})
    if not clean:
        return []
    down = b"\x1b[B"
    if not multi_select:
        events = [down] * clean[0] + [b"\r"]
        # A multi-question form advances to its Submit tab after the final
        # single-select answer. The extra Enter confirms that tab.
        if is_last and question_count > 1:
            events.append(b"\r")
        return events

    # Claude's native question UI uses Enter (not Space) to toggle checkboxes.
    events = []
    cursor = 0
    for index in clean:
        events.extend([down] * (index - cursor))
        events.append(b"\r")
        cursor = index
    # Tab leaves the multi-select question. On the final question it lands on
    # Submit; otherwise it advances to the next question tab.
    events.append(b"\t")
    if is_last:
        events.append(b"\r")
    return events


def selection_keys(indexes, multi_select=False, *, is_last=True, question_count=1):
    """Return the byte stream used by tests and simple integrations."""
    return b"".join(
        selection_key_events(
            indexes,
            multi_select,
            is_last=is_last,
            question_count=question_count,
        )
    )


def run_native_tui(cwd, claude_args):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("deck-code 必须在交互式终端中运行")
    claude = shutil.which("claude")
    if not claude:
        raise SystemExit("找不到 Claude Code CLI")

    socket_path = f"/tmp/claude-deck-{os.getuid()}-{os.getpid()}.sock"
    control = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    control.bind(socket_path)
    os.chmod(socket_path, 0o600)
    control.setblocking(False)

    pid, master_fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        env = os.environ.copy()
        env["CLAUDE_DECK_CONTROL_SOCKET"] = socket_path
        env["CLAUDE_DECK_NATIVE_TUI"] = "1"
        args = [claude, *claude_args]
        os.execvpe(claude, args, env)

    original_tty = termios.tcgetattr(sys.stdin.fileno())
    copy_window_size(sys.stdin.fileno(), master_fd)

    def resized(_signum, _frame):
        copy_window_size(sys.stdin.fileno(), master_fd)

    previous_winch = signal.signal(signal.SIGWINCH, resized)
    status = 1
    try:
        tty.setraw(sys.stdin.fileno())
        while True:
            readable, _, _ = select.select(
                [sys.stdin.fileno(), master_fd, control], [], []
            )
            if sys.stdin.fileno() in readable:
                data = os.read(sys.stdin.fileno(), 4096)
                if not data:
                    break
                os.write(master_fd, data)
            if master_fd in readable:
                try:
                    data = os.read(master_fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                os.write(sys.stdout.fileno(), data)
            if control in readable:
                try:
                    message = json.loads(control.recv(65536).decode("utf-8"))
                    if message.get("type") == "question_answer":
                        events = selection_key_events(
                            message.get("indexes", []),
                            bool(message.get("multiSelect")),
                            is_last=bool(message.get("isLast", True)),
                            question_count=max(1, int(message.get("questionCount", 1))),
                        )
                        if events:
                            # The Hook returns just before the native question is painted.
                            time.sleep(0.35)
                            for event in events:
                                os.write(master_fd, event)
                                time.sleep(0.08)
                except (OSError, ValueError, TypeError):
                    pass
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original_tty)
        signal.signal(signal.SIGWINCH, previous_winch)
        control.close()
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass
        try:
            _, wait_status = os.waitpid(pid, 0)
            status = os.waitstatus_to_exitcode(wait_status)
        except ChildProcessError:
            status = 0
    return status


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--no-bypass-permissions", action="store_true")
    parser.add_argument("--help", action="store_true")
    known, forwarded = parser.parse_known_args()
    if known.help:
        print("usage: deck-code [--cwd DIR] [--no-bypass-permissions] [claude options]")
        print("Runs the original Claude Code TUI with Claude Deck question mirroring.")
        return 0

    args = list(forwarded)
    if not known.no_bypass_permissions and "--dangerously-skip-permissions" not in args:
        args.insert(0, "--dangerously-skip-permissions")
    args[0:0] = ["--append-system-prompt", QUESTION_INSTRUCTION]
    return run_native_tui(str(Path(known.cwd).resolve()), args)


if __name__ == "__main__":
    raise SystemExit(main())
