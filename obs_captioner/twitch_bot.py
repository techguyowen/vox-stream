"""Optional Twitch IRC Live Caption Chat Broadcaster."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("obs_captioner.twitch")


@dataclass
class TwitchConfig:
    enabled: bool = False
    channel: str = ""
    bot_username: str = ""
    oauth_token: str = ""  # oauth:xxxx
    message_prefix: str = "[CC] "
    min_interval_seconds: float = 1.5


class TwitchCaptionBot:
    """Streams finalized captions directly into Twitch chat for hearing-impaired viewers."""

    def __init__(self, config: TwitchConfig):
        self.config = config
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.is_connected = False
        self._send_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._worker_task: Optional[asyncio.Task] = None

    async def start(self) -> bool:
        """Connect to Twitch IRC server."""
        if not self.config.enabled or not self.config.channel or not self.config.oauth_token:
            return False

        logger.info(f"Connecting Twitch Caption Bot to #{self.config.channel}...")
        try:
            self.reader, self.writer = await asyncio.open_connection("irc.chat.twitch.tv", 6667)
            
            token = self.config.oauth_token
            if not token.startswith("oauth:"):
                token = f"oauth:{token}"

            username = self.config.bot_username or self.config.channel.lower()
            channel = self.config.channel.lower().lstrip("#")

            self.writer.write(f"PASS {token}\r\n".encode("utf-8"))
            self.writer.write(f"NICK {username}\r\n".encode("utf-8"))
            self.writer.write(f"JOIN #{channel}\r\n".encode("utf-8"))
            await self.writer.drain()

            self.is_connected = True
            self._worker_task = asyncio.create_task(self._send_worker())
            logger.info(f"Twitch Caption Bot joined #{channel} successfully.")
            return True
        except Exception as e:
            logger.warning(f"Could not connect Twitch Caption Bot: {e}")
            self.is_connected = False
            return False

    async def send_caption(self, text: str):
        """Queue a caption line to be broadcasted to Twitch chat."""
        if not self.is_connected or not text.strip():
            return
        clean_text = text.strip()
        msg = f"{self.config.message_prefix}{clean_text}"
        try:
            self._send_queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass

    async def _send_worker(self):
        """Worker to send messages while respecting Twitch chat rate limits."""
        channel = self.config.channel.lower().lstrip("#")
        while self.is_connected:
            try:
                msg = await self._send_queue.get()
                if self.writer:
                    # Sanitize newline chars for IRC protocol safety
                    safe_msg = msg.replace("\r", " ").replace("\n", " ")
                    line = f"PRIVMSG #{channel} :{safe_msg}\r\n"
                    self.writer.write(line.encode("utf-8"))
                    await self.writer.drain()
                await asyncio.sleep(self.config.min_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Twitch chat send error: {e}")
                await asyncio.sleep(2.0)

    async def stop(self):
        """Disconnect Twitch bot."""
        self.is_connected = False
        if self._worker_task:
            self._worker_task.cancel()
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        logger.info("Twitch Caption Bot stopped.")
