"""Small helpers for Medic_Checker.ps1 / Medic_Checker.bat launcher."""
from __future__ import annotations

import platform
import re
import sys
from pathlib import Path


def cmd_platform() -> None:
    print(platform.machine())


_MEDIC_DIR = Path(__file__).resolve().parent


def cmd_env_status() -> None:
    path = _MEDIC_DIR / ".env"
    if not path.is_file():
        print("EMPTY")
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"EMPTY:{exc}")
        return
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


def _env_api_key_status() -> tuple[str, str]:
    """Return (or_val, groq_ready) from .env only. groq_ready is 'yes' or ''."""
    env_path = _MEDIC_DIR / ".env"
    if not env_path.is_file():
        return "", ""
    try:
        env_text = env_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    or_key = re.search(r"^OPENROUTER_API_KEY\s*=\s*(.+)$", env_text, re.M)
    or_val = or_key.group(1).strip() if or_key else ""
    groq_keys = [
        m.group(1).strip()
        for m in re.finditer(r"^GROQ_API_KEY(?:_\d+)?\s*=\s*(.+)$", env_text, re.M)
        if m.group(1).strip() and m.group(1).strip() not in ("", "YOUR_KEY_HERE")
    ]
    groq_ready = "yes" if any(len(k) > 20 for k in groq_keys) else ""
    return or_val, groq_ready


def cmd_config_status() -> None:
    """Report AI backend status. API keys are read from .env only (not config.txt)."""
    path = _MEDIC_DIR / "config.txt"
    if not path.is_file():
        print("MISSING")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    local = re.search(r"USE_LOCAL_AI\s*=\s*(\S+)", text)
    model = re.search(r"LOCAL_AI_MODEL\s*=\s*(\S+)", text)
    openrouter = re.search(r"ENABLE_OPENROUTER\s*=\s*(\S+)", text)
    or_val, groq_ready = _env_api_key_status()
    if local and local.group(1).lower() == "yes" and model and model.group(1).strip():
        print("LOCAL")
    elif local and local.group(1).lower() == "yes":
        print("LOCAL_NO_MODEL")
    elif openrouter and openrouter.group(1).lower() == "yes" and len(or_val) > 10:
        print("OPENROUTER")
    elif groq_ready:
        print("SET")
    else:
        print("EMPTY")


def cmd_config_secrets() -> None:
    """Print KEYS_IN_CONFIG if non-empty API keys remain in config.txt (ignored)."""
    path = _MEDIC_DIR / "config.txt"
    if not path.is_file():
        print("OK")
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        print("OK")
        return
    found = [
        m.group(1).upper()
        for m in re.finditer(
            r"^(GROQ_API_KEY(?:_\d+)?|OPENROUTER_API_KEY)\s*=\s*(\S+)",
            text,
            re.M | re.I,
        )
        if m.group(2).strip()
    ]
    if found:
        print("KEYS_IN_CONFIG:" + ",".join(found[:12]))
    else:
        print("OK")


def cmd_voice_deps() -> None:
    """Print VOICE_OK, VOICE_MISSING, STT_OK, or STT_MISSING for Medic_Checker."""
    try:
        from agetha.platform.voice_input import check_voice_dependencies, check_local_stt_dependencies
    except ImportError:
        print("VOICE_MISSING:voice_input.py")
        return
    ok, msg = check_voice_dependencies()
    if not ok:
        print(f"VOICE_MISSING:{msg}")
        return
    print("VOICE_OK")
    path = _MEDIC_DIR / "config.txt"
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


def _read_config_text() -> str:
    path = _MEDIC_DIR / "config.txt"
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _config_flag(key: str, default: str = "no") -> str:
    text = _read_config_text()
    m = re.search(rf"^{re.escape(key)}\s*=\s*(\S+)", text, re.M | re.I)
    return m.group(1).strip().lower() if m else default.lower()


def cmd_tts_deps() -> None:
    """Print TTS_OK, TTS_SKIP (bleeps_only), or TTS_MISSING for Medic_Checker."""
    mode = _config_flag("VOICE_OUTPUT_MODE", "bleeps_only")
    if mode not in ("tts_only", "both"):
        print("TTS_SKIP")
        return
    try:
        import pyttsx3  # noqa: F401
        print("TTS_OK")
    except ImportError:
        print("TTS_MISSING")


def cmd_feature_modules() -> None:
    """Verify Phase 1–4 extension modules import (no Tk mainloop)."""
    failures: list[str] = []
    for mod in (
        "agetha.core.memory_search",
        "agetha.core.companion_stats",
        "agetha.ui.dashboard",
        "agetha.features.tts_player",
        "agetha.features.web_rag",
        "agetha.ui.glitch_overlay",
        "agetha.ui.virus_trivia",
        "agetha.ui.w95_window",
    ):
        try:
            __import__(mod)
        except Exception as exc:
            failures.append(f"{mod}:{exc}")
    if failures:
        print("FEATURE_FAIL:" + ";".join(failures))
    else:
        print("FEATURE_OK")


def cmd_realism_apis() -> None:
    """Verify realism/presence APIs exist (session recap, host mood, coding assist)."""
    failures: list[str] = []
    try:
        from agetha.core import companion_stats as cs
        if not callable(getattr(cs, "suggest_mood_from_host", None)):
            failures.append("companion_stats.suggest_mood_from_host")
        if not callable(getattr(cs, "suggest_mood_from_heat", None)):
            failures.append("companion_stats.suggest_mood_from_heat")
        if not callable(getattr(cs, "format_stats_for_prompt", None)):
            failures.append("companion_stats.format_stats_for_prompt")
    except Exception as exc:
        failures.append(f"companion_stats:{exc}")

    try:
        from agetha.core import memory_search as ms
        if not callable(getattr(ms, "format_session_recap_for_prompt", None)):
            failures.append("memory_search.format_session_recap_for_prompt")
        if not callable(getattr(ms, "search_memories", None)):
            failures.append("memory_search.search_memories")
    except Exception as exc:
        failures.append(f"memory_search:{exc}")

    try:
        from agetha.core import ai_engine as ae
        if not callable(getattr(ae, "_screen_has_error_pattern", None)):
            failures.append("ai_engine._screen_has_error_pattern")
    except Exception as exc:
        failures.append(f"ai_engine:{exc}")

    if failures:
        print("REALISM_FAIL:" + ";".join(failures))
    else:
        print("REALISM_OK")


def cmd_openrouter_module() -> None:
    """Print OPENROUTER_READY, OPENROUTER_OK_NOT_READY:…, or OPENROUTER_MISSING:…"""
    try:
        from agetha.core import ai_engine as ae
    except Exception as exc:
        print(f"OPENROUTER_MISSING:import ai_engine:{exc}")
        return
    client = getattr(ae, "_OpenRouterClient", None)
    if client is None:
        print("OPENROUTER_MISSING:_OpenRouterClient not found in agetha.core.ai_engine")
        return
    if not isinstance(client, type):
        print("OPENROUTER_MISSING:_OpenRouterClient is not a class")
        return
    create = getattr(client, "chat_completions_create", None)
    if not callable(create):
        print("OPENROUTER_MISSING:_OpenRouterClient.chat_completions_create missing")
        return
    try:
        import urllib.request  # noqa: F401
        import json as _json
    except ImportError as exc:
        print(f"OPENROUTER_MISSING:urllib.request:{exc}")
        return

    # Module exists — check ready-to-use (key + model listed on OpenRouter)
    try:
        from agetha.app_config import get_settings
        settings = get_settings(reload=True)
    except Exception as exc:
        print(f"OPENROUTER_OK_NOT_READY:config:{exc}")
        return

    if not settings.enable_openrouter:
        print("OPENROUTER_OK_NOT_READY:ENABLE_OPENROUTER=no")
        return
    if not (settings.openrouter_api_key or "").strip():
        print("OPENROUTER_OK_NOT_READY:OPENROUTER_API_KEY empty")
        return

    model = (settings.openrouter_model or "").strip()
    if not model:
        print("OPENROUTER_OK_NOT_READY:OPENROUTER_MODEL empty")
        return

    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"User-Agent": "Agetha-Medic"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = _json.loads(resp.read().decode("utf-8", errors="replace"))
        ids = {m.get("id", "") for m in payload.get("data", []) if isinstance(m, dict)}
    except Exception as exc:
        print(f"OPENROUTER_OK_NOT_READY:models_list_unreachable:{exc}")
        return

    if model not in ids:
        hint = ""
        if model.endswith(":free"):
            bare = model[: -len(":free")]
            if bare in ids:
                hint = f";try:{bare}"
        print(f"OPENROUTER_OK_NOT_READY:model_not_found:{model}{hint}")
        return

    paid = not model.lower().endswith(":free")
    groq_on = settings.bool("ENABLE_GROQ", True)
    groq_key = bool((settings.get("GROQ_API_KEY", "") or "").strip())
    if paid and (not groq_on or not groq_key):
        print("OPENROUTER_READY_RECOMMEND_GROQ")
        return

    print("OPENROUTER_READY")


def cmd_toast_shortcut() -> None:
    """Ensure Start Menu Agetha.lnk + AUMID for branded Windows toasts."""
    if sys.platform != "win32":
        print("TOAST_SKIP")
        return
    try:
        from agetha.platform.windows_notify import ensure_start_menu_shortcut
        print("TOAST_OK" if ensure_start_menu_shortcut() else "TOAST_FAIL")
    except Exception as exc:
        print(f"TOAST_FAIL:{exc}")


_COMMANDS = {
    "platform": cmd_platform,
    "env": cmd_env_status,
    "config": cmd_config_status,
    "config_secrets": cmd_config_secrets,
    "voice": cmd_voice_deps,
    "dnd": cmd_dnd_deps,
    "tts": cmd_tts_deps,
    "features": cmd_feature_modules,
    "realism": cmd_realism_apis,
    "openrouter": cmd_openrouter_module,
    "toast_shortcut": cmd_toast_shortcut,
}


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "platform"
    _COMMANDS.get(name, cmd_platform)()
