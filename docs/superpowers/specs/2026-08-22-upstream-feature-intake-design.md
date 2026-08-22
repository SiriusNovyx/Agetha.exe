# Upstream feature intake design

## Baseline and scope

This intake is based on fork `main` at
`3678fdebd38c17b1f8c0547d12a7f4b03db6eac4` and audits upstream range
`f327e0c5afbca2d279a5e920dd0c7c8fbfd55795..b9436a8b7960b6b719bd2bf820a35d1a9da47471`.
It adapts useful behavior through the post-PR-#34 architecture; it does not merge
or cherry-pick upstream's monolithic modules.

## Audit decisions

| Upstream behavior | Decision | Fork owner | Reason |
|---|---|---|---|
| Gemini REST and SSE provider | ADAPT | `agetha/providers/gemini.py`, explicit AIEngine route wiring | Useful missing provider; upstream transport is embedded in AIEngine and puts the key in the URL/config. |
| Append newly added config keys | ADAPT | `agetha.config.schema` plus `app_config` startup transaction | Useful discoverability; upstream append logic does not preserve the fork's durability and secret boundaries. |
| Provider status without inferred quota percentages | ADAPT | `AIEngine.get_token_status()` and existing main UI labels | Current Groq percentage is a local estimate, not authoritative provider quota. |
| Win32 `PrintWindow` focused capture | ADAPT | Existing screen capture boundary only | Disposable Notepad and Windows Terminal trials reproduced uniform/blank MSS frames while `PrintWindow` returned visible detail; the fallback remains bounded behind the existing target policy. |
| Microphone picker/persistence and local faster-whisper | SUPERSEDED | Existing voice/platform/UI modules | Fork implementation is already modular and more complete. |
| Focused OCR, preprocessing, multi-monitor/DPI | SUPERSEDED | Existing `screen_reader` and platform code | Fork has stronger capture lifecycle and privacy validation. |
| CRT close, glow/motion, full date/time | SUPERSEDED | Existing UI controllers and `time_context` | Already present behind clearer owners. |
| GPT-OSS, reasoning effort, OpenRouter | SUPERSEDED | Existing provider adapters | Post-#34 provider layer already owns these mechanics. |
| Ambient screen awareness/personality edits | SUPERSEDED or SKIP | Existing origin profiles, continuation, presence etiquette | Fork has stronger ambient relevance and authority isolation; upstream prompt text that trusts memory/context is rejected. |
| API keys in `config.txt`, username blacklist, autonomous prompt authority | SKIP | None | Conflicts with secret ownership and authority invariants. |

## Gemini boundary

`GeminiProvider` implements the existing provider adapter contract. Its client:

- sends the API key only in the `x-goog-api-key` header;
- converts system messages to `systemInstruction` and assistant messages to the
  Gemini `model` role;
- uses `generateContent` or `streamGenerateContent?alt=sse`;
- requests JSON output through Gemini generation configuration;
- converts candidates, streaming deltas, usage, and HTTP errors into the same
  OpenAI-shaped response objects consumed by AIEngine.

AIEngine remains responsible for selection, Groq key rotation, bounded retries,
fallback, parsing/repair, history, memory, origins, and final publication.
Gemini never receives command, capability, or origin authority.

Routing remains explicit and conservative:

1. Local Ollama retains exclusive priority when enabled.
2. Groq remains the primary cloud route when usable.
3. Gemini is the next automatic cloud fallback when enabled and keyed.
4. OpenRouter remains the final fallback, or the explicit startup choice when
   the existing Groq/OpenRouter choice dialog selects it.
5. Without Groq, Gemini is primary and OpenRouter is its fallback.

Each route is attempted through the same provider-neutral command envelope.
Fallback changes transport only, never authority or repair eligibility.

## Config discoverability

`ENABLE_GEMINI` and `GEMINI_MODEL` are canonical non-secret `SettingSpec`
entries. `GEMINI_API_KEY` is an allowed `.env` secret and is explicitly rejected
from `config.txt`.

At normal application startup, after ensuring the file exists and before
loading cached settings, one transaction compares parsed keys with
`SETTING_SPECS`. Missing non-secret canonical keys are appended in registry
order by the existing structural renderer and atomic writer. Existing values,
comments, order, unknown keys, and duplicate occurrences are unchanged. The
operation is idempotent and never runs as part of Fast Mode snapshot recovery.

## Provider status

The UI displays truthful identity only: provider/model and Groq key index/count.
It no longer calls locally estimated usage a provider quota. Internal token
accounting may remain for bounded operational decisions, but is not presented
as authoritative remaining capacity.

## Security invariants

- Provider choice cannot change request origin, CommandSpec, CommandGuard,
  CapabilityController, repair eligibility, history, memory, or continuation.
- Gemini configuration contains no secret value in generated docs or
  `config.txt`.
- Config insertion uses the existing path validation, lock, same-directory
  atomic replace, flush/fsync, and duplicate semantics.
- PrintWindow remains a one-attempt Windows-only fallback after a uniform MSS
  focused-window frame. It performs no target selection and remains behind the
  existing sensitivity, minimized-window, exclusion, generation, and stale
  result checks.
- Tests use deterministic fake transports and no paid/live API calls.

## PrintWindow benchmark decision

The benchmark used disposable known-content Notepad and Windows Terminal
windows and retained only coarse image metrics. In the reproduced blank-MSS
cases, `PrintWindow(PW_RENDERFULLCONTENT)` returned non-uniform visible detail;
in later runs MSS sometimes recovered normally. This environment-dependent
benefit justified an optional fallback, not a replacement backend. The
implementation never runs for full-desktop capture, an unavailable target, a
minimized target, or a target rejected by the existing exclusion policy.
