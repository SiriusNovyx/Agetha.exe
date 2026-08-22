# Generated stable settings reference

> Generated from `agetha.config.schema.SETTING_SPECS`. Do not edit
> this file by hand; run `python -m agetha.config.generate_settings_reference`.

This is the conservative canonical subset only. Settings with special
transactional, secret, or security semantics remain explicitly implemented.

| Setting | Default | Kind | Constraint | Group | Restart required |
|---|---|---|---|---|---|
| `AI_MAX_TOKENS` | `400` | int | 64 .. 8192 | ai | yes |
| `AI_TEMPERATURE` | `0.85` | float | 0.0 .. 2.0 | ai | yes |
| `AI_TOP_P` | `0.95` | float | 0.0 .. 1.0 | ai | yes |
| `ENABLE_GEMINI` | `no` | bool | - | provider | yes |
| `ENABLE_PRINTWINDOW_FALLBACK` | `yes` | bool | - | screen | yes |
| `EPISODIC_PROMPT_LIMIT` | `10` | int | 0 .. 50 | memory | yes |
| `GEMINI_MODEL` | `gemini-2.5-flash` | string | - | provider | yes |
| `HISTORY_LIMIT` | `6` | int | 1 .. 20 | memory | yes |
| `MEMORY_CHARS` | `600` | int | 100 .. 5000 | memory | yes |
| `OCR_FORCE_REFRESH_SECONDS` | `20` | float | 1.0 .. 3600.0 | screen | yes |
| `OCR_MAX_DIMENSION` | `2560` | int | 640 .. 8192 | screen | yes |
| `OCR_PREPROCESSING` | `auto` | enum | `auto`, `basic` | screen | yes |
| `SCREEN_POLL_INTERVAL_SEC` | `120` | int | 15 .. 3600 | screen | no |
| `UNICODE_TYPING_MODE` | `auto` | enum | `auto`, `paced`, `paste`, `preview`, `unicode` | typing | no |
