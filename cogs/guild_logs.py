import datetime
import logging
import os
import subprocess
import sys
import time
from collections import OrderedDict

import discord
from discord.ext import commands
from discord.utils import format_dt

from config import CONFIG

log = logging.getLogger("guild_logs")


def _trunc(text: str, limit: int) -> str:
    text = (text or "").strip() or "∅"
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


class GuildLogs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cfg = CONFIG.get("guild_logs") or {}
        self._messages: "OrderedDict[int, dict]" = OrderedDict()
        self._max_cache = 1200
        self._removals: dict[int, float] = {}
        self._startup_posted = False

    def _cid(self, key: str) -> int:
        return self.cfg.get(key, 0)

    def _cnl(self, key: str):
        return self.bot.get_channel(self._cid(key))

    def _enabled(self) -> bool:
        return self.cfg.get("enabled", True)

    def _ignored_channel(self, channel) -> bool:
        if channel is None:
            return True
        cid = getattr(channel, "id", None)
        cat = getattr(getattr(channel, "category", None), "id", None)
        cats = set(self.cfg.get("ignore_category_ids") or [])
        chs = set(self.cfg.get("ignore_channel_ids") or [])
        return cid in chs or cat in cats

    def _ignored_author(self, author) -> bool:
        if author is None:
            return True
        if self.cfg.get("ignore_bots", True) and getattr(author, "bot", False):
            return True
        return False

    def _avatar(self, obj) -> str:
        avatar = getattr(obj, "display_avatar", None)
        if avatar is not None:
            return avatar.url
        return discord.utils.MISSING

    @commands.Cog.listener()
    async def on_ready(self):
        if self._startup_posted or not self.cfg.get("startup_notify", True):
            return
        self._startup_posted = True
        await self._post_deploy_log()
        ch = self._cnl("bot_log_channel_id")
        if ch is None:
            return
        try:
            embed = discord.Embed(
                title="🚀 Бот запущен и готов к работе",
                color=discord.Color.blurple(),
            )
            embed.add_field(name="Бот", value=f"<@{self.bot.user.id}>", inline=True)
            embed.add_field(name="Серверов", value=str(len(self.bot.guilds)), inline=True)
            embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
            await ch.send(embed=embed)
        except Exception:
            log.exception("Не удалось отправить сообщение о запуске")

    @staticmethod
    def _deploy_info() -> dict:
        info = {
            "pid": os.getpid(),
            "python": sys.version.split()[0],
            "repo": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        }
        try:
            out = subprocess.run(
                ["git", "-C", info["repo"], "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            info["commit"] = (out.stdout or "").strip() or "?"
        except Exception:
            info["commit"] = "?"
        try:
            out = subprocess.run(
                ["git", "-C", info["repo"], "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            info["branch"] = (out.stdout or "").strip() or "?"
        except Exception:
            info["branch"] = "?"
        return info

    async def _post_deploy_log(self):
        ch = self._cnl("deploy_log_channel_id")
        if ch is None:
            return
        try:
            info = self._deploy_info()
            embed = discord.Embed(
                title="Deploys",
                color=discord.Color.brand_green(),
                timestamp=datetime.datetime.now(datetime.timezone.utc),
            )
            embed.add_field(name="Бот", value=f"<@{self.bot.user.id}>", inline=True)
            embed.add_field(name="Версия (commit)", value=f"`{info['commit']}`", inline=True)
            embed.add_field(name="Ветка", value=f"`{info['branch']}`", inline=True)
            embed.add_field(name="PID", value=f"`{info['pid']}`", inline=True)
            embed.add_field(name="Python", value=f"`{info['python']}`", inline=True)
            await ch.send(embed=embed)
        except Exception:
            log.exception("Не удалось отправить лог деплоя")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not self._enabled() or self._ignored_author(member):
            return
        ch = self._cnl("member_log_channel_id")
        if ch is None:
            return
        now = time.time()
        since = now - self._removals.pop(member.id, 0)
        window = max(60, int(self.cfg.get("rejoin_window_minutes", 30)) * 60)
        rejoin = 0 < since < window
        embed = discord.Embed(
            title="Вступил на сервер",
            description=f"{member.mention}\n**{member.display_name}**",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        if not self._avatar(member) is discord.utils.MISSING:
            embed.set_author(name=member.display_name, icon_url=self._avatar(member))
        embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Всего участников", value=str(member.guild.member_count), inline=True)
        embed.add_field(
            name="Аккаунт создан",
            value=format_dt(member.created_at, style="R"),
            inline=True,
        )
        if rejoin:
            embed.add_field(name="Повторный вход", value=f"~{int(since // 60)} мин назад", inline=True)
        try:
            await ch.send(embed=embed)
        except Exception:
            log.exception("Ошибка лога вступления")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if not self._enabled() or self._ignored_author(member):
            return
        self._removals[member.id] = time.time()
        ch = self._cnl("member_log_channel_id")
        if ch is None:
            return
        embed = discord.Embed(
            title="Вышел с сервера",
            description=f"{member.mention}\n**{member.display_name}**",
            color=discord.Color.dark_red(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        if not self._avatar(member) is discord.utils.MISSING:
            embed.set_author(name=member.display_name, icon_url=self._avatar(member))
        embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Всего участников", value=str(member.guild.member_count), inline=True)
        since = time.time() - getattr(member, "joined_at", None).timestamp() if getattr(member, "joined_at", None) else None
        if since is not None:
            embed.add_field(name="Пробыл на сервере", value=format_dt(member.joined_at, style="R"), inline=True)
        try:
            await ch.send(embed=embed)
        except Exception:
            log.exception("Ошибка лога выхода")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not self._enabled():
            return
        if message.guild is None or self._ignored_channel(message.channel) or self._ignored_author(message.author):
            return
        self._messages[message.id] = {
            "author_id": message.author.id,
            "author": message.author.display_name,
            "channel": message.channel,
            "content": message.content or "",
            "attachments": len(message.attachments),
            "created": message.created_at,
        }
        while len(self._messages) > self._max_cache:
            self._messages.popitem(last=False)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not self._enabled():
            return
        if getattr(message, "guild", None) is None or self._ignored_channel(message.channel):
            return
        ch = self._cnl("message_log_channel_id")
        if ch is None:
            return
        snap = self._messages.pop(message.id, None)
        author = getattr(message, "author", None)
        embed = discord.Embed(
            title="🗑 Удалено сообщение",
            color=discord.Color.orange(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        if snap:
            embed.set_author(name=snap["author"], icon_url=(getattr(message.author, "display_avatar", None) or discord.utils.MISSING).url if getattr(message.author, "display_avatar", None) else discord.utils.MISSING)
            embed.add_field(name="Автор", value=f"<@{snap['author_id']}>", inline=True)
            embed.add_field(name="Канал", value=snap["channel"].mention, inline=True)
            embed.add_field(name="Вложений", value=str(snap["attachments"]), inline=True)
            embed.add_field(name="Содержимое", value=f"```\n{_trunc(snap['content'], 1000)}\n```", inline=False)
        else:
            uname = getattr(author, "display_name", "?")
            uid = getattr(author, "id", "?")
            embed.set_author(name=uname)
            embed.add_field(name="Автор", value=f"<@{uid}>", inline=True)
            embed.add_field(name="Канал", value=message.channel.mention, inline=True)
            embed.add_field(name="Содержимое", value="Не закешировано", inline=False)
        try:
            await ch.send(embed=embed)
        except Exception:
            log.exception("Ошибка лога удаления")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not self._enabled():
            return
        if getattr(after, "guild", None) is None or self._ignored_channel(after.channel) or self._ignored_author(after.author):
            return
        if (before.content or "") == (after.content or ""):
            return
        ch = self._cnl("message_log_channel_id")
        if ch is None:
            return
        snap = self._messages.get(after.id)
        old = snap["content"] if snap else (before.content or "")
        new = after.content or ""
        if snap:
            snap["content"] = new
        author = after.author
        embed = discord.Embed(
            title="✏️ Изменено сообщение",
            color=discord.Color.blurple(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        if getattr(author, "display_avatar", None):
            embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
        else:
            embed.set_author(name=getattr(author, "display_name", "?"))
        embed.add_field(name="Автор", value=f"<@{author.id}>", inline=True)
        embed.add_field(name="Канал", value=after.channel.mention, inline=True)
        if after.jump_url:
            embed.add_field(name="Перейти", value=f"[Открыть]({after.jump_url})", inline=True)
        embed.add_field(name="Было", value=f"```\n{_trunc(old, 900)}\n```", inline=False)
        embed.add_field(name="Стало", value=f"```\n{_trunc(new, 900)}\n```", inline=False)
        try:
            await ch.send(embed=embed)
        except Exception:
            log.exception("Ошибка лога изменения")

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if not self._enabled() or self._ignored_author(member):
            return
        b, a = before.channel, after.channel
        if b is a:
            return
        ch = self._cnl("voice_log_channel_id")
        if ch is None:
            return
        if b is None:
            title, color, target = "🎙 Вошёл в голосовой", discord.Color.green(), a
        elif a is None:
            title, color, target = "🚪 Вышел из голосового", discord.Color.orange(), b
        else:
            title, color, target = "↔️ Перешёл в голосовом", discord.Color.blurple(), a
        embed = discord.Embed(
            title=title,
            description=member.mention,
            color=color,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        if member.display_avatar:
            embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
        name = getattr(target, "name", "?")
        cid = getattr(target, "id", "?")
        embed.add_field(name="Канал", value=f"`{name}` (`{cid}`)", inline=True)
        if b is not None and a is not None and b is not a:
            embed.add_field(name="Было", value=f"`{b.name}`", inline=True)
        try:
            await ch.send(embed=embed)
        except Exception:
            log.exception("Ошибка лога голосового канала")

    async def post_mod_log(
        self,
        *,
        member: discord.Member,
        action: str,
        reason: str = "",
        channel=None,
        content: str = "",
    ):
        if not self._enabled():
            return
        ch = self._cnl("mod_log_channel_id")
        if ch is None:
            return
        embed = discord.Embed(
            title=f"🛡 {action}",
            color=discord.Color.brand_red(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        if getattr(member, "display_avatar", None):
            embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.add_field(name="Участник", value=f"{member.mention} (`{member.id}`)", inline=True)
        if channel is not None:
            mention = getattr(channel, "mention", None)
            embed.add_field(name="Канал", value=mention or str(channel), inline=True)
        if reason:
            embed.add_field(name="Причина", value=_trunc(reason, 400), inline=False)
        if content:
            embed.add_field(name="Сообщение", value=f"```\n{_trunc(content, 900)}\n```", inline=False)
        try:
            await ch.send(embed=embed)
        except Exception:
            log.exception("Ошибка лога модерации")


async def setup(bot: commands.Bot):
    await bot.add_cog(GuildLogs(bot))