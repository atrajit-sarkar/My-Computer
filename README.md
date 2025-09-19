# Discord Command/Chat Bot

A Discord bot that can execute OS-specific shell commands or parse natural language into commands using Gemini 2.0 Flash, sending outputs back to chat. Includes a file-edit modal for creating/updating files.

## Features
- Auto-detect OS and run commands accordingly (Windows/Linux/macOS)
- Two modes per channel: `command` and `chat`
- `chat` mode uses Gemini 2.0 Flash to translate requests into commands and executes them
- Return stdout/stderr/exit codes back to Discord messages (truncated when large)
- File edit modal to create/modify files (optional content)
- Config via `.env`

## Setup
1. Create and activate a Python 3.10+ environment.
2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill values:

```powershell
copy .env.example .env
```

Required values:
- `DISCORD_TOKEN`: Your Discord bot token
- `GEMINI_API_KEY`: Google Generative Language API key
- `WORK_DIR`: Directory where commands run (default current folder)
- `DEFAULT_MODE`: `command` or `chat`
- `COMMAND_TIMEOUT_SEC`: Safety timeout for command execution
- `ALLOW_EVERYONE`: If `true`, allow all users; otherwise restrict to roles configured in code

## Run

```powershell
python -m src.bot
```

## Commands
- `/mode set <command|chat>`: Change channel mode (now a subcommand: `/mode set value:<...>`) 
- `/mode get`: Show current channel mode (`/mode get`)
- `/run <command>`: Run a command directly
- `/file edit`: Open modal to create/edit a file
- `/editfile <path>`: Open existing file, edit in modal (content prefilled)
- `/cwd [path]`: Show or change the per-channel working directory

In `chat` mode, just send a message; the bot will attempt to convert it to a command using Gemini and run it.
If your message contains multiple tasks, the bot will try to plan a short, ordered to-do list of OS-specific commands and execute them one-by-one, stopping on the first error.

## Notes
- Outputs are truncated to keep messages within Discord limits. Use files for large outputs.
- Commands run in `WORK_DIR`. Be careful with destructive commands.
- Channel mode is persisted per-channel in `MODE_STORE_PATH` (default `.data/modes.json`).
- `/editfile` reads up to ~20KB to fit modal limits; larger files will be truncated in the modal view.
- Multi-step planning: set `CHAT_MAX_STEPS` (default `5`). The bot executes steps sequentially and reports progress.
- Per-channel working directory: the bot tracks CWD per channel and persists it in `CWD_STORE_PATH`. Use shell `cd`/`Set-Location`, or `/cwd` to view/change. All paths are sandboxed under `WORK_DIR`.
- Restrict usage by location: set `ALLOWED_GUILD_IDS` and/or `ALLOWED_CHANNEL_IDS` (comma-separated IDs). When set, the bot ignores messages and slash commands outside those servers/channels. If `ALLOWED_GUILD_IDS` is set, slash commands are synced only to those guilds.
