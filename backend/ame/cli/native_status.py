"""Print native Windows/local development environment status."""

from __future__ import annotations

import json
import shutil
import socket
import sys
from pathlib import Path

from ame.config import get_settings
from ame.db.runtime import database_backend, postgres_reachable, sqlite_paths
from ame.media.ffmpeg import find_ffmpeg


def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def collect() -> dict:
    settings = get_settings()
    ffmpeg = find_ffmpeg()
    postgres = postgres_reachable(settings.database_url_sync)
    _, sqlite_sync = sqlite_paths()
    return {
        "mode": "native",
        "dry_run": settings.dry_run,
        "python": sys.version.split()[0],
        "ffmpeg": ffmpeg,
        "ffmpeg_ok": bool(ffmpeg),
        "postgres_reachable": postgres,
        "redis_port_open": _port_open("127.0.0.1", 6379),
        "resolved_backend": database_backend(),
        "sqlite_path": sqlite_sync,
        "storage_root": str(Path(settings.storage_local_root).resolve()),
        "api": "http://127.0.0.1:8000",
        "dashboard": "http://localhost:3000",
        "docker_required": False,
        "which_docker": shutil.which("docker"),
    }


def main() -> None:
    print(json.dumps(collect(), indent=2))


if __name__ == "__main__":
    main()
