from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from weather_polymarket_bot.config import TelegramConfig


@dataclass(frozen=True)
class TelegramReply:
    city: str
    command: str
    text: str
    message_id: int | None


def import_telethon() -> tuple[Any, Any]:
    try:
        from telethon import TelegramClient  # type: ignore[import-not-found]
        from telethon.errors import RPCError  # type: ignore[import-not-found]
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Telethon is not installed. Run: python -m pip install -r requirements.txt"
        ) from error
    return TelegramClient, RPCError


async def wait_for_reply(client: Any, bot: Any, sent_id: int, timeout_seconds: float) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_error: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            async for message in client.iter_messages(bot, limit=8):
                if getattr(message, "id", 0) > sent_id and not getattr(message, "out", False):
                    text = getattr(message, "raw_text", "") or ""
                    if text.strip():
                        return message
        except Exception as error:  # Telegram can transiently race right after send.
            last_error = error
        await asyncio.sleep(0.5)
    if last_error is not None:
        raise TimeoutError(f"Timed out waiting for Telegram reply after transient error: {last_error}")
    raise TimeoutError("Timed out waiting for Telegram reply")


async def fetch_weather_round(config: TelegramConfig) -> list[TelegramReply]:
    config.validate_for_telegram()
    TelegramClient, _rpc_error = import_telethon()
    client = TelegramClient(config.session, config.api_id, config.api_hash)
    replies: list[TelegramReply] = []
    async with client:
        if not await client.is_user_authorized():
            await client.start(phone=config.phone)
        bot = await client.get_entity(config.bot_username)
        for city in config.cities:
            command = config.command_template.format(city=city)
            sent = await client.send_message(bot, command)
            reply = await wait_for_reply(client, bot, sent.id, config.timeout_seconds)
            replies.append(
                TelegramReply(
                    city=city,
                    command=command,
                    text=getattr(reply, "raw_text", "") or "",
                    message_id=getattr(reply, "id", None),
                )
            )
    return replies

