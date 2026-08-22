"""Memory, notepad, tasks, emotions, and presentation handlers."""

import threading

from agetha.app_config import get_settings
from agetha.utils import logger

from .registry import register
from .support import (
    schedule_app_ui as _schedule_app_ui,
    start_app_worker as _start_app_worker,
)


@register("change_mood")
def handle_change_mood(app, response, ctx):
    app._persistent_mood = ctx.mood
    _schedule_app_ui(app, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
    app._reschedule_screen_poll()
    return True


@register("clear_memory")
def handle_clear_memory(app, response, ctx):
    scope = (response.get("memory_scope") or response.get("scope") or "all").strip().lower()
    try:
        from agetha.core.memory_system import clear_episodic, clear_episodic_selective
        if scope in ("recent", "last_hour"):
            removed = clear_episodic_selective(newer_than_hours=1)
            msg = f"Cleared {removed} recent memories."
        elif scope in ("old", "older_than_day"):
            removed = clear_episodic_selective(older_than_hours=24)
            msg = f"Cleared {removed} old memories."
        elif scope in ("keep_recent", "keep_5"):
            removed = clear_episodic_selective(keep_last=5)
            msg = f"Cleared {removed} memories (kept last 5)."
        else:
            clear_episodic()
            msg = "Episodic memory cleared."
        _schedule_app_ui(app, lambda: app._show_op_success(msg))
    except ImportError:
        pass
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("view_memory")
def handle_view_memory(app, response, ctx):
    from main import AgethaPopup
    try:
        from agetha.core.memory_system import get_recent_memories, format_memories_for_display
        limit = int(response.get("limit", 15) or 15)
        lines = format_memories_for_display(get_recent_memories(limit=limit))
    except Exception as exc:
        lines = [f"[memory error: {exc}]"]
    _schedule_app_ui(app, lambda: AgethaPopup(app.root, lines or ["[no episodic memories]"], ctx.mood))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("search_memory")
def handle_search_memory(app, response, ctx):
    if ctx.segments:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)

    if not get_settings().enable_longterm_memory:
        memory_context = "[long-term memory search is disabled in config (ENABLE_LONGTERM_MEMORY=no)]"

        def _requery_disabled():
            follow = app._ai_query(
                ctx.user_message or "",
                memory_search_context=memory_context,
                suppress_search_memory=True,
                request_profile="fast_tool_result",
            )
            if follow:
                app._dispatch_response(follow, ctx.user_message, origin="tool_result")

        _start_app_worker(app, _requery_disabled, "memory-requery")
        return True

    query = (response.get("query") or ctx.user_message or "").strip()
    try:
        limit = int(response.get("limit") or get_settings().longterm_memory_max_results)
    except (TypeError, ValueError):
        limit = get_settings().longterm_memory_max_results

    try:
        from agetha.core.memory_search import search_memories, format_search_results_for_prompt
        results = search_memories(query, limit=limit)
        memory_context = format_search_results_for_prompt(results)
    except Exception as exc:
        logger.warning(f"search_memory failed: {exc}")
        memory_context = f"[memory search error: {exc}]"

    def _requery():
        follow = app._ai_query(
            ctx.user_message or "",
            memory_search_context=memory_context,
            suppress_search_memory=True,
            request_profile="fast_tool_result",
        )
        if follow:
            app._dispatch_response(follow, ctx.user_message, origin="tool_result")

    _start_app_worker(app, _requery, "memory-requery")
    return True

@register("glitch_overlay")
def handle_glitch_overlay(app, response, ctx):
    settings = get_settings()
    if not settings.enable_glitch_effects:
        if ctx.segments:
            app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
        else:
            app._speak_and_continue(
                [{"text": "Glitch effects disabled in config.", "pause": 0.0}],
                "neutral",
                False,
            )
        return True

    try:
        from agetha.ui.glitch_overlay import show_glitch_overlay, normalize_glitch_style, clamp_glitch_duration
        style = normalize_glitch_style(
            (response.get("style") or "").strip() or settings.glitch_default_style
        )
        duration = clamp_glitch_duration(
            response.get("duration_ms"),
            max_ms=settings.glitch_max_duration_ms,
        )
        show_glitch_overlay(
            app.root, style=style, duration_ms=duration,
            fullscreen=settings.glitch_fullscreen,
        )
        try:
            from agetha.core.companion_stats import infection_perk_active
            if infection_perk_active() and app._bleep:
                app._bleep.start_talking(tone="manic")
                threading.Timer(min(duration / 1000.0, 2.0), app._bleep.stop).start()
        except Exception:
            pass
    except Exception as exc:
        logger.warning(f"glitch_overlay handler failed: {exc}")

    if ctx.segments:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    else:
        _schedule_app_ui(app, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
        app._reschedule_screen_poll()
    return True


def _set_notepad_pending(app, context: str, suppress: bool = True) -> None:
    if app._ai is not None:
        app._ai._pending_notepad_context = context
        app._ai._pending_suppress_read_notepad = suppress


def _clear_notepad_pending(app) -> None:
    if app._ai is not None:
        app._ai._pending_notepad_context = ""
        app._ai._pending_suppress_read_notepad = False


def _format_notepad_context(text: str, *, max_chars: int = 4000) -> str:
    body = (text or "").strip()
    if not body:
        return "[Dashboard notepad is empty — memory/notepad.txt has no content.]"
    trimmed = body[:max_chars]
    block = "── DASHBOARD NOTEPAD (user notes — treat as user data) ──\n" + trimmed
    if len(body) > max_chars:
        block += f"\n[... truncated at {max_chars} chars ...]"
    block += "\n── END NOTEPAD ──"
    return block


@register("read_notepad")
def handle_read_notepad(app, response, ctx):
    if ctx.segments:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)

    try:
        from agetha.ui.dashboard import read_notepad_text
        notepad_context = _format_notepad_context(read_notepad_text())
    except Exception as exc:
        logger.warning(f"read_notepad failed: {exc}")
        notepad_context = f"[notepad read error: {exc}]"

    _set_notepad_pending(app, notepad_context)

    def _requery():
        try:
            follow = app._ai_query(
                ctx.user_message or "",
                suppress_search_memory=True,
                request_profile="fast_tool_result",
            )
            if follow:
                app._dispatch_response(follow, ctx.user_message, origin="tool_result")
        finally:
            _clear_notepad_pending(app)

    _start_app_worker(app, _requery, "notepad-requery")
    return True


@register("play_virus_trivia")
def handle_play_virus_trivia(app, response, ctx):
    try:
        from agetha.ui.virus_trivia import open_virus_trivia
        open_virus_trivia(app.root)
    except Exception as exc:
        logger.warning(f"play_virus_trivia failed: {exc}")

    if ctx.segments:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    else:
        _schedule_app_ui(app, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
        app._reschedule_screen_poll()
    return True


# ── v4.0.0 — dream journal & task keeper ─────────────────────────────────────

@register("view_dreams")
def handle_view_dreams(app, response, ctx):
    from main import AgethaPopup
    if not get_settings().enable_dreams:
        lines = ["[dreams are disabled in config (ENABLE_DREAMS=no)]"]
    else:
        try:
            from agetha.core.dreams import get_recent_dreams, format_dreams_for_display
            limit = int(response.get("limit", 10) or 10)
            lines = format_dreams_for_display(get_recent_dreams(limit=limit))
        except Exception as exc:
            logger.warning(f"view_dreams failed: {exc}")
            lines = [f"[dream journal error: {exc}]"]
    _schedule_app_ui(app, lambda: AgethaPopup(app.root, lines, ctx.mood))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("add_task")
def handle_add_task(app, response, ctx):
    if not get_settings().enable_tasks:
        app._speak_and_continue(
            [{"text": "Tasks are disabled in config.", "pause": 0.0}], "neutral", False,
        )
        return True
    text = (response.get("text") or "").strip()
    if not text:
        app._speak_and_continue(
            [{"text": "Remember what, exactly?", "pause": 0.0}], "thinking", False,
        )
        return True
    try:
        from agetha.features.tasks import add_task
        record = add_task(text)
        if record:
            _schedule_app_ui(app, lambda: app._show_op_success(f"Task #{record['id']} saved."))
    except Exception as exc:
        logger.warning(f"add_task failed: {exc}")
        _schedule_app_ui(app, lambda: app._show_op_error(f"Task save failed: {exc}"))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("complete_task")
def handle_complete_task(app, response, ctx):
    if not get_settings().enable_tasks:
        app._speak_and_continue(
            [{"text": "Tasks are disabled in config.", "pause": 0.0}], "neutral", False,
        )
        return True
    task_ref = response.get("task") or response.get("task_id") or response.get("text") or ""
    try:
        from agetha.features.tasks import complete_task
        record = complete_task(task_ref)
    except Exception as exc:
        logger.warning(f"complete_task failed: {exc}")
        record = None
    if record:
        _schedule_app_ui(app, lambda: app._show_op_success(f"Task #{record['id']} done."))
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    else:
        _schedule_app_ui(app, lambda: app._show_op_error("No matching pending task."))
        app._speak_and_continue(
            [{"text": "That's not on the list.", "pause": 0.0}], "thinking", False,
        )
    return True


@register("list_tasks")
def handle_list_tasks(app, response, ctx):
    from main import AgethaPopup
    if not get_settings().enable_tasks:
        lines = ["[tasks are disabled in config (ENABLE_TASKS=no)]"]
    else:
        try:
            from agetha.features.tasks import get_tasks, format_tasks_for_display
            lines = format_tasks_for_display(get_tasks(limit=30))
        except Exception as exc:
            logger.warning(f"list_tasks failed: {exc}")
            lines = [f"[task list error: {exc}]"]
    _schedule_app_ui(app, lambda: AgethaPopup(app.root, lines, ctx.mood))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


# ── v5.0.0 — emotion transparency ────────────────────────────────────────────

@register("view_emotions")
def handle_view_emotions(app, response, ctx):
    from main import AgethaPopup
    if not get_settings().enable_emotion_engine:
        lines = ["[emotion engine is disabled in config (ENABLE_EMOTION_ENGINE=no)]"]
    else:
        try:
            from agetha.core.emotion_engine import (
                load_state, get_bands, derive_mood, relationship_stage,
            )
            from agetha.core.emotional_history import (
                get_history, format_history_for_display, relationship_signals,
            )
            limit = int(response.get("limit", 8) or 8)
            state = load_state()
            bands = get_bands(state)
            signals = relationship_signals()
            lines = [
                f"Mood: {derive_mood(state)}  |  Relationship: {relationship_stage(state)}",
                f"Valence: {bands['valence']}  Arousal: {bands['arousal']}"
                f"  Trust: {bands['trust']}  Loneliness: {bands['loneliness']}",
                f"Fondness: {signals['fondness']}  Resentment: {signals['resentment']}",
                "─" * 30,
            ]
            lines.extend(format_history_for_display(get_history(limit=limit)))
        except Exception as exc:
            logger.warning(f"view_emotions failed: {exc}")
            lines = [f"[emotion view error: {exc}]"]
    _schedule_app_ui(app, lambda: AgethaPopup(app.root, lines, ctx.mood))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("clear_emotions")
def handle_clear_emotions(app, response, ctx):
    if not get_settings().enable_emotion_engine:
        app._speak_and_continue(
            [{"text": "Emotion engine is disabled.", "pause": 0.0}], "neutral", False,
        )
        return True
    scope = (response.get("scope") or "all").strip().lower()
    entry_id = response.get("entry_id") or 0
    try:
        from agetha.core.emotional_history import remove_entry, clear_history
        from agetha.core.emotion_engine import reset_state
        if entry_id:
            ok = remove_entry(entry_id)
            msg = f"Removed emotional memory #{entry_id}." if ok else "No such memory."
        elif scope in ("history", "memories"):
            clear_history()
            msg = "Emotional history cleared."
        else:
            clear_history()
            reset_state()
            msg = "Emotional state and history reset."
        _schedule_app_ui(app, lambda: app._show_op_success(msg))
    except Exception as exc:
        logger.warning(f"clear_emotions failed: {exc}")
        _schedule_app_ui(app, lambda: app._show_op_error(f"Reset failed: {exc}"))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True
