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
        channels = config.get("channels", [config["channel"]])
        super().__init__(
            token=config["oauth"],
            initial_channels=channels,
            loop=loop,
        )
        self.channels_map = {}
        self.chat_commands = CommandHandler(config, self)
        self.moderation = Moderation(config.get("moderation") or {})
        self.greetings = Greetings(config.get("greetings") or {})
        self._promo_task = None
        self._promo_message = config.get("promo_message", "")
        self._promo_interval = config.get("promo_interval_seconds", 600)

    async def event_ready(self):
        for name in self.cfg.get("channels", [self.cfg["channel"]]):
            ch = self.get_channel(name)
            if ch:
                self.channels_map[name] = ch
                log.info("Twitch: подключён к #%s", name)
        if self.bridge is not None:
            self.bridge.channel_id = self.cfg.get("discord_channel_id", 0)
        if self._promo_message and self._promo_interval > 0:
            self._promo_task = self.loop.create_task(self._promo_loop())

    async def _promo_loop(self):
        await asyncio.sleep(self._promo_interval)
        while True:
            try:
                for ch in self.channels_map.values():
                    if self._promo_message:
                        await ch.send(self._promo_message)
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
