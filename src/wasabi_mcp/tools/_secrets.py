"""Write new secrets to files in ``WASABI_MCP_SECRETS_DIR`` instead of returning them.

A private module: ``discover_groups`` skips names starting with ``_``, so it registers no
tools. Tools that create credentials call ``prepare`` before the Wasabi call, so a secret
is never created that can't be saved, then ``write`` once the call has returned.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any

from wasabi_mcp.config import Settings
from wasabi_mcp.safety import ToolRefused

DIR_MODE = 0o700
FILE_MODE = 0o600

SAVED_NOTE = (
    "The secret was written to secrets_file (mode 0600) and isn't returned here. It can't "
    "be retrieved from Wasabi again: keep the file safe, or delete it once it's stored."
)


def prepare(settings: Settings) -> Path | None:
    """Check the secrets directory, creating it with mode 0700 if it doesn't exist.

    Returns the directory, or ``None`` when ``WASABI_MCP_SECRETS_DIR`` is unset. Raises
    ``ToolRefused`` if the path isn't a directory, is group- or world-accessible, or
    isn't writable.
    """
    directory = settings.secrets_dir
    if directory is None:
        return None
    try:
        try:
            directory.mkdir(mode=DIR_MODE, parents=True)
        except FileExistsError:
            pass
        info = os.stat(directory)
    except OSError as exc:
        raise ToolRefused(
            f"WASABI_MCP_SECRETS_DIR {directory} can't be created or read: {exc.strerror}. "
            "Create it with 'mkdir -m 700' or point the setting at another directory."
        ) from exc
    if not stat.S_ISDIR(info.st_mode):
        raise ToolRefused(
            f"WASABI_MCP_SECRETS_DIR {directory} isn't a directory. "
            "Point the setting at a directory."
        )
    if info.st_mode & 0o077:
        raise ToolRefused(
            f"WASABI_MCP_SECRETS_DIR {directory} is accessible to other users "
            f"(mode {stat.S_IMODE(info.st_mode):04o}). Run 'chmod 700 {directory}' "
            "and try again."
        )
    if not os.access(directory, os.W_OK | os.X_OK):
        raise ToolRefused(
            f"WASABI_MCP_SECRETS_DIR {directory} isn't writable by this server. "
            f"Run 'chmod 700 {directory}' as its owner, or point the setting at a "
            "directory the server's user owns."
        )
    return directory


def write(directory: Path, tool: str, payload: dict[str, Any], resource: str) -> str:
    """Write ``payload`` as JSON to a new mode-0600 file and return its absolute path.

    The file name is ``<tool>-<random hex>.json``, created with ``O_EXCL`` so an existing
    file (or symlink) is never overwritten. ``resource`` names what the Wasabi call
    created, so a failure here tells the operator what to delete or rotate.
    """
    path = directory / f"{tool}-{secrets.token_hex(16)}.json"
    data = json.dumps(payload, indent=2, default=str).encode()
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, FILE_MODE)
    except OSError as exc:
        raise _lost(resource, directory, exc) from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        path.unlink(missing_ok=True)
        raise _lost(resource, directory, exc) from exc
    return str(path)


def _lost(resource: str, directory: Path, exc: OSError) -> ToolRefused:
    return ToolRefused(
        f"{resource} was created, but its secret couldn't be written to "
        f"WASABI_MCP_SECRETS_DIR {directory}: {exc.strerror or exc}. The secret is lost: "
        "delete or rotate it."
    )
