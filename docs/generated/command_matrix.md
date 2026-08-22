# Generated command matrix

> Generated from `agetha.commands.specs.COMMAND_SPECS`. Do not edit
> this file by hand; run `python -m agetha.commands.generate_command_matrix`.

This reference contains static policy facts only. Dynamic target, process,
generation, confirmation, and effect-time decisions remain in their runtime owners.

| Command | Base risk | Capability | Execution required | Allowed origins | Dispatch | Handler | Command-specific feature gates |
|---|---|---|---|---|---|---|---|
| `add_task` | safe | memory | no | user, touch, file_drop, reminder | handler | `add_task` | `ENABLE_TASKS` |
| `analyze_screen_deep` | caution | advanced_os_integration | yes | user | handler | `analyze_screen_deep` | - |
| `change_mood` | safe | emotion_personality | no | user, touch, file_drop, reminder | handler | `change_mood` | - |
| `clear_emotions` | caution | emotion_personality | no | user, touch, file_drop, reminder | handler | `clear_emotions` | `ENABLE_EMOTION_ENGINE` |
| `clear_memory` | caution | memory | no | user, touch, file_drop, reminder | handler | `clear_memory` | - |
| `complete_task` | safe | memory | no | user, touch, file_drop, reminder | handler | `complete_task` | `ENABLE_TASKS` |
| `computer_use` | caution | computer_use | yes | user | handler | `computer_use` | `ENABLE_COMPUTER_USE` |
| `copy_to_clipboard` | caution | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `copy_to_clipboard` | - |
| `create_file` | danger | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `create_file` | - |
| `create_folder` | danger | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `create_folder` | - |
| `delete_file` | danger | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `delete_file` | - |
| `fetch_webpage` | caution | web_rag | no | user, touch, file_drop, reminder | handler | `fetch_webpage` | - |
| `force_close` | danger | app_control | yes | user, touch, file_drop, reminder | handler | `force_close` | `ENABLE_WINDOW_CONTROL` |
| `get_active_app` | safe | process_awareness | no | user, touch, file_drop, reminder | handler | `get_active_app` | - |
| `get_clipboard` | safe | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `get_clipboard` | - |
| `glitch_overlay` | safe | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `glitch_overlay` | `ENABLE_GLITCH_EFFECTS` |
| `idle` | safe | chat | no | user, touch, file_drop, reminder, ambient, tool_result, terminal_sentinel | core | - | - |
| `list_dir` | caution | read_only_continuation | no | user, touch, file_drop, reminder | handler | `list_dir` | - |
| `list_directory` | caution | read_only_continuation | no | user, touch, file_drop, reminder | handler | `list_directory` | - |
| `list_running_apps` | safe | process_awareness | no | user, touch, file_drop, reminder | handler | `list_running_apps` | - |
| `list_tasks` | safe | memory | no | user, touch, file_drop, reminder | handler | `list_tasks` | `ENABLE_TASKS` |
| `lock_screen` | danger | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `lock_screen` | - |
| `monitor_process` | safe | process_awareness | no | user, touch, file_drop, reminder | handler | `monitor_process` | - |
| `move_window` | safe | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `move_window` | - |
| `open_app` | caution | app_control | yes | user, touch, file_drop, reminder | handler | `open_app` | - |
| `open_browser` | safe | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `open_browser` | - |
| `open_file` | caution | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `open_file` | - |
| `open_folder` | safe | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `open_folder` | - |
| `open_settings` | caution | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `open_settings` | - |
| `open_url` | safe | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `open_url` | - |
| `play_emotion_sound` | safe | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `play_emotion_sound` | - |
| `play_sound` | caution | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `play_sound` | - |
| `play_virus_trivia` | safe | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `play_virus_trivia` | - |
| `popup` | safe | chat | no | user, touch, file_drop, reminder | core | - | - |
| `read_document` | safe | read_only_continuation | no | user, touch, file_drop, reminder | handler | `read_document` | - |
| `read_file` | caution | read_only_continuation | no | user, touch, file_drop, reminder | handler | `read_file` | - |
| `read_notepad` | safe | memory | no | user, touch, file_drop, reminder | handler | `read_notepad` | - |
| `recycle_bin_status` | safe | read_only_continuation | no | user, touch, file_drop, reminder | handler | `recycle_bin_status` | - |
| `rename_file` | danger | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `rename_file` | - |
| `request_path` | safe | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `request_path` | - |
| `request_screen_read` | safe | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `request_screen_read` | - |
| `restart` | danger | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `restart` | - |
| `run_command` | danger | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `run_command` | - |
| `search_files` | caution | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `search_files` | - |
| `search_memory` | safe | memory | no | user, touch, file_drop, reminder | handler | `search_memory` | `ENABLE_LONGTERM_MEMORY` |
| `search_web` | caution | web_rag | no | user, touch, file_drop, reminder | handler | `search_web` | - |
| `set_autostart` | danger | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `set_autostart` | `ENABLE_AUTOSTART_CONTROL` |
| `set_clipboard` | caution | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `set_clipboard` | - |
| `set_reminder` | safe | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `set_reminder` | - |
| `set_theme` | danger | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `set_theme` | `ENABLE_THEME_CONTROL` |
| `set_volume` | caution | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `set_volume` | - |
| `set_wallpaper` | caution | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `set_wallpaper` | - |
| `show_dialog` | caution | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `show_dialog` | - |
| `show_error_gif` | safe | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `show_error_gif` | - |
| `show_notification` | safe | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `show_notification` | - |
| `shutdown` | danger | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `shutdown` | - |
| `snap_to_center` | safe | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `snap_to_center` | - |
| `speak` | safe | chat | no | user, touch, file_drop, reminder, ambient, tool_result, terminal_sentinel | core | - | - |
| `system_info` | safe | read_only_continuation | no | user, touch, file_drop, reminder | handler | `system_info` | - |
| `take_screenshot` | safe | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `take_screenshot` | - |
| `target_window_close` | caution | app_control | yes | user, touch, file_drop, reminder | handler | `target_window_close` | `ENABLE_WINDOW_CONTROL` |
| `target_window_move` | caution | app_control | yes | user, touch, file_drop, reminder | handler | `target_window_move` | `ENABLE_WINDOW_CONTROL` |
| `target_window_resize` | caution | app_control | yes | user, touch, file_drop, reminder | handler | `target_window_resize` | `ENABLE_WINDOW_CONTROL` |
| `type_text` | caution | os_typing | yes | user, touch, file_drop, reminder | handler | `type_text` | `ENABLE_UNICODE_TYPING` |
| `view_dreams` | safe | memory | no | user, touch, file_drop, reminder | handler | `view_dreams` | `ENABLE_DREAMS` |
| `view_emotions` | safe | emotion_personality | no | user, touch, file_drop, reminder | handler | `view_emotions` | `ENABLE_EMOTION_ENGINE` |
| `view_memory` | safe | memory | no | user, touch, file_drop, reminder | handler | `view_memory` | - |
| `wake_user` | safe | chat | no | user, touch, file_drop, reminder | core | - | - |
| `write_file` | danger | advanced_os_integration | yes | user, touch, file_drop, reminder | handler | `write_file` | - |
