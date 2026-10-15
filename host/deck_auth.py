"""Shared local IPC authentication for the hook and the deck daemon."""

import os
import secrets
import stat
from pathlib import Path


TOKEN_PATH = Path.home() / ".config" / "claude-deck" / "ipc-token"
DEVICE_ID_PATH = TOKEN_PATH.with_name("device-id")


def load_token(create=False):
    directory = TOKEN_PATH.parent
    if create:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        try:
            fd = os.open(TOKEN_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w") as stream:
                stream.write(secrets.token_hex(32))

    info = TOKEN_PATH.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise PermissionError(f"Unsafe deck IPC token permissions: {TOKEN_PATH}")
    token = TOKEN_PATH.read_text().strip()
    if len(token) != 64:
        raise ValueError("Invalid deck IPC token")
    return token


def load_device_id():
    info = DEVICE_ID_PATH.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise PermissionError(f"Unsafe deck device ID permissions: {DEVICE_ID_PATH}")
    device_id = DEVICE_ID_PATH.read_text().strip()
    if not device_id:
        raise ValueError("Deck device ID is empty")
    return device_id
