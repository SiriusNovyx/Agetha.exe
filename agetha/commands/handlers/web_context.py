"""Read-only web context handlers."""

from agetha.app_config import get_settings
from agetha.utils import logger

from .registry import register
from .support import start_app_worker as _start_app_worker


def _set_web_rag_pending(app, context: str, suppress: bool = True) -> None:
    if app._ai is not None:
        app._ai._pending_web_rag_context = context
        app._ai._pending_suppress_web_rag = suppress


def _clear_web_rag_pending(app) -> None:
    if app._ai is not None:
        app._ai._pending_web_rag_context = ""
        app._ai._pending_suppress_web_rag = False


def _requery_with_web_context(app, ctx, web_context: str) -> None:
    _set_web_rag_pending(app, web_context, suppress=True)
    try:
        follow = app._ai_query(
            ctx.user_message or "", request_profile="fast_tool_result",
        )
        if follow:
            app._dispatch_response(follow, ctx.user_message, origin="tool_result")
    finally:
        _clear_web_rag_pending(app)


@register("search_web")
def handle_search_web(app, response, ctx):
    if ctx.segments:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)

    if not get_settings().enable_web_rag:
        web_context = "[web search is disabled in config (ENABLE_WEB_RAG=no)]"

        def _requery_disabled():
            _requery_with_web_context(app, ctx, web_context)

        _start_app_worker(app, _requery_disabled, "web-search-requery")
        return True

    query = (response.get("query") or ctx.user_message or "").strip()
    try:
        limit = int(response.get("limit") or get_settings().web_search_max_results)
    except (TypeError, ValueError):
        limit = get_settings().web_search_max_results

    try:
        from agetha.features.web_rag import search_web, format_search_results_for_prompt
        results = search_web(query, limit=limit)
        web_context = format_search_results_for_prompt(results)
    except Exception as exc:
        logger.warning(f"search_web failed: {exc}")
        web_context = f"[web search error: {exc}]"

    def _requery():
        _requery_with_web_context(app, ctx, web_context)

    _start_app_worker(app, _requery, "web-search-requery")
    return True


@register("fetch_webpage")
def handle_fetch_webpage(app, response, ctx):
    if ctx.segments:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)

    if not get_settings().enable_web_rag:
        web_context = "[web fetch is disabled in config (ENABLE_WEB_RAG=no)]"

        def _requery_disabled():
            _requery_with_web_context(app, ctx, web_context)

        _start_app_worker(app, _requery_disabled, "web-fetch-requery")
        return True

    url = (response.get("url") or "").strip()
    if not url:
        web_context = "[web fetch error: no url provided]"

        def _requery_empty():
            _requery_with_web_context(app, ctx, web_context)

        _start_app_worker(app, _requery_empty, "web-fetch-requery")
        return True

    try:
        from agetha.features.web_rag import fetch_webpage, format_fetched_page_for_prompt
        page = fetch_webpage(url)
        web_context = format_fetched_page_for_prompt(page)
    except Exception as exc:
        logger.warning(f"fetch_webpage failed: {exc}")
        web_context = f"[web fetch error: {exc}]"

    def _requery():
        _requery_with_web_context(app, ctx, web_context)

    _start_app_worker(app, _requery, "web-fetch-requery")
    return True
