import asyncio
import json
import time
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands, tasks

from config import CONFIG

log = logging.getLogger("youtube_growth")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
YT_STATE_FILE = DATA_DIR / "youtube_growth_state.json"
MSK = timezone(timedelta(hours=3))


def _load_state():
    if YT_STATE_FILE.exists():
        try:
            return json.loads(YT_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(data):
    try:
        YT_STATE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


class YouTubeGrowth(commands.Cog):
    """YouTube-specific growth features: shorts, premieres, analytics, CTA, comment bridge."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = CONFIG.get("youtube") or {}
        self._state = _load_state()
        self._known_video_ids: set = set(self._state.get("known_videos", []))
        self._known_short_ids: set = set(self._state.get("known_shorts", []))
        self._last_analytics_post = self._state.get("last_analytics_post", 0)
        self._session: aiohttp.ClientSession | None = None
        self._access_token: str | None = None
        self._token_expires: float = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self._session

    async def _get_access_token(self) -> str | None:
        if self._access_token and time.time() < self._token_expires:
            return self._access_token
        client_id = self.config.get("client_id", "")
        client_secret = self.config.get("client_secret", "")
        refresh_token = self.config.get("refresh_token", "")
        if not all([client_id, client_secret, refresh_token]):
            return None
        try:
            session = await self._get_session()
            async with session.post("https://oauth2.googleapis.com/token", data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }) as resp:
                data = await resp.json()
                if "access_token" in data:
                    self._access_token = data["access_token"]
                    self._token_expires = time.time() + data.get("expires_in", 3600) - 60
                    return self._access_token
        except Exception:
            log.exception("YouTubeGrowth: token refresh failed")
        return None

    async def _api(self, method: str, endpoint: str, **kwargs) -> dict | None:
        token = await self._get_access_token()
        if not token:
            return None
        session = await self._get_session()
        url = f"https://www.googleapis.com/youtube/v3{endpoint}"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            if method == "GET":
                async with session.get(url, headers=headers, **kwargs) as resp:
                    return await resp.json()
            elif method == "POST":
                async with session.post(url, headers=headers, **kwargs) as resp:
                    return await resp.json()
            elif method == "DELETE":
                async with session.delete(url, headers=headers, **kwargs) as resp:
                    if resp.status == 204:
                        return {"status": "ok"}
                    return await resp.json()
        except Exception:
            log.exception("YouTubeGrowth: API error %s %s", method, endpoint)
            return None

    async def _get_channel_id(self) -> str | None:
        handle = self.config.get("channel", "")
        if not handle:
            return None
        data = await self._api("GET", "/channels", params={
            "part": "id",
            "forHandle": handle,
        })
        if data:
            items = data.get("items", [])
            if items:
                return items[0].get("id")
        return None

    def _notify_channel(self) -> discord.TextChannel | None:
        ch_id = self.config.get("notify_channel_id", 0)
        if ch_id:
            return self.bot.get_channel(ch_id)
        return None

    def _save(self):
        self._state["known_videos"] = list(self._known_video_ids)[-200:]
        self._state["known_shorts"] = list(self._known_short_ids)[-200:]
        self._state["last_analytics_post"] = self._last_analytics_post
        _save_state(self._state)

    # ── Feature 1: YouTube Shorts notifications ──
    async def _check_shorts(self):
        channel_id = await self._get_channel_id()
        if not channel_id:
            return
        data = await self._api("GET", "/search", params={
            "part": "id,snippet",
            "channelId": channel_id,
            "order": "date",
            "maxResults": 10,
            "type": "video",
        })
        if not data:
            return
        ch = self._notify_channel()
        if not ch:
            return
        for item in data.get("items", []):
            vid = item.get("id", {}).get("videoId")
            if not vid or vid in self._known_short_ids:
                continue
            vid_data = await self._api("GET", "/videos", params={
                "part": "contentDetails,snippet",
                "id": vid,
            })
            if not vid_data or not vid_data.get("items"):
                continue
            v = vid_data["items"][0]
            duration = v.get("contentDetails", {}).get("duration", "")
            snippet = v.get("snippet", {})
            title = snippet.get("title", "?")
            thumb = snippet.get("thumbnails", {}).get("high", {}).get("url", "")
            is_short = "PT" in duration and self._parse_duration_seconds(duration) <= 60
            if not is_short:
                continue
            self._known_short_ids.add(vid)
            embed = discord.Embed(
                title=f"📱 Новый YouTube Short: {title}",
                url=f"https://www.youtube.com/shorts/{vid}",
                color=0xFF0000,
            )
            if thumb:
                embed.set_thumbnail(url=thumb)
            embed.set_footer(text="YouTube Shorts")
            ping_role = self.config.get("ping_role_id", 0)
            content = f"<@&{ping_role}>" if ping_role else None
            try:
                await ch.send(content=content, embed=embed)
            except Exception:
                log.exception("YouTubeGrowth: failed to post Short notification")

    # ── Feature 2: YouTube Premiere countdown ──
    async def _check_premieres(self):
        channel_id = await self._get_channel_id()
        if not channel_id:
            return
        data = await self._api("GET", "/search", params={
            "part": "id,snippet",
            "channelId": channel_id,
            "order": "date",
            "maxResults": 5,
            "type": "video",
            "eventType": "upcoming",
        })
        if not data:
            return
        ch = self._notify_channel()
        if not ch:
            return
        for item in data.get("items", []):
            vid = item.get("id", {}).get("videoId")
            if not vid:
                continue
            snippet = item.get("snippet", {})
            title = snippet.get("title", "?")
            publish_at = snippet.get("publishedAt", "")
            key = f"premiere:{vid}"
            if key in self._state:
                continue
            self._state[key] = True
            embed = discord.Embed(
                title=f"🎬 Премьера: {title}",
                description=f"Скоро на YouTube! Не пропусти.",
                url=f"https://www.youtube.com/watch?v={vid}",
                color=0xFF0000,
            )
            if publish_at:
                try:
                    dt = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
                    embed.add_field(name="Старт", value=f"<t:{int(dt.timestamp())}:R>")
                except Exception:
                    pass
            embed.set_footer(text="Добавь в календарь!")
            ping_role = self.config.get("ping_role_id", 0)
            content = f"<@&{ping_role}>" if ping_role else None
            try:
                await ch.send(content=content, embed=embed)
            except Exception:
                log.exception("YouTubeGrowth: failed to post premiere notification")

    # ── Feature 3: Top comments → Discord bridge ──
    async def _bridge_top_comments(self):
        channel_id = await self._get_channel_id()
        if not channel_id:
            return
        data = await self._api("GET", "/search", params={
            "part": "id",
            "channelId": channel_id,
            "order": "viewCount",
            "maxResults": 1,
            "type": "video",
        })
        if not data or not data.get("items"):
            return
        vid = data["items"][0].get("id", {}).get("videoId")
        if not vid:
            return
        comments_data = await self._api("GET", "/commentThreads", params={
            "part": "snippet",
            "videoId": vid,
            "order": "relevance",
            "maxResults": 10,
        })
        if not comments_data:
            return
        comments = []
        for ct in comments_data.get("items", []):
            s = ct.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            text = s.get("textDisplay", "")
            author = s.get("authorDisplayName", "?")
            likes = int(s.get("likeCount", 0))
            if likes >= 3 and len(text) > 10:
                comments.append({"author": author, "text": text[:200], "likes": likes})
        if not comments:
            return
        ch = self._notify_channel()
        if not ch:
            return
        top = comments[:3]
        lines = []
        for c in top:
            lines.append(f"**{c['author']}** ({c['likes']}👍): {c['text']}")
        embed = discord.Embed(
            title=f"💬 Лучшие комментарии под видео",
            description="\n\n".join(lines),
            url=f"https://www.youtube.com/watch?v={vid}",
            color=0xFF0000,
        )
        embed.set_footer(text="Подпишись на YouTube и оставь комментарий!")
        try:
            await ch.send(embed=embed)
        except Exception:
            log.exception("YouTubeGrowth: failed to bridge comments")

    # ── Feature 4: Weekly analytics report ──
    async def _post_analytics(self):
        now = time.time()
        if now - self._last_analytics_post < 7 * 86400:
            return
        channel_id = await self._get_channel_id()
        if not channel_id:
            return
        data = await self._api("GET", "/channels", params={
            "part": "statistics,snippet",
            "id": channel_id,
        })
        if not data or not data.get("items"):
            return
        item = data["items"][0]
        stats = item.get("statistics", {})
        snippet = item.get("snippet", {})
        ch = self._notify_channel()
        if not ch:
            return
        subs = int(stats.get("subscriberCount", 0))
        views = int(stats.get("viewCount", 0))
        videos = int(stats.get("videoCount", 0))
        title = snippet.get("title", "?")
        embed = discord.Embed(
            title=f"📈 YouTube аналитика: {title}",
            color=0xFF0000,
        )
        embed.add_field(name="Подписчики", value=f"{subs:,}".replace(",", " "), inline=True)
        embed.add_field(name="Просмотры", value=f"{views:,}".replace(",", " "), inline=True)
        embed.add_field(name="Видео", value=str(videos), inline=True)
        videos_data = await self._api("GET", "/search", params={
            "part": "id,snippet",
            "channelId": channel_id,
            "order": "date",
            "maxResults": 5,
            "type": "video",
        })
        if videos_data and videos_data.get("items"):
            recent_ids = [v["id"]["videoId"] for v in videos_data["items"] if "videoId" in v.get("id", {})]
            if recent_ids:
                vid_stats = await self._api("GET", "/videos", params={
                    "part": "statistics,snippet",
                    "id": ",".join(recent_ids[:5]),
                })
                if vid_stats and vid_stats.get("items"):
                    lines = []
                    for v in vid_stats["items"]:
                        s = v.get("statistics", {})
                        n = v.get("snippet", {}).get("title", "?")[:40]
                        vc = int(s.get("viewCount", 0))
                        lk = int(s.get("likeCount", 0))
                        lines.append(f"**{n}** — 👁 {vc:,} · 👍 {lk:,}".replace(",", " "))
                    if lines:
                        embed.add_field(name="Последние видео", value="\n".join(lines), inline=False)
        embed.set_footer(text=f"Обновлено {datetime.now(MSK).strftime('%d.%m.%Y %H:%M')} МСК")
        try:
            await ch.send(embed=embed)
            self._last_analytics_post = now
            self._save()
        except Exception:
            log.exception("YouTubeGrowth: failed to post analytics")

    # ── Feature 5: CTA during stream ──
    async def _stream_cta_loop(self):
        await self.bot.wait_until_ready()
        interval = self.config.get("cta_interval_minutes", 15) * 60
        while not self.bot.is_closed():
            try:
                from twitch_bot.stream_state import is_stream_live
                if not is_stream_live():
                    await asyncio.sleep(60)
                    continue
                ch = self._notify_channel()
                if ch:
                    msgs = [
                        "📹 Смотрите ещё больше на YouTube! Подписывайтесь: https://www.youtube.com/@{}",
                        "🎮 Новые видео каждый день на YouTube! youtube.com/@{}",
                        "🔔 Не пропустите новый контент — подпишитесь на YouTube!",
                    ]
                    import random
                    msg = random.choice(msgs).format(self.config.get("channel", ""))
                    try:
                        await ch.send(msg)
                    except Exception:
                        pass
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("YouTubeGrowth: CTA error")
            await asyncio.sleep(interval)

    # ── Feature 6: Auto-reply to top comments ──
    async def _auto_reply_comments(self):
        channel_id = await self._get_channel_id()
        if not channel_id:
            return
        data = await self._api("GET", "/search", params={
            "part": "id",
            "channelId": channel_id,
            "order": "date",
            "maxResults": 3,
            "type": "video",
        })
        if not data:
            return
        replied = self._state.get("replied_comments", set())
        for item in data.get("items", []):
            vid = item.get("id", {}).get("videoId")
            if not vid:
                continue
            comments_data = await self._api("GET", "/commentThreads", params={
                "part": "snippet",
                "videoId": vid,
                "order": "relevance",
                "maxResults": 5,
            })
            if not comments_data:
                continue
            for ct in comments_data.get("items", []):
                cid = ct.get("id", "")
                if cid in replied:
                    continue
                s = ct.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
                text = s.get("textDisplay", "")
                author = s.get("authorDisplayName", "?")
                likes = int(s.get("likeCount", 0))
                if likes < 5 or len(text) < 15:
                    continue
                reply_text = "Спасибо за комментарий! ❤️ Подписывайся на канал!"
                await self._api("POST", "/comments", json={
                    "snippet": {
                        "parentId": ct.get("id"),
                        "textOriginal": reply_text,
                    },
                })
                replied.add(cid)
                log.info("YouTubeGrowth: replied to comment by %s under %s", author, vid)
                break
        self._state["replied_comments"] = list(replied)[-500:]
        self._save()

    def _parse_duration_seconds(self, duration: str) -> int:
        total = 0
        for part in duration.replace("PT", "").split("M"):
            for sub in part.split("H"):
                for s in sub.replace("S", "").split("S"):
                    if s.isdigit():
                        total += int(s)
        return total

    # ── Main loop ──
    async def _growth_loop(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)
        while not self.bot.is_closed():
            try:
                await self._check_shorts()
                await asyncio.sleep(10)
                await self._check_premieres()
                await asyncio.sleep(10)
                await self._bridge_top_comments()
                await asyncio.sleep(10)
                await self._post_analytics()
                await asyncio.sleep(10)
                await self._auto_reply_comments()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("YouTubeGrowth: error in main loop")
            await asyncio.sleep(self.config.get("growth_check_seconds", 600))

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.config.get("enabled", False):
            return
        self.bot.loop.create_task(self._growth_loop())
        self.bot.loop.create_task(self._stream_cta_loop())
        log.info("YouTubeGrowth: модуль запущен")

    async def cog_unload(self):
        if self._session and not self._session.closed:
            await self._session.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(YouTubeGrowth(bot))
