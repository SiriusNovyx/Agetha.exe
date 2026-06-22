"""Small helpers for Medic_Checker.ps1 / Medic_Checker.bat launcher."""
from __future__ import annotations

import platform
import re
import sys
from pathlib import Path


def cmd_platform() -> None:
    print(platform.machine())


def cmd_env_status() -> None:
    path = Path(".env")
    if not path.is_file():
        print("EMPTY")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    keys = [
        m.group(1).strip()
        for m in re.finditer(r"^GROQ_API_KEY(?:_\d+)?\s*=\s*(.+)$", text, re.M)
        if m.group(1).strip() and m.group(1).strip() not in ("", "YOUR_KEY_HERE")
    ]
    print("SET" if any(len(k) > 20 for k in keys) else "EMPTY")


def cmd_config_status() -> None:
    path = Path("config.txt")
    if not path.is_file():
        print("MISSING")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    local = re.search(r"USE_LOCAL_AI\s*=\s*(\S+)", text)
    model = re.search(r"LOCAL_AI_MODEL\s*=\s*(\S+)", text)
    key = re.search(r"GROQ_API_KEY\s*=\s*(.+)", text)
    key_val = key.group(1).strip() if key else ""
    if local and local.group(1).lower() == "yes" and model and model.group(1).strip():
        print("LOCAL")
    elif local and local.group(1).lower() == "yes":
        print("LOCAL_NO_MODEL")
    elif len(key_val) > 20:
        print("SET")
    else:
        print("EMPTY")


_COMMANDS = {
    "platform": cmd_platform,
    "env": cmd_env_status,
    "config": cmd_config_status,
}


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "platform"
    _COMMANDS.get(name, cmd_platform)()
