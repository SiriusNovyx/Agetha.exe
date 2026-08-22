"""Small helpers for Medic_Checker.ps1 / Medic_Checker.bat launcher."""
from __future__ import annotations

import ctypes
import json
import platform
import re
import struct
import sys
import sysconfig
from pathlib import Path


def cmd_platform() -> None:
    print(platform.machine())


_WINDOWS_MACHINE_NAMES = {
    0x014C: "X86",
    0x01C4: "ARM",
    0x8664: "AMD64",
    0xAA64: "ARM64",
}


def normalize_python_architecture(value: str, pointer_bits: int | None = None) -> str:
    """Return a stable interpreter architecture name from common aliases."""
    compact = re.sub(r"[^a-z0-9]", "", (value or "").lower())
    if compact in {"amd64", "x8664", "x64"}:
        return "AMD64"
    if compact in {"arm64", "aarch64"}:
        return "ARM64"
    if compact in {"x86", "i386", "i486", "i586", "i686"}:
        return "X86"
    if compact in {"arm", "arm32", "armv7", "armv7l"}:
        return "ARM"
    if pointer_bits == 32:
        return "X86"
    return (value or "UNKNOWN").strip().upper() or "UNKNOWN"


def architecture_from_build_platform(platform_tag: str) -> str:
    """Map Python's wheel/build platform tag to its binary architecture."""
    compact = re.sub(r"[^a-z0-9]", "", (platform_tag or "").lower())
    if "amd64" in compact or "x8664" in compact:
        return "AMD64"
    if "arm64" in compact or "aarch64" in compact:
        return "ARM64"
    if compact in {"win32", "windowsi386"} or compact.endswith("i686"):
        return "X86"
    return ""


def _windows_process_architectures() -> tuple[str, str]:
    """Return (emulated process arch, native OS arch) via IsWow64Process2."""
    if sys.platform != "win32":
        return "", ""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        is_wow64_process2 = kernel32.IsWow64Process2
        is_wow64_process2.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ushort),
            ctypes.POINTER(ctypes.c_ushort),
        ]
        is_wow64_process2.restype = ctypes.c_bool
        process_machine = ctypes.c_ushort(0)
        native_machine = ctypes.c_ushort(0)
        process = kernel32.GetCurrentProcess()
        if not is_wow64_process2(
            process, ctypes.byref(process_machine), ctypes.byref(native_machine)
        ):
            return "", ""
        return (
            _WINDOWS_MACHINE_NAMES.get(process_machine.value, ""),
            _WINDOWS_MACHINE_NAMES.get(native_machine.value, ""),
        )
    except (AttributeError, OSError):
        # IsWow64Process2 is unavailable on older Windows releases.
        return "", ""


def get_python_architecture_info() -> dict[str, str | int]:
    """Describe this Python build without confusing it with the host CPU."""
    reported = platform.machine()
    pointer_bits = struct.calcsize("P") * 8
    build_platform = sysconfig.get_platform()
    build_arch = architecture_from_build_platform(build_platform)
    process_arch, native_arch = _windows_process_architectures()
    # sysconfig's platform is the interpreter's wheel/ABI target (for example,
    # win-amd64). On recent Windows ARM/Prism builds, both platform.machine()
    # and IsWow64Process2 may expose ARM64 even for an AMD64 Python binary.
    python_arch = normalize_python_architecture(
        build_arch or process_arch or reported, pointer_bits
    )
    native = normalize_python_architecture(native_arch or reported, pointer_bits)
    return {
        "python_arch": python_arch,
        "native_arch": native,
        "build_platform": build_platform or "UNKNOWN",
        "reported_machine": reported or "UNKNOWN",
        "pointer_bits": pointer_bits,
    }


def cmd_python_arch() -> None:
    print(json.dumps(get_python_architecture_info(), separators=(",", ":")))


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
    gemini_key = re.search(r"^GEMINI_API_KEY\s*=\s*(.+)$", text, re.M)
    if gemini_key and len(gemini_key.group(1).strip()) > 10:
        print("GEMINI")
        return
    print("SET" if any(len(k) > 20 for k in keys) else "EMPTY")


def _env_api_key_status() -> tuple[str, str, str]:
    """Return OpenRouter, Gemini, and Groq readiness from ``.env`` only."""
    env_path = _MEDIC_DIR / ".env"
    if not env_path.is_file():
        return "", "", ""
    try:
        env_text = env_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", "", ""
    or_key = re.search(r"^OPENROUTER_API_KEY\s*=\s*(.+)$", env_text, re.M)
    or_val = or_key.group(1).strip() if or_key else ""
    gemini_key = re.search(r"^GEMINI_API_KEY\s*=\s*(.+)$", env_text, re.M)
    gemini_val = gemini_key.group(1).strip() if gemini_key else ""
    groq_keys = [
        m.group(1).strip()
        for m in re.finditer(r"^GROQ_API_KEY(?:_\d+)?\s*=\s*(.+)$", env_text, re.M)
        if m.group(1).strip() and m.group(1).strip() not in ("", "YOUR_KEY_HERE")
    ]
    groq_ready = "yes" if any(len(k) > 20 for k in groq_keys) else ""
    return or_val, gemini_val, groq_ready


def cmd_config_status() -> None:
    """Report AI backend status. API keys are read from .env only (not config.txt)."""
    path = _MEDIC_DIR / "config.txt"
    if not path.is_file():
        print("MISSING")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    local = re.search(r"USE_LOCAL_AI\s*=\s*(\S+)", text)
    model = re.search(r"LOCAL_AI_MODEL\s*=\s*(\S+)", text)
    gemini = re.search(r"ENABLE_GEMINI\s*=\s*(\S+)", text)
    openrouter = re.search(r"ENABLE_OPENROUTER\s*=\s*(\S+)", text)
    or_val, gemini_val, groq_ready = _env_api_key_status()
    if local and local.group(1).lower() == "yes" and model and model.group(1).strip():
        print("LOCAL")
    elif local and local.group(1).lower() == "yes":
        print("LOCAL_NO_MODEL")
    elif openrouter and openrouter.group(1).lower() == "yes" and len(or_val) > 10:
        print("OPENROUTER")
    elif gemini and gemini.group(1).lower() == "yes" and len(gemini_val) > 10:
        print("GEMINI")
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
            r"^(GROQ_API_KEY(?:_\d+)?|GEMINI_API_KEY|OPENROUTER_API_KEY)\s*=\s*(\S+)",
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
    engine = _config_flag("VOICE_TTS_ENGINE", "pyttsx3")
    try:
        if engine == "edge_tts":
            import edge_tts  # noqa: F401
        elif engine == "kokoro":
            from kokoro import KPipeline  # noqa: F401
        else:
            import pyttsx3  # noqa: F401
        print(f"TTS_OK:{engine}")
    except ImportError:
        print(f"TTS_MISSING:{engine}")


def cmd_feature_modules() -> None:
    """Verify Phase 1–6 extension modules import (no Tk mainloop)."""
    failures: list[str] = []
    for mod in (
        "agetha.core.memory_search",
        "agetha.core.companion_stats",
        "agetha.core.rhythm",
        "agetha.core.dreams",
        "agetha.core.emotion_engine",
        "agetha.core.emotional_history",
        "agetha.core.audit_log",
        "agetha.core.fast_mode_profile",
        "agetha.providers.gemini",
        "agetha.platform.autostart",
        "agetha.platform.win_integration",
        "agetha.platform.ocr_backends",
        "agetha.platform.screen_monitoring",
        "agetha.features.tasks",
        "agetha.features.status_providers",
        "agetha.features.tray_scaffold",
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

    try:
        from agetha.platform.screen_reader import ScreenReader
        if not callable(getattr(ScreenReader, "capture_deep_text", None)):
            failures.append("screen_reader.capture_deep_text")
    except Exception as exc:
        failures.append(f"screen_reader:{exc}")

    # v4.0.0 — circadian rhythm, dream journal, task keeper
    try:
        from agetha.core import rhythm as rh
        if not callable(getattr(rh, "format_rhythm_for_prompt", None)):
            failures.append("rhythm.format_rhythm_for_prompt")
    except Exception as exc:
        failures.append(f"rhythm:{exc}")

    try:
        from agetha.core import dreams as dr
        for fn in ("generate_dream", "pop_wake_recall_for_prompt", "get_recent_dreams"):
            if not callable(getattr(dr, fn, None)):
                failures.append(f"dreams.{fn}")
    except Exception as exc:
        failures.append(f"dreams:{exc}")

    try:
        from agetha.features import tasks as tk
        for fn in ("add_task", "complete_task", "get_tasks", "format_tasks_for_prompt"):
            if not callable(getattr(tk, fn, None)):
                failures.append(f"tasks.{fn}")
    except Exception as exc:
        failures.append(f"tasks:{exc}")

    if failures:
        print("REALISM_FAIL:" + ";".join(failures))
    else:
        print("REALISM_OK")


def cmd_deep_ocr_status() -> None:
    """Validate optional deep-OCR configuration without contacting the service."""
    try:
        from agetha.app_config import get_settings
        from agetha.platform.ocr_backends.unlimited_ocr_backend import (
            is_local_server_url,
            normalize_server_url,
        )
        settings = get_settings(reload=True)
    except Exception:
        print("DEEP_OCR_INVALID")
        return

    if settings.deep_ocr_backend != "unlimited_ocr":
        print("DEEP_OCR_DISABLED")
        return
    raw_url = settings.get("UNLIMITED_OCR_SERVER_URL", "")
    try:
        normalize_server_url(raw_url)
    except ValueError:
        print("DEEP_OCR_INVALID_URL")
        return
    if is_local_server_url(raw_url):
        print("DEEP_OCR_CONFIGURED_LOCAL")
    elif settings.unlimited_ocr_allow_remote:
        print("DEEP_OCR_CONFIGURED_REMOTE")
    else:
        print("DEEP_OCR_REMOTE_BLOCKED")


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


def cmd_autostart_status() -> None:
    """Read-only Startup-folder status. Never creates, removes, or rewrites shortcuts.

    Prints one of:
      AUTOSTART_ON
      AUTOSTART_OFF
      AUTOSTART_MALFORMED
      AUTOSTART_FOREIGN
      AUTOSTART_UNAVAILABLE
      AUTOSTART_ERROR:<msg>
    """
    if sys.platform != "win32":
        print("AUTOSTART_UNAVAILABLE")
        return
    try:
        from agetha.platform import autostart
        status = autostart.validate()
        if status == autostart.STATUS_VALID:
            print("AUTOSTART_ON")
        elif status == autostart.STATUS_MISSING:
            print("AUTOSTART_OFF")
        elif status == autostart.STATUS_MALFORMED:
            print("AUTOSTART_MALFORMED")
        elif status == autostart.STATUS_FOREIGN:
            print("AUTOSTART_FOREIGN")
        else:
            print(f"AUTOSTART_ERROR:unknown_status:{status}")
    except Exception as exc:
        print(f"AUTOSTART_ERROR:{exc}")


def _fast_mode_result_value(result: object, name: str, default: object = None) -> object:
    if isinstance(result, dict):
        return result.get(name, default)
    return getattr(result, name, default)


def _fast_mode_result_ok(result: object) -> bool:
    status = str(_fast_mode_result_value(result, "status", "unknown"))
    if status == "restored_snapshot_retained":
        return True
    explicit = _fast_mode_result_value(result, "ok", None)
    if explicit is not None:
        return bool(explicit)
    return status not in {
        "snapshot_invalid", "snapshot_write_failed", "snapshot_cleanup_failed",
        "config_write_failed", "invalid_updates", "profile_busy", "restore_failed",
        "unsafe_path_state", "unsafe_profile_definition", "verification_pending",
        "failed",
    }


def _print_fast_mode_result(result: object, profile: object) -> None:
    """Print one secret-free JSON object for PowerShell to consume."""
    status = str(_fast_mode_result_value(result, "status", "unknown"))
    changed = tuple(str(key) for key in (_fast_mode_result_value(result, "changed_keys", ()) or ()))
    warnings = tuple(_fast_mode_result_value(result, "warnings", ()) or ())
    conflict_count = sum(
        1 for warning in warnings
        if str(warning).startswith("Preserved a user-edited restore value for ")
    )
    if status == "restore_conflict_preserved" and conflict_count == 0:
        # Older snapshots/results did not distinguish conflict warnings. The
        # warning count remains a safer estimate than the unrelated changed-key
        # count (which also includes ordinary restored/unmanaged settings).
        conflict_count = len(warnings)
    payload = {
        "status": status,
        "ok": _fast_mode_result_ok(result),
        "active": bool(profile.is_fast_mode_profile_active()),
        "managed_count": len(tuple(profile.managed_fast_mode_keys())),
        "changed_keys": changed,
        "conflict_count": conflict_count,
        # Medic only needs the count. Warning text can contain local paths and is
        # already logged by the owning module when appropriate.
        "warning_count": len(warnings),
    }
    print(json.dumps(payload, separators=(",", ":")))


def _run_fast_mode_command(action: str) -> None:
    try:
        from agetha.core import fast_mode_profile as profile

        if action == "reconcile":
            result = profile.reconcile_fast_mode_profile()
        elif action == "restore":
            restore = getattr(profile, "restore_fast_mode_profile", None)
            if not callable(restore):
                restore = profile.deactivate_fast_mode
            result = restore()
        else:
            result = profile.inspect_fast_mode_profile()
        _print_fast_mode_result(result, profile)
    except Exception:
        # Keep this machine-readable and avoid echoing exception text that may
        # include private filesystem paths.
        print(json.dumps({
            "status": "unavailable",
            "ok": False,
            "active": False,
            "managed_count": 0,
            "changed_keys": [],
            "conflict_count": 0,
            "warning_count": 1,
        }, separators=(",", ":")))


def cmd_fast_mode_status() -> None:
    """Inspect Fast Mode without changing config or snapshot state."""
    _run_fast_mode_command("status")


def cmd_fast_mode_reconcile() -> None:
    """Run the guarded profile reconciliation requested by Medic."""
    _run_fast_mode_command("reconcile")


def cmd_fast_mode_restore() -> None:
    """Restore the saved profile; Medic asks for confirmation before this call."""
    _run_fast_mode_command("restore")


_COMMANDS = {
    "platform": cmd_platform,
    "python_arch": cmd_python_arch,
    "env": cmd_env_status,
    "config": cmd_config_status,
    "config_secrets": cmd_config_secrets,
    "voice": cmd_voice_deps,
    "dnd": cmd_dnd_deps,
    "tts": cmd_tts_deps,
    "features": cmd_feature_modules,
    "realism": cmd_realism_apis,
    "deep_ocr": cmd_deep_ocr_status,
    "openrouter": cmd_openrouter_module,
    "toast_shortcut": cmd_toast_shortcut,
    "autostart": cmd_autostart_status,
    "fast_mode_status": cmd_fast_mode_status,
    "fast_mode_reconcile": cmd_fast_mode_reconcile,
    "fast_mode_restore": cmd_fast_mode_restore,
}


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "platform"
    _COMMANDS.get(name, cmd_platform)()
