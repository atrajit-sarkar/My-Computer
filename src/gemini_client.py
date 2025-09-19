from __future__ import annotations

import os
from typing import Optional

import httpx


GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"


async def generate_text(prompt: str, api_key: Optional[str] = None, timeout: float = 30.0) -> str:
    """Call Gemini 2.0 Flash and return raw text from the first candidate.

    Follows the provided curl shape. We pass the user prompt as a single text part.
    """
    key = api_key or os.getenv("GEMINI_API_KEY", "")
    if not key:
        return ""

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                ]
            }
        ]
    }

    headers = {
        "Content-Type": "application/json",
        "X-goog-api-key": key,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(GEMINI_ENDPOINT, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        # The API can return varying shapes; we attempt to extract text safely.
        try:
            candidates = data.get("candidates") or []
            if not candidates:
                return ""
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts and isinstance(parts[0], dict) and "text" in parts[0]:
                return parts[0]["text"].strip()
        except Exception:
            pass
        return ""

class CommandStyle:
    POWERSHELL = "powershell"
    BASH = "bash"


async def to_command_from_nl(message: str, style: str) -> str:
    """Prompt Gemini to produce a single shell command for the user's natural language input.

    style: 'powershell' or 'bash'
    """
    if style == CommandStyle.POWERSHELL:
        shell_instructions = (
            "Generate ONE Windows PowerShell command (not CMD). Use PowerShell cmdlets like Get-ChildItem, Select-String, "
            "Write-Output, etc. Avoid Linux utilities. Do not include code fences, comments, or explanations."
        )
        fallback = "Write-Output 'Unable to determine a command'"
    else:
        shell_instructions = (
            "Generate ONE bash command for Linux/macOS. Prefer POSIX tools (ls, grep, sed, awk). Do not include code fences, "
            "comments, or explanations."
        )
        fallback = "echo 'Unable to determine a command'"

    system_prompt = (
        "You are a command generator. Convert the user's request into exactly one shell command. "
        "Prefer safe, read-only commands when ambiguous."
    )
    prompt = f"{system_prompt}\n{shell_instructions}\nUser: {message}\nCommand:"
    text = await generate_text(prompt)
    # Post-process: remove code fences or extra lines
    text = text.strip().strip("`") if text else ""
    first_line = text.splitlines()[0] if text else ""
    # Provide a fallback when empty
    return first_line or fallback


async def to_commands_from_nl(message: str, style: str, max_steps: int = 5) -> list[str]:
    """Ask Gemini to break a multi-part instruction into a small ordered list of commands.

    Returns a list of commands (strings). If parsing fails, falls back to a single-step generation.
    """
    if style == CommandStyle.POWERSHELL:
        shell_hint = "Windows PowerShell"
    else:
        shell_hint = "bash (Linux/macOS)"

    system_prompt = (
        "You are a planner that converts the user's instruction into a short, safe, ordered list of shell commands. "
        "Output STRICTLY a JSON array of strings, each a single command. No comments or explanations."
    )
    safety = (
        "Prefer safe, read-only commands where possible. Do not use destructive commands unless explicitly requested."
    )
    prompt = (
        f"{system_prompt}\nShell: {shell_hint}. Limit to {max_steps} steps. {safety}\n"
        f"User: {message}\nCommands (JSON array only):"
    )
    text = await generate_text(prompt)
    text = (text or "").strip()
    # Attempt to locate a JSON array in the response
    import json, re
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        blob = match.group(0)
        try:
            arr = json.loads(blob)
            if isinstance(arr, list) and all(isinstance(x, str) for x in arr):
                return arr[:max_steps]
        except Exception:
            pass

    # Fallback to single command
    one = await to_command_from_nl(message, style)
    return [one]
