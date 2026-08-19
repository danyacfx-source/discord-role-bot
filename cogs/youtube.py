import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import CONFIG
from twitch_bot.bridge import TwitchDiscordBridge
from twitch_bot.youtube_api import YouTubeAPI
from twitch_bot.youtube_chat import YouTubeChatClient

log = logging.getLogger("youtube")

MSK = timezone(timedelta(hours=3))


class YouTube(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = CONFIG.get("youtube") or {}
        self.client = None
        self.bridge = None
        self._task = None
        self.api = YouTubeAPI(self.config)
        self._moderation_task = None

    async def cog_load(self):
        if not self.config.get("enabled", False):
            log.info("YouTube-модуль отключён (youtube.enabled=false)")
            return

        self.bridge = TwitchDiscordBridge(self.config, self.bot)
        self.client = YouTubeChatClient(
            self.config, self.bot.loop, self.bridge, self.bot
        )
        self._task = self.bot.loop.create_task(self._run_youtube())
        self._moderation_task = self.bot.loop.create_task(self._comment_moderation_loop())
        log.info("YouTube-модуль запущен: @%s", self.config.get("channel"))

    async def cog_unload(self):
        if self.client:
            await self.client.stop()
        if self._task:
            self._task.cancel()
        if self._moderation_task:
            self._moderation_task.cancel()
        await self.api.close()

    async def _run_youtube(self):
        try:
            await self.client.start()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("YouTube-клиент завершился с ошибкой")

    async def _comment_moderation_loop(self):
        await self.bot.wait_until_ready()
        mod_config = self.config.get("comment_moderation", {})
        if not mod_config.get("enabled", False):
            return

        interval = mod_config.get("check_seconds", 300)
        banned_words = mod_config.get("banned_words", [])
        channel_handle = self.config.get("channel", "Dendosich")

        while not self.bot.is_closed():
            try:
                item = await self.api.get_channel_id(channel_handle)
                if item:
                    channel_id = item["id"]
                    videos = await self.api.get_recent_videos(channel_id, max_results=3)
                    for v in videos:
                        vid_id = v.get("id", {}).get("videoId")
                        if vid_id:
                            deleted = await self.api.moderate_video_comments(vid_id, banned_words)
                            if deleted:
                                log.info("YouTube: удалено %d спам-комментариев под %s", deleted, vid_id)
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("YouTube: ошибка модерации комментариев")

            await asyncio.sleep(interval)

    @app_commands.command(name="yt_stats", description="Статистика YouTube канала")
    async def yt_stats(self, interaction: discord.Interaction):
        await interaction.response.defer()
        handle = self.config.get("channel", "Dendosich")

        item = await self.api.get_channel_id(handle)
        if not item:
            await interaction.followup.send("Канал не найден")
            return

        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        title = snippet.get("title", handle)
        description = snippet.get("description", "")[:200]
        subs = int(stats.get("subscriberCount", 0))
        views = int(stats.get("viewCount", 0))
        videos_count = int(stats.get("videoCount", 0))
        hidden_subs = stats.get("hiddenSubscriberCount", False)

        embed = discord.Embed(
            title=f"📊 {title}",
            description=description or "Нет описания",
            url=f"https://www.youtube.com/@{handle}",
            color=0xFF0000,
        )
        embed.add_field(name="Подписчики", value=f"{subs:,}".replace(",", " "), inline=True)
        embed.add_field(name="Просмотры", value=f"{views:,}".replace(",", " "), inline=True)
        embed.add_field(name="Видео", value=str(videos_count), inline=True)

        videos = await self.api.get_recent_videos(item["id"], max_results=3)
        if videos:
            vid_ids = [v["id"]["videoId"] for v in videos if "videoId" in v.get("id", {})]
            if vid_ids:
                vid_stats = await self.api.get_video_stats(vid_ids)
                top = []
                for vs in vid_stats[:3]:
                    s = vs.get("statistics", {})
                    name = vs.get("snippet", {}).get("title", "?")[:40]
                    vcount = int(s.get("viewCount", 0))
                    likes = int(s.get("likeCount", 0))
                    top.append(f"**{name}**\n👁 {vcount:,} · 👍 {likes:,}".replace(",", " "))
                if top:
                    embed.add_field(name="Последние видео", value="\n\n".join(top), inline=False)

        embed.set_thumbnail(url=snippet.get("thumbnails", {}).get("high", {}).get("url", ""))
        embed.set_footer(text=f"Обновлено {datetime.now(MSK).strftime('%H:%M:%S')} МСК")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="yt_comments", description="Модерация комментариев под последним видео")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def yt_comments(self, interaction: discord.Interaction, limit: int = 50):
        await interaction.response.defer()
        handle = self.config.get("channel", "Dendosich")
        item = await self.api.get_channel_id(handle)
        if not item:
            await interaction.followup.send("Канал не найден")
            return

        videos = await self.api.get_recent_videos(item["id"], max_results=1)
        if not videos:
            await interaction.followup.send("Нет видео")
            return

        vid_id = videos[0].get("id", {}).get("videoId")
        if not vid_id:
            await interaction.followup.send("Видео не найдено")
            return

        mod_config = self.config.get("comment_moderation", {})
        banned = mod_config.get("banned_words", [])
        deleted = await self.api.moderate_video_comments(vid_id, banned, max_results=limit)

        comments, _ = await self.api.get_comment_threads(vid_id, max_results=min(limit, 20))

        embed = discord.Embed(
            title="🛡 Модерация комментариев",
            description=f"Видео: https://youtube.com/watch?v={vid_id}",
            color=0x3498db,
        )
        embed.add_field(name="Удалено спама", value=str(deleted), inline=True)
        embed.add_field(name="Проверено", value=str(limit), inline=True)

        if comments:
            recent = []
            for c in comments[:5]:
                s = c.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
                author = s.get("authorDisplayName", "?")
                text = s.get("textDisplay", "")[:80]
                recent.append(f"**{author}**: {text}")
            if recent:
                embed.add_field(name="Последние комментарии", value="\n".join(recent), inline=False)

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="yt_stream", description="Создать стрим на YouTube")
    @app_commands.describe(
        title="Название стрима",
        description="Описание (необязательно)",
        minutes="Через сколько минут начать (по умолчанию 0 = сейчас)",
        privacy="Приватность: unlisted, public, private",
    )
    @app_commands.choices(privacy=[
        app_commands.Choice(name="Unlisted (только по ссылке)", value="unlisted"),
        app_commands.Choice(name="Public (публичный)", value="public"),
        app_commands.Choice(name="Private (приватный)", value="private"),
    ])
    @app_commands.checks.has_permissions(manage_channels=True)
    async def yt_stream(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str = "",
        minutes: int = 0,
        privacy: str = "unlisted",
    ):
        await interaction.response.defer()

        now = datetime.now(timezone.utc)
        start_time = (now + timedelta(minutes=minutes)).isoformat()

        data = await self.api.create_live_broadcast(
            title=title,
            description=description,
            scheduled_start=start_time,
            privacy=privacy,
        )

        if not data or "error" in data:
            err = data.get("error", {}).get("message", "Неизвестная ошибка") if data else "Нет ответа"
            await interaction.followup.send(f"Ошибка: {err}")
            return

        broadcast_id = data.get("id", "")
        snippet = data.get("snippet", {})
        status = data.get("status", {})

        embed = discord.Embed(
            title="🔴 YouTube стрим создан!",
            description=f"**{title}**",
            url=f"https://www.youtube.com/watch?v={broadcast_id}",
            color=0xFF0000,
        )
        embed.add_field(name="Приватность", value=privacy, inline=True)
        embed.add_field(name="Старт", value=f"<t:{int(now.timestamp()) + minutes * 60}:R>" if minutes else "Сейчас", inline=True)
        embed.add_field(name="ID", value=broadcast_id[:20] + "...", inline=False)
        embed.set_footer(text="Используй OBS для подключения к трансляции")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="yt_streams", description="Список активных стримов на YouTube")
    @app_commands.describe(status="all, active, upcoming, completed")
    @app_commands.choices(status=[
        app_commands.Choice(name="Все", value="all"),
        app_commands.Choice(name="Активные", value="active"),
        app_commands.Choice(name="Запланированные", value="upcoming"),
        app_commands.Choice(name="Завершённые", value="completed"),
    ])
    @app_commands.checks.has_permissions(manage_channels=True)
    async def yt_streams(self, interaction: discord.Interaction, status: str = "all"):
        await interaction.response.defer()
        broadcasts = await self.api.get_live_broadcasts(status)

        if not broadcasts:
            await interaction.followup.send("Нет стримов")
            return

        embed = discord.Embed(
            title="📺 YouTube стримы",
            color=0xFF0000,
        )

        for b in broadcasts[:5]:
            s = b.get("snippet", {})
            st = b.get("status", {})
            bid = b.get("id", "?")
            title = s.get("title", "?")
            pstatus = st.get("lifeCycleStatus", st.get("broadcastStatus", "?"))
            privacy = st.get("privacyStatus", "?")
            start = s.get("scheduledStartTime", "")
            embed.add_field(
                name=f"{title}",
                value=f"Статус: {pstatus}\nПриватность: {privacy}\nID: `{bid[:15]}...`",
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="yt_delete_stream", description="Удалить стрим на YouTube")
    @app_commands.describe(broadcast_id="ID стрима")
    @app_commands.checks.has_permissions(administrator=True)
    async def yt_delete_stream(self, interaction: discord.Interaction, broadcast_id: str):
        await interaction.response.defer()
        result = await self.api.delete_live_broadcast(broadcast_id)
        if result and "error" not in result:
            await interaction.followup.send(f"Стрим `{broadcast_id}` удалён")
        else:
            err = result.get("error", {}).get("message", "?") if result else "?"
            await interaction.followup.send(f"Ошибка: {err}")


async def setup(bot: commands.Bot):
    await bot.add_cog(YouTube(bot))
