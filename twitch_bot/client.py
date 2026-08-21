import asyncio
import logging

import aiohttp
import twitchio

from twitch_bot.twitch_cmds import CommandHandler
from twitch_bot.moderation import Moderation
from twitch_bot.greetings import Greetings

log = logging.getLogger("twitch")

TWITCH_ANON_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"


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

    async def _is_channel_live(self, channel_name):
        headers = {"Client-Id": TWITCH_ANON_CLIENT_ID, "Content-Type": "application/json"}
        query = "query($login: String!){user(login: $login){stream{id}}}"
        payload = {"query": query, "variables": {"login": channel_name}}
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post("https://gql.twitch.tv/gql", headers=headers, json=payload) as resp:
                    data = await resp.json()
            user = (data.get("data") or {}).get("user") or {}
            return user.get("stream") is not None
        except Exception:
            log.exception("Twitch: ошибка проверки стрима для %s", channel_name)
            return False

    async def _promo_loop(self):
        await asyncio.sleep(self._promo_interval)
        while True:
            try:
                for name, ch in self.channels_map.items():
                    if self._promo_message and await self._is_channel_live(name):
                        await ch.send(self._promo_message)
                        log.info("Twitch: промо отправлено в #%s", name)
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
