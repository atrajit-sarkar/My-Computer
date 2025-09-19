make a discord bot that will have option to detect in which system/os it is runninng. Accordingly it will switch to windows/Linux/Mac command line mode.

The bot can execute every terminal commands through the chat of the bot. when select command-mode and when select chat-mode it will use gemini 2.0 flash model to parse the user query and make equivalent commands and run the right command. All the out put the commands the bot should send back to the chat.

Use the following code for gemini integration :
```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent" \
  -H 'Content-Type: application/json' \
  -H 'X-goog-api-key: GEMINI_API_KEY' \
  -X POST \
  -d '{
    "contents": [
      {
        "parts": [
          {
            "text": "Explain how AI works in a few words"
          }
        ]
      }
    ]
  }'
```

Stroe discord bot token, gemini api key and all the necessary variables that are seceret to.env file from there fetch.

Implementation notes:
- Implemented OS detection and shell wrapping in `src/os_utils.py`.
- Shell command execution with timeout and output collection in `src/command_runner.py`.
- Gemini 2.0 Flash integration following the provided curl shape in `src/gemini_client.py`.
- Discord bot with modes, slash commands, and file edit modal in `src/bot.py`.
- Configuration via `.env` (see `.env.example`).
- Basic tests in `tests/test_basic.py`.

Run with:
```powershell
pip install -r requirements.txt; copy .env.example .env; notepad .env; python -m src.bot
```

**Note:** For the file editing command use discord modal view where file name and content these section should be there. The the file content should be option so that user even can create a empty content file.
Implemented as `/file` command showing a modal with filename and optional content fields.