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
    or_key = re.search(r"^OPENROUTER_API_KEY\s*=\s*(.+)$", text, re.M)
    if or_key and len(or_key.group(1).strip()) > 10:
        print("OPENROUTER")
        return
    print("SET" if any(len(k) > 20 for k in keys) else "EMPTY")


def cmd_config_status() -> None:
    path = Path("config.txt")
    if not path.is_file():
        print("MISSING")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    local = re.search(r"USE_LOCAL_AI\s*=\s*(\S+)", text)
    model = re.search(r"LOCAL_AI_MODEL\s*=\s*(\S+)", text)
    openrouter = re.search(r"ENABLE_OPENROUTER\s*=\s*(\S+)", text)
    or_key = re.search(r"OPENROUTER_API_KEY\s*=\s*(.+)", text)
    or_val = or_key.group(1).strip() if or_key else ""
    key = re.search(r"GROQ_API_KEY\s*=\s*(.+)", text)
    key_val = key.group(1).strip() if key else ""
    if local and local.group(1).lower() == "yes" and model and model.group(1).strip():
        print("LOCAL")
    elif local and local.group(1).lower() == "yes":
        print("LOCAL_NO_MODEL")
    elif openrouter and openrouter.group(1).lower() == "yes" and len(or_val) > 10:
        print("OPENROUTER")
    elif len(key_val) > 20:
        print("SET")
    else:
        print("EMPTY")


def cmd_voice_deps() -> None:
    """Print VOICE_OK, VOICE_MISSING, STT_OK, or STT_MISSING for Medic_Checker."""
    try:
        from voice_input import check_voice_dependencies, check_local_stt_dependencies
    except ImportError:
        print("VOICE_MISSING:voice_input.py")
        return
    ok, msg = check_voice_dependencies()
    if not ok:
        print(f"VOICE_MISSING:{msg}")
        return
    print("VOICE_OK")
    path = Path("config.txt")
    use_local = False
    if path.is_file():
        m = re.search(r"USE_LOCAL_STT\s*=\s*(\S+)", path.read_text(encoding="utf-8", errors="replace"))
        use_local = m and m.group(1).lower() in ("yes", "true", "1", "on")
    if use_local:
        stt_ok, stt_msg = check_local_stt_dependencies()
        print("STT_OK" if stt_ok else f"STT_MISSING:{stt_msg}")


def cmd_dnd_deps() -> None:
    try:
        import tkinterdnd2  # noqa: F401
        print("DND_OK")
    except ImportError:
        print("DND_MISSING")


_COMMANDS = {
    "platform": cmd_platform,
    "env": cmd_env_status,
    "config": cmd_config_status,
    "voice": cmd_voice_deps,
    "dnd": cmd_dnd_deps,
}


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "platform"
    _COMMANDS.get(name, cmd_platform)()
