#!/usr/bin/env python3
"""Report whether the current platform is in Arcanum's supported matrix."""

from __future__ import annotations

import json
import platform
import sys

SUPPORTED = {
    ("Linux", "x86_64"),
    ("Linux", "aarch64"),
    ("Darwin", "x86_64"),
    ("Darwin", "arm64"),
    ("Windows", "AMD64"),
    ("Windows", "arm64"),
}
MAESTRO_UNSUPPORTED = {("Windows", "arm64")}


def main() -> int:
    system = platform.system()
    machine = platform.machine()
    supported = (system, machine) in SUPPORTED
    maestro = (system, machine) in SUPPORTED and (system, machine) not in MAESTRO_UNSUPPORTED
    print(json.dumps({
        "system": system,
        "machine": machine,
        "python": platform.python_version(),
        "arcanum_supported": supported,
        "maestro_artifact_available": maestro,
        "note": (
            "RunMaestro/Maestro v1.1.1-RC publishes no Windows ARM64 artifact; "
            "manual installation must be documented for that platform."
            if (system, machine) in MAESTRO_UNSUPPORTED else ""
        ),
    }, indent=2))
    return 0 if supported else 1


if __name__ == "__main__":
    raise SystemExit(main())
