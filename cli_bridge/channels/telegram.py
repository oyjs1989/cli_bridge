"""Telegram channel implementation using python-telegram-bot."""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger
from telegram import BotCommand, ReplyParameters, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

from cli_bridge.bus.events import OutboundMessage
from cli_bridge.bus.queue import MessageBus
from cli_bridge.channels._streaming_mixin import StreamingMixin
from cli_bridge.channels._telegram_helpers import (
    markdown_to_telegram_html as _markdown_to_telegram_html,
)
from cli_bridge.channels._telegram_helpers import (
    retry_async as _retry_async,
)
from cli_bridge.channels._telegram_helpers import (
    split_message as _split_message,
)
from cli_bridge.channels.base import BaseChannel
from cli_bridge.channels.manager import register_channel
from cli_bridge.config.schema import TelegramConfig


@register_channel("telegram")
class TelegramChannel(StreamingMixin, BaseChannel):
    """Telegram channel using long polling."""

    name = "telegram"

    BOT_COMMANDS = [
        BotCommand("start", "Start the bot"),
        BotCommand("new", "Start a new conversation"),
        BotCommand("help", "Show available commands"),
    ]

    def __init__(self, config: TelegramConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: TelegramConfig = config
        self._app: Application | None = None
        self._typing_tasks: dict[str, asyncio.Task] = {}
        self._stream_messages: dict[str, int] = {}  # chat_id -> message_id for streaming
        self._last_stream_update: dict[str, float] = {}  # chat_id -> timestamp for throttling
        self._stream_buffer: dict[str, str] = {}  # chat_id -> buffered content
        self._stream_update_interval: float = 1.5  # Minimum seconds between edits

    async def start(self) -> None:
        """Start the Telegram bot with long polling."""
        if not self.config.token:
            logger.error("Telegram bot token not configured")
            return

        self._running = True

        # Increased connection pool and timeouts for better stability
        req = HTTPXRequest(
            connection_pool_size=32,
            pool_timeout=10.0,
            connect_timeout=30.0,
            read_timeout=60.0,
            write_timeout=60.0,
        )
        self._app = Application.builder().token(self.config.token).request(req).get_updates_request(req).build()
        self._app.add_error_handler(self._on_error)

        # Command handlers
        self._app.add_handler(CommandHandler("start", self._on_start))
        self._app.add_handler(CommandHandler("new", self._forward_command))
        self._app.add_handler(CommandHandler("help", self._on_help))

        # Message handler
        self._app.add_handler(
            MessageHandler(
                (filters.TEXT | filters.PHOTO | filters.VOICE | filters.Document.ALL) & ~filters.COMMAND,
                self._on_message
            )
        )

        logger.info("Starting Telegram bot (polling mode)...")

        await self._app.initialize()
        await self._app.start()

        bot_info = await self._app.bot.get_me()
        logger.info("Telegram bot @{} connected", bot_info.username)

        try:
            await self._app.bot.set_my_commands(self.BOT_COMMANDS)
        except Exception as e:
            logger.warning("Failed to register bot commands: {}", e)

        await self._app.updater.start_polling(allowed_updates=["message"], drop_pending_updates=True)
        logger.info("Telegram bot started and polling for updates")
        # 不在这里阻塞，让 ChannelManager 用 asyncio.create_task() 启动

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        self._running = False

        for chat_id in list(self._typing_tasks):
            self._stop_typing(chat_id)

        self._stream_messages.clear()
        self._last_stream_update.clear()
        self._stream_buffer.clear()

        if self._app:
            logger.info("Stopping Telegram bot...")
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            self._app = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Telegram."""
        if not self._app:
            logger.warning("Telegram bot not running")
            return

        try:
            chat_id = int(msg.chat_id)
        except ValueError:
            logger.error("Invalid chat_id: {}", msg.chat_id)
            return

        # Log outbound message for debugging
        logger.debug(f"Telegram outbound: chat_id={chat_id}, streaming={msg.metadata.get('_streaming')}, progress={msg.metadata.get('_progress')}, content_len={len(msg.content) if msg.content else 0}")

        # Handle streaming end - just clear state, don't send duplicate message
        if msg.metadata.get("_streaming_end"):
            chat_id_str = str(chat_id)
            # Send final buffered content if any
            if self._stream_buffer.get(chat_id_str):
                await self._flush_stream_buffer(chat_id, chat_id_str)
            self._stream_messages.pop(chat_id_str, None)
            self._last_stream_update.pop(chat_id_str, None)
            self._stream_buffer.pop(chat_id_str, None)
            # Ensure typing stops with correct key format
            self._stop_typing(chat_id_str)
            logger.info(f"Streaming ended for chat {chat_id}")
            return

        # Handle streaming messages first (before _progress check)
        if msg.metadata.get("_streaming"):
            await self._handle_streaming_message(chat_id, msg)
            return

        # Skip other progress messages
        if msg.metadata.get("_progress"):
            return

        # Clear streaming state for this chat and stop typing
        chat_id_str = str(chat_id)
        self._stream_messages.pop(chat_id_str, None)
        self._stop_typing(chat_id_str)

        logger.info(f"Sending final message to chat {chat_id}: content_len={len(msg.content) if msg.content else 0}")

        reply_params = None
        reply_to_message_id = msg.metadata.get("message_id")
        if reply_to_message_id:
            reply_params = ReplyParameters(message_id=reply_to_message_id, allow_sending_without_reply=True)

        # Send media files
        media_files = msg.media or msg.metadata.get("media") or []
        for media_path in media_files:
            try:
                ext = Path(media_path).suffix.lower()
                if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                    with open(media_path, 'rb') as f:
                        await _retry_async(
                            self._app.bot.send_photo,
                            chat_id=chat_id, photo=f, reply_parameters=reply_params
                        )
                elif ext == ".ogg":
                    with open(media_path, 'rb') as f:
                        await _retry_async(
                            self._app.bot.send_voice,
                            chat_id=chat_id, voice=f, reply_parameters=reply_params
                        )
                else:
                    with open(media_path, 'rb') as f:
                        await _retry_async(
                            self._app.bot.send_document,
                            chat_id=chat_id, document=f, reply_parameters=reply_params
                        )
            except Exception as e:
                logger.error("Failed to send media {}: {}", media_path, e)

        # Send text
        if msg.content and msg.content != "[empty message]":
            for chunk in _split_message(msg.content):
                try:
                    html = _markdown_to_telegram_html(chunk)
                    await _retry_async(
                        self._app.bot.send_message,
                        chat_id=chat_id, text=html, parse_mode="HTML", reply_parameters=reply_params
                    )
                except Exception as e:
                    logger.warning("HTML parse failed, falling back to plain text: {}", e)
                    try:
                        await _retry_async(
                            self._app.bot.send_message,
                            chat_id=chat_id, text=chunk, reply_parameters=reply_params
                        )
                    except Exception as e2:
                        logger.error("Error sending Telegram message: {}", e2)

    async def _flush_stream_buffer(self, chat_id: int, chat_id_str: str) -> None:
        """Flush the stream buffer and send/update the message."""
        content = self._stream_buffer.get(chat_id_str, "")
        if not content:
            return

        existing_message_id = self._stream_messages.get(chat_id_str)
        display_content = content[:4000] if len(content) > 4000 else content

        try:
            html = _markdown_to_telegram_html(display_content)
        except Exception as e:
            logger.warning(f"Markdown to HTML failed: {e}")
            html = display_content

        try:
            if existing_message_id:
                await _retry_async(
                    self._app.bot.edit_message_text,
                    chat_id=chat_id,
                    message_id=existing_message_id,
                    text=html,
                    parse_mode="HTML",
                )
                logger.debug(f"Edited streaming message {existing_message_id} in chat {chat_id}")
            else:
                sent_message = await _retry_async(
                    self._app.bot.send_message,
                    chat_id=chat_id,
                    text=html,
                    parse_mode="HTML",
                )
                self._stream_messages[chat_id_str] = sent_message.message_id
                logger.info(f"Sent new streaming message {sent_message.message_id} to chat {chat_id}")

        except Exception as e:
            if "message is not modified" in str(e).lower():
                return  # Ignore if content is the same
            logger.error("Failed to flush stream buffer: {}", e)

    async def _handle_streaming_message(self, chat_id: int, msg: OutboundMessage) -> None:
        """Handle streaming message updates by editing the same message."""
        content = msg.content
        if not content or content == "[empty message]":
            logger.debug(f"Skipping empty streaming message for chat {chat_id}")
            return

        chat_id_str = str(chat_id)

        # Update buffer with latest content and flush
        self._stream_buffer[chat_id_str] = content
        await self._flush_stream_buffer(chat_id, chat_id_str)
        # Note: Keep typing indicator running during streaming

    async def _on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        user = update.effective_user
        await update.message.reply_text(
            f"👋 Hi {user.first_name}! I'm cli-bridge.\n\n"
            "Send me a message and I'll respond!\n"
            "Type /help to see available commands."
        )

    async def _on_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        await update.message.reply_text(
            "🤖 cli-bridge commands:\n"
            "/new — Start a new conversation\n"
            "/help — Show available commands"
        )

    @staticmethod
    def _sender_id(user) -> str:
        sid = str(user.id)
        return f"{sid}|{user.username}" if user.username else sid

    async def _forward_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        await self._handle_message(
            sender_id=self._sender_id(update.effective_user),
            chat_id=str(update.message.chat_id),
            content=update.message.text,
        )

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return

        message = update.message
        user = update.effective_user
        chat_id = message.chat_id
        sender_id = self._sender_id(user)

        content_parts = []
        media_paths = []

        if message.text:
            content_parts.append(message.text)
        if message.caption:
            content_parts.append(message.caption)

        # Handle media
        media_file = None
        media_type = None

        if message.photo:
            media_file = message.photo[-1]
            media_type = "image"
        elif message.voice:
            media_file = message.voice
            media_type = "voice"
        elif message.document:
            media_file = message.document
            media_type = "file"

        if media_file and self._app:
            try:
                file = await self._app.bot.get_file(media_file.file_id)
                media_dir = Path.home() / ".cli-bridge" / "media"
                media_dir.mkdir(parents=True, exist_ok=True)

                ext = Path(file.file_path).suffix if file.file_path else ""
                file_path = media_dir / f"{media_file.file_id[:16]}{ext}"
                await file.download_to_drive(str(file_path))

                media_paths.append(str(file_path))
                content_parts.append(f"[{media_type}: {file_path}]")
            except Exception as e:
                logger.error("Failed to download media: {}", e)
                content_parts.append(f"[{media_type}: download failed]")

        content = "\n".join(content_parts) if content_parts else "[empty message]"

        str_chat_id = str(chat_id)
        self._start_typing(str_chat_id)

        await self._handle_message(
            sender_id=sender_id,
            chat_id=str_chat_id,
            content=content,
            media=media_paths,
            metadata={
                "message_id": message.message_id,
                "user_id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "is_group": message.chat.type != "private"
            }
        )

    def _start_typing(self, chat_id: str) -> None:
        self._stop_typing(chat_id)
        self._typing_tasks[chat_id] = asyncio.create_task(self._typing_loop(chat_id))

    def _stop_typing(self, chat_id: str) -> None:
        task = self._typing_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

    async def _typing_loop(self, chat_id: str) -> None:
        try:
            while self._app:
                try:
                    await _retry_async(
                        self._app.bot.send_chat_action,
                        chat_id=int(chat_id), action="typing"
                    )
                except Exception as e:
                    logger.debug("Typing action failed: {}", e)
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("Typing indicator stopped: {}", e)

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Telegram error: {}", context.error)
