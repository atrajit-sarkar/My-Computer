from __future__ import annotations

import asyncio
import os
from typing import Dict

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from .command_runner import run_command, summarize_output
from .gemini_client import to_command_from_nl, to_commands_from_nl, CommandStyle
from .os_utils import detect_os
from .persistence import load_json, save_json


# Load environment
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WORK_DIR = os.getenv("WORK_DIR", ".")
DEFAULT_MODE = os.getenv("DEFAULT_MODE", "command").lower()
COMMAND_TIMEOUT_SEC = float(os.getenv("COMMAND_TIMEOUT_SEC", "60"))
ALLOW_EVERYONE = os.getenv("ALLOW_EVERYONE", "true").lower() == "true"
MODE_STORE_PATH = os.getenv("MODE_STORE_PATH", os.path.join(".data", "modes.json"))
CWD_STORE_PATH = os.getenv("CWD_STORE_PATH", os.path.join(".data", "cwd.json"))
ALLOWED_GUILD_IDS = {int(x) for x in os.getenv("ALLOWED_GUILD_IDS", "").replace(" ", "").split(",") if x.isdigit()}
ALLOWED_CHANNEL_IDS = {int(x) for x in os.getenv("ALLOWED_CHANNEL_IDS", "").replace(" ", "").split(",") if x.isdigit()}
ALLOWED_USER_IDS = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x.isdigit()}


class ModeStore:
    """Per-channel mode store with JSON persistence."""

    def __init__(self, default_mode: str = "command", path: str | None = None):
        self.path = path
        self.default = default_mode if default_mode in ("command", "chat") else "command"
        raw = load_json(self.path) if self.path else {}
        # keys are strings in JSON; convert to int
        self._modes: Dict[int, str] = {int(k): v for k, v in raw.items() if v in ("command", "chat")}

    def _persist(self):
        if not self.path:
            return
        data = {str(k): v for k, v in self._modes.items()}
        save_json(self.path, data)

    def get(self, channel_id: int) -> str:
        return self._modes.get(channel_id, self.default)

    def set(self, channel_id: int, mode: str) -> str:
        mode = mode if mode in ("command", "chat") else self.default
        self._modes[channel_id] = mode
        self._persist()
        return mode


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
mode_store = ModeStore(DEFAULT_MODE, MODE_STORE_PATH)


class CwdStore:
    """Per-channel working directory store with JSON persistence and WORK_DIR sandbox."""

    def __init__(self, base_dir: str, path: str | None = None):
        self.base = os.path.abspath(base_dir)
        self.path = path
        raw = load_json(self.path) if self.path else {}
        self._cwd: Dict[int, str] = {}
        for k, v in raw.items():
            try:
                cid = int(k)
                abs_v = os.path.abspath(v)
                if abs_v.startswith(self.base) and os.path.isdir(abs_v):
                    self._cwd[cid] = abs_v
            except Exception:
                continue

    def _persist(self):
        if not self.path:
            return
        data = {str(k): v for k, v in self._cwd.items()}
        save_json(self.path, data)

    def get(self, channel_id: int) -> str:
        return self._cwd.get(channel_id, self.base)

    def set(self, channel_id: int, new_dir: str) -> str:
        absd = os.path.abspath(new_dir)
        if not absd.startswith(self.base):
            raise ValueError("Path escapes WORK_DIR sandbox")
        if not os.path.isdir(absd):
            raise FileNotFoundError("Directory does not exist")
        self._cwd[channel_id] = absd
        self._persist()
        return absd


cwd_store = CwdStore(WORK_DIR, CWD_STORE_PATH)


def _resolve_cd_target(channel_id: int, arg: str | None) -> str:
    """Resolve a cd target relative to current cwd; None or empty goes to base."""
    if not arg or not arg.strip():
        # Like `cd` to HOME, we bring back to base
        return os.path.abspath(WORK_DIR)
    current = cwd_store.get(channel_id)
    candidate = os.path.abspath(os.path.join(current, arg.strip()))
    return candidate


def _preprocess_command_for_cwd(channel_id: int, command: str, is_windows: bool):
    """
    Detect leading cd/Set-Location and update cwd. Returns (maybe_remainder, changed_msg or None).
    - If only a cd is present, returns (None, ack_message)
    - If cd plus remainder (e.g., `cd x && ls`), sets cwd then returns (remainder, None)
    """
    import re

    text = command.strip()
    if not text:
        return None, None

    if is_windows:
        # Match Set-Location or cd at start
        m = re.match(r"^(?:Set-Location|cd)\s+([^;&]+)(?:\s*;|\s*&&\s*|\s*$)(.*)$", text, re.IGNORECASE)
    else:
        m = re.match(r"^cd\s+([^;&]+)(?:\s*;|\s*&&\s*|\s*$)(.*)$", text)

    if not m:
        # Also handle bare cd with no args
        if re.match(r"^(?:Set-Location|cd)\s*$", text, re.IGNORECASE if is_windows else 0):
            target = _resolve_cd_target(channel_id, None)
            try:
                newd = cwd_store.set(channel_id, target)
                return None, f"Changed directory to `{os.path.relpath(newd, os.path.abspath(WORK_DIR))}`"
            except Exception as e:
                return None, f"Failed to change directory: {e}"
        return text, None

    path_arg = m.group(1).strip().strip('"').strip("'")
    remainder = m.group(2).strip()
    target = _resolve_cd_target(channel_id, path_arg)
    try:
        newd = cwd_store.set(channel_id, target)
    except Exception as e:
        return None, f"Failed to change directory: {e}"

    if remainder:
        return remainder, None
    else:
        return None, f"Changed directory to `{os.path.relpath(newd, os.path.abspath(WORK_DIR))}`"


def _channel_key_from_obj(ch: discord.abc.MessageableChannel | discord.Thread) -> int:
    """Return a stable key for a channel/thread. Use parent channel id for threads."""
    try:
        # Threads have .parent
        parent = getattr(ch, "parent", None)
        if parent is not None:
            return parent.id
        # Normal channels
        return ch.id  # type: ignore[attr-defined]
    except Exception:
        # Fallback: try id directly
        return getattr(ch, "id", 0)


def _is_allowed_location(channel: discord.abc.MessageableChannel, guild: discord.Guild | None) -> bool:
    """Return True if this channel/guild is allowed by env settings.

    Rules:
    - If ALLOWED_GUILD_IDS is non-empty, guild must be in the set (DMs disallowed in that case).
    - If ALLOWED_CHANNEL_IDS is non-empty, the channel key (parent id for threads) must be in the set.
    - If a set is empty, it doesn't restrict that dimension.
    """
    # Guild restriction
    if ALLOWED_GUILD_IDS:
        if guild is None or guild.id not in ALLOWED_GUILD_IDS:
            return False
    # Channel restriction
    if ALLOWED_CHANNEL_IDS:
        key = _channel_key_from_obj(channel)
        if key not in ALLOWED_CHANNEL_IDS:
            return False
    return True


def _is_allowed_user(user: discord.abc.User | discord.Member) -> bool:
    """If ALLOWED_USER_IDS is non-empty, only those users are allowed."""
    if not ALLOWED_USER_IDS:
        return True
    try:
        return user.id in ALLOWED_USER_IDS
    except Exception:
        return False


def user_allowed(interaction: discord.Interaction) -> bool:
    if ALLOW_EVERYONE:
        return True
    # In the future: check roles/permissions
    return interaction.user.guild_permissions.administrator if interaction.guild else True


class FileEditModal(discord.ui.Modal, title="Edit/Create File"):
    def __init__(self):
        super().__init__()
        self.filename = discord.ui.TextInput(
            label="File path (relative to WORK_DIR)", placeholder="notes.txt", required=True
        )
        self.content = discord.ui.TextInput(
            label="File content (optional)", style=discord.TextStyle.paragraph, required=False
        )
        self.add_item(self.filename)
        self.add_item(self.content)

    async def on_submit(self, interaction: discord.Interaction):
        if not user_allowed(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        rel_path = str(self.filename.value).strip()
        text = str(self.content.value or "")
        base = os.path.abspath(WORK_DIR)
        path = os.path.abspath(os.path.join(base, rel_path))
        # Ensure path is within base
        if not path.startswith(base):
            await interaction.response.send_message("Invalid path.", ephemeral=True)
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        await interaction.response.send_message(f"Saved `{rel_path}` ({len(text)} bytes).", ephemeral=True)


class EditExistingFileModal(discord.ui.Modal, title="Edit File"):
    def __init__(self, rel_path: str, initial_text: str):
        super().__init__()
        self.rel_path = rel_path
        # Show path as read-only info and editable content field
        self.filename_display = discord.ui.TextInput(
            label="File path (read-only)", default=rel_path, required=True, max_length=400, style=discord.TextStyle.short
        )
        self.filename_display.disabled = True
        self.content = discord.ui.TextInput(
            label="File content", style=discord.TextStyle.paragraph, required=False, default=initial_text, max_length=3900
        )
        self.add_item(self.filename_display)
        self.add_item(self.content)

    async def on_submit(self, interaction: discord.Interaction):
        if not user_allowed(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        base = os.path.abspath(WORK_DIR)
        path = os.path.abspath(os.path.join(base, self.rel_path))
        if not path.startswith(base):
            await interaction.response.send_message("Invalid path.", ephemeral=True)
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        text = str(self.content.value or "")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        await interaction.response.send_message(f"Saved `{self.rel_path}` ({len(text)} bytes).", ephemeral=True)


@bot.event
async def on_ready():
    osinfo = detect_os()
    try:
        if ALLOWED_GUILD_IDS:
            # Clear global commands and sync only to allowed guilds
            tree.clear_commands(guild=None)
            total = 0
            for gid in ALLOWED_GUILD_IDS:
                synced = await tree.sync(guild=discord.Object(id=gid))
                total += len(synced)
            print(f"Synced guild commands to {len(ALLOWED_GUILD_IDS)} guild(s), total {total} cmds. OS {osinfo.name} shell {osinfo.shell}")
        else:
            synced = await tree.sync()
            print(f"Synced {len(synced)} global commands. OS {osinfo.name} shell {osinfo.shell}")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


class ModeGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="mode", description="Get or set the channel mode")

    @app_commands.command(name="get", description="Show current channel mode")
    async def get_mode(self, interaction: discord.Interaction):
        if not user_allowed(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        cur = mode_store.get(_channel_key_from_obj(interaction.channel))
        await interaction.response.send_message(f"Mode for this channel: `{cur}`", ephemeral=True)

    @app_commands.command(name="set", description="Set current channel mode")
    @app_commands.choices(value=[
        app_commands.Choice(name="command", value="command"),
        app_commands.Choice(name="chat", value="chat"),
    ])
    async def set_mode(self, interaction: discord.Interaction, value: app_commands.Choice[str]):
        if not user_allowed(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        newv = mode_store.set(_channel_key_from_obj(interaction.channel), value.value)
        await interaction.response.send_message(f"Mode set to `{newv}`.", ephemeral=True)


tree.add_command(ModeGroup())


@tree.command(name="run", description="Run a shell command")
@app_commands.describe(command="Command to execute")
async def run_cmd(interaction: discord.Interaction, command: str):
    if not user_allowed(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    if not _is_allowed_user(interaction.user):
        return
    if not _is_allowed_location(interaction.channel, interaction.guild):
        # Silently ignore: do not respond in disallowed locations
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    # Use stable channel key (parent channel for threads)
    key = _channel_key_from_obj(interaction.channel)
    # Update CWD if command is a cd/Set-Location; possibly run remainder
    remainder, changed = _preprocess_command_for_cwd(key, command, detect_os().is_windows)
    if changed and not remainder:
        await interaction.followup.send(changed, ephemeral=True)
        return
    work_dir = cwd_store.get(key)
    result = await run_command(remainder or command, work_dir=work_dir, timeout=COMMAND_TIMEOUT_SEC)
    msg = f"$ {command}\nexit={result.exit_code}\n"
    if result.stdout:
        msg += f"stdout:\n{summarize_output(result.stdout)}\n"
    if result.stderr:
        msg += f"stderr:\n{summarize_output(result.stderr)}"
    await interaction.followup.send(msg, ephemeral=True)


@tree.command(name="file", description="Create or edit a file via modal")
async def file_cmd(interaction: discord.Interaction):
    if not user_allowed(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    if not _is_allowed_user(interaction.user):
        return
    if not _is_allowed_location(interaction.channel, interaction.guild):
        return
    await interaction.response.send_modal(FileEditModal())


@tree.command(name="editfile", description="Open an existing file, edit, and save via modal")
@app_commands.describe(path="File path relative to WORK_DIR")
async def edit_file_cmd(interaction: discord.Interaction, path: str):
    if not user_allowed(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    if not _is_allowed_user(interaction.user):
        return
    if not _is_allowed_location(interaction.channel, interaction.guild):
        return
    rel_path = path.strip()
    base = os.path.abspath(WORK_DIR)
    abs_path = os.path.abspath(os.path.join(base, rel_path))
    if not abs_path.startswith(base):
        await interaction.response.send_message("Invalid path.", ephemeral=True)
        return
    initial_text = ""
    try:
        # Limit reading to 20KB to fit modal field limits
        max_bytes = 20000
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read(max_bytes + 1)
            initial_text = data[:max_bytes]
            if len(data) > max_bytes:
                initial_text += "\n...\n[truncated for modal]"
    except FileNotFoundError:
        await interaction.response.send_message("File not found.", ephemeral=True)
        return
    except IsADirectoryError:
        await interaction.response.send_message("Path is a directory.", ephemeral=True)
        return
    except Exception as e:
        await interaction.response.send_message(f"Error opening file: {e}", ephemeral=True)
        return

    await interaction.response.send_modal(EditExistingFileModal(rel_path, initial_text))

@bot.event
async def on_message(message: discord.Message):
    # Let commands extension process commands
    if message.author.bot:
        return
    await bot.process_commands(message)

    # User/location gating: ignore messages from disallowed users or locations
    if not _is_allowed_user(message.author):
        return
    if not _is_allowed_location(message.channel, message.guild):
        return

    # Handle chat-mode messages
    if message.guild is None:
        # For simplicity, process DMs as default mode
        modev = DEFAULT_MODE
    else:
        modev = mode_store.get(_channel_key_from_obj(message.channel))

    # Show typing while we work
    async with message.channel.typing():
        content = message.content.strip()
        if not content:
            return
        key = _channel_key_from_obj(message.channel)
        if modev == "command":
            # Strict command mode: never call Gemini, run raw input
            remainder, changed = _preprocess_command_for_cwd(key, content, detect_os().is_windows)
            if changed and not remainder:
                await message.channel.send(changed)
                return
            work_dir = cwd_store.get(key)
            result = await run_command(remainder or content, work_dir=work_dir, timeout=COMMAND_TIMEOUT_SEC)
            os_line = f"OS: {detect_os().name}\n"
            msg = f"{os_line}$ {content}\nexit={result.exit_code}\n"
            if result.stdout:
                msg += f"stdout:\n{summarize_output(result.stdout)}\n"
            if result.stderr:
                msg += f"stderr:\n{summarize_output(result.stderr)}"
            await message.channel.send(msg)
            return

        # chat mode -> convert to commands using Gemini
        style = CommandStyle.POWERSHELL if detect_os().is_windows else CommandStyle.BASH
        max_steps = int(os.getenv("CHAT_MAX_STEPS", "5"))
        commands = await to_commands_from_nl(content, style, max_steps=max_steps)
        if len(commands) <= 1:
            command = commands[0]
            remainder, changed = _preprocess_command_for_cwd(key, command, detect_os().is_windows)
            if changed and not remainder:
                await message.channel.send(changed)
                return
            work_dir = cwd_store.get(key)
            result = await run_command(remainder or command, work_dir=work_dir, timeout=COMMAND_TIMEOUT_SEC)
            os_line = f"OS: {detect_os().name}\n"
            msg = f"{os_line}$ {command}\nexit={result.exit_code}\n"
            if result.stdout:
                msg += f"stdout:\n{summarize_output(result.stdout)}\n"
            if result.stderr:
                msg += f"stderr:\n{summarize_output(result.stderr)}"
            await message.channel.send(msg)
            return
        else:
            await message.channel.send(f"Planning {len(commands)} step(s). Executing sequentially…")
            os_name = detect_os().name
            for idx, cmd in enumerate(commands, start=1):
                remainder, changed = _preprocess_command_for_cwd(key, cmd, detect_os().is_windows)
                if changed and not remainder:
                    await message.channel.send(f"[{idx}/{len(commands)}] {changed}")
                    continue
                await message.channel.send(f"[{idx}/{len(commands)}] $ {cmd}")
                work_dir = cwd_store.get(key)
                result = await run_command(remainder or cmd, work_dir=work_dir, timeout=COMMAND_TIMEOUT_SEC)
                msg = f"OS: {os_name}\nexit={result.exit_code}\n"
                if result.stdout:
                    msg += f"stdout:\n{summarize_output(result.stdout)}\n"
                if result.stderr:
                    msg += f"stderr:\n{summarize_output(result.stderr)}"
                await message.channel.send(msg)
                if result.exit_code != 0:
                    await message.channel.send(f"Stopped due to error at step {idx}.")
                    break
            return


@tree.command(name="cwd", description="Show or change the current working directory for this channel")
@app_commands.describe(path="Optional path relative to WORK_DIR to change to")
async def cwd_cmd(interaction: discord.Interaction, path: str | None = None):
    if not user_allowed(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    if not _is_allowed_user(interaction.user):
        return
    if not _is_allowed_location(interaction.channel, interaction.guild):
        return
    key = _channel_key_from_obj(interaction.channel)
    if path is None:
        cur = cwd_store.get(key)
        rel = os.path.relpath(cur, os.path.abspath(WORK_DIR))
        await interaction.response.send_message(f"Current directory: `{rel}`", ephemeral=True)
        return
    target = _resolve_cd_target(key, path)
    try:
        newd = cwd_store.set(key, target)
        rel = os.path.relpath(newd, os.path.abspath(WORK_DIR))
        await interaction.response.send_message(f"Changed directory to `{rel}`", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Failed to change directory: {e}", ephemeral=True)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("Missing DISCORD_TOKEN in environment or .env")
    try:
        bot.run(DISCORD_TOKEN)
    except KeyboardInterrupt:
        pass
