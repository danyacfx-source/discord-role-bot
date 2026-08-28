import asyncio
import logging
import re

log = logging.getLogger("twitch")


class TwitchDiscordBridge:
    def __init__(self, config, discord_bot):
        self.config = config
        self.discord_bot = discord_bot
        self.client = None
        self.channel_id = config.get("discord_channel_id", 0)
        self._queue = []
        self._lock = asyncio.Lock()
        self._flush_task = None

    async def forward_to_discord(self, user, content):
        if not self.config.get("twitch_to_discord", True):
            return
        if self.channel_id <= 0:
            return
        async with self._lock:
            self._queue.append(f"[Twitch] **{user}**: {content}")
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_loop())

    async def on_discord_message(self, message):
        if not self.config.get("discord_to_twitch", True):
            return
        if message.author.bot:
            return
        if message.channel.id != self.channel_id:
            return
        if self.client is None or not self.client.channels_map:
            return
        text = (message.content or "").replace("\n", " ")[:450]
        if not text.strip():
            return
        safe_name = re.sub(r"[^\w\s\-]", "", message.author.display_name)[:30]
        try:
            for ch in self.client.channels_map.values():
                try:
                    await ch.send(f"[Discord] {safe_name}: {text}")
                except Exception:
                    log.exception("Ошибка отправки в Twitch-канал %s", getattr(ch, "name", ch))
        except Exception:
            log.exception("Ошибка отправки в Twitch из Discord")

    async def _flush_loop(self):
        try:
            while True:
                await asyncio.sleep(self.config.get("flush_interval", 2))
                try:
                    await self._flush_now()
                except Exception:
                    log.exception("Ошибка отправки пачки в Discord")
        finally:
            self._flush_task = None

    async def _flush_now(self):
        async with self._lock:
            batch = self._queue
            self._queue = []
        if not batch:
            return
        channel = self.discord_bot.get_channel(self.channel_id)
        if channel is None:
            try:
                channel = await self.discord_bot.fetch_channel(self.channel_id)
            except Exception:
                log.warning(
                    "Bridge: канал Discord %s недоступен, %d сообщений возвращено в очередь",
                    self.channel_id,
                    len(batch),
                )
                async with self._lock:
                    self._queue.extend(batch)
                return
        for chunk in self._chunks(batch, 1900):
            try:
                await channel.send("\n".join(chunk))
            except Exception:
                log.exception("Ошибка отправки сообщения в Discord")
                async with self._lock:
                    self._queue.extend(chunk)

    @staticmethod
    def _chunks(lines, limit):
        current = []
        size = 0
        for line in lines:
            if current and size + len(line) + 1 > limit:
                yield current
                current = []
                size = 0
            current.append(line)
            size += len(line) + 1
        if current:
            yield current
