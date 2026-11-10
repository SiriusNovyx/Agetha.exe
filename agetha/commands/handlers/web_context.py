"""Read-only web context handlers."""

from agetha.app_config import get_settings
from agetha.utils import logger

from .registry import register
from .support import start_app_worker as _start_app_worker
from .support import capture_context_validity as _capture_context_validity


def _requery_with_web_context(app, ctx, web_context: str, *, context_is_current=None) -> None:
    if context_is_current is None:
        context_is_current = _capture_context_validity(app)
    if not context_is_current():
        return
    follow = app._ai_query(
        ctx.user_message or "", request_profile="fast_tool_result",
        web_rag_context=web_context, suppress_web_rag=True,
    )
    if follow:
        app._dispatch_response(follow, ctx.user_message, origin="tool_result",
                               speech_is_current=context_is_current)


@register("search_web")
def handle_search_web(app, response, ctx):
    context_is_current = _capture_context_validity(app)
    if ctx.segments:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)

    if not get_settings().enable_web_rag:
        web_context = "[web search is disabled in config (ENABLE_WEB_RAG=no)]"

        def _requery_disabled():
            _requery_with_web_context(app, ctx, web_context, context_is_current=context_is_current)

        _start_app_worker(app, _requery_disabled, "web-search-requery", continuation_is_current=context_is_current)
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
        _requery_with_web_context(app, ctx, web_context, context_is_current=context_is_current)

    _start_app_worker(app, _requery, "web-search-requery", continuation_is_current=context_is_current)
    return True


@register("fetch_webpage")
def handle_fetch_webpage(app, response, ctx):
    context_is_current = _capture_context_validity(app)
    if ctx.segments:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)

    if not get_settings().enable_web_rag:
        web_context = "[web fetch is disabled in config (ENABLE_WEB_RAG=no)]"

        def _requery_disabled():
            _requery_with_web_context(app, ctx, web_context, context_is_current=context_is_current)

        _start_app_worker(app, _requery_disabled, "web-fetch-requery", continuation_is_current=context_is_current)
        return True

    url = (response.get("url") or "").strip()
    if not url:
        web_context = "[web fetch error: no url provided]"

        def _requery_empty():
            _requery_with_web_context(app, ctx, web_context, context_is_current=context_is_current)

        _start_app_worker(app, _requery_empty, "web-fetch-requery", continuation_is_current=context_is_current)
        return True

    try:
        from agetha.features.web_rag import fetch_webpage, format_fetched_page_for_prompt
        page = fetch_webpage(url)
        web_context = format_fetched_page_for_prompt(page)
    except Exception as exc:
        logger.warning(f"fetch_webpage failed: {exc}")
        web_context = f"[web fetch error: {exc}]"

    def _requery():
        _requery_with_web_context(app, ctx, web_context, context_is_current=context_is_current)

    _start_app_worker(app, _requery, "web-fetch-requery", continuation_is_current=context_is_current)
    return True
