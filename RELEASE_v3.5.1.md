## Agetha Mod v3.5.1 — Hotfixes

Patch release for issues found right after **v3.5.0**. No new features — stability and UX fixes only.

---

### Fixes

**Chat input blocked by token placeholder**
- The Groq token hint (`key 1/1 • …% tokens left`) was drawn on top of the text field and stayed visible while typing.
- Placeholder now hides when the input is focused or has text; token usage remains in the status bar.
- Hint text when idle: `type here... • key 1/1 • …% tokens left`.

**Medic_Checker step 3 crash (optional packages)**
- With only drag-and-drop enabled (voice off), PowerShell treated a single package name as a scalar — `.Count` failed under `Set-StrictMode`.
- Fixed by forcing `@()` array wrapping after `Select-Object -Unique`.

---

### Upgrade from v3.5.0

1. Pull or download this release
2. Set `APP_VERSION = 3.5.1` in `config.txt` (optional — window title / update check)
3. Run **`Medic_Checker.bat`** if you use drag-and-drop or voice
4. Restart Agetha

No migration needed for `memory/` or `.env`.

---

### Files changed

| File | Change |
|------|--------|
| `main.py` | Placeholder hide on focus; click-to-focus handler |
| `Medic_Checker.ps1` | Optional-package array fix |
| `app_config.py` | Default `APP_VERSION` → 3.5.1 |

**Full diff**: https://github.com/SiriusNovyx/Agetha.exe/compare/v3.5.0...v3.5.1
