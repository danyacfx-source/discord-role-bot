import asyncio
import logging

import twitchio

from twitch_bot.twitch_cmds import CommandHandler
from twitch_bot.moderation import Moderation
from twitch_bot.greetings import Greetings

log = logging.getLogger("twitch")


class TwitchChatClient(twitchio.Client):
    def __init__(self, config, loop, bridge):
        self.cfg = config
        self.bridge = bridge
        super().__init__(
            token=config["oauth"],
            initial_channels=[config["channel"]],
            loop=loop,
        )
        self.channel = None
        self.chat_commands = CommandHandler(config, self)
        self.moderation = Moderation(config.get("moderation") or {})
        self.greetings = Greetings(config.get("greetings") or {})
        self._promo_task = None
        self._promo_message = config.get("promo_message", "")
        self._promo_interval = config.get("promo_interval_seconds", 600)

    async def event_ready(self):
        self.channel = self.get_channel(self.cfg["channel"])
        self.moderation.set_channel(self.channel)
        log.info("Twitch: подключён как %s (канал #%s)", self.nick, self.cfg["channel"])
        if self.bridge is not None:
            self.bridge.channel_id = self.cfg.get("discord_channel_id", 0)
        if self._promo_message and self._promo_interval > 0:
            self._promo_task = self.loop.create_task(self._promo_loop())

    async def _promo_loop(self):
        await asyncio.sleep(self._promo_interval)
        while True:
            try:
                if self.channel and self._promo_message:
                    await self.channel.send(self._promo_message)
                    log.info("Twitch: промо-сообщение отправлено")
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Twitch: ошибка отправки промо")
            await asyncio.sleep(self._promo_interval)

    async def event_message(self, message):
        if message.echo:
            return
        author = message.author
        if author is None:
            return
        content = message.content or ""
        if await self.moderation.check(message):
            return
        if self.bridge is not None:
            await self.bridge.forward_to_discord(author.name, content)
        await self.chat_commands.handle(message)
        if (self.cfg.get("greetings") or {}).get("welcome_new_chatters", True):
            await self.greetings.handle(message)
