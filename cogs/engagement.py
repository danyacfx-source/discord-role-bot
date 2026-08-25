import asyncio
import json
import random
import time
import logging
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands, tasks
from config import CONFIG

log = logging.getLogger("engagement")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

LOYALTY_FILE = DATA_DIR / "loyalty.json"
STREAK_FILE = DATA_DIR / "streaks.json"


def _load_json(path):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_json(path, data):
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


class Engagement(commands.Cog):
    """Twitch role sync, loyalty points, minigames, polls, streaks."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.loyalty: dict = _load_json(LOYALTY_FILE)
        self.streaks: dict = _load_json(STREAK_FILE)
        self._polls: dict = {}
        self._sub_cache: set[int] = set()

    def _save_loyalty(self):
        _save_json(LOYALTY_FILE, self.loyalty)

    def _save_streaks(self):
        _save_json(STREAK_FILE, self.streaks)

    def _points_key(self, user: str, channel: str) -> str:
        return f"{channel}:{user}"

    def add_points(self, user: str, channel: str, amount: int, reason: str = ""):
        key = self._points_key(user, channel)
        entry = self.loyalty.setdefault(key, {"points": 0, "total": 0, "user": user, "channel": channel})
        entry["points"] += amount
        entry["total"] += amount
        entry["last_active"] = time.time()
        if reason:
            entry["last_reason"] = reason
        self._save_loyalty()

    def get_points(self, user: str, channel: str) -> int:
        return self.loyalty.get(self._points_key(user, channel), {}).get("points", 0)

    def spend_points(self, user: str, channel: str, amount: int) -> bool:
        key = self._points_key(user, channel)
        entry = self.loyalty.get(key)
        if not entry or entry["points"] < amount:
            return False
        entry["points"] -= amount
        self._save_loyalty()
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not hasattr(self.bot, "twitch_client") or self.bot.twitch_client is None:
            return
        tc = self.bot.twitch_client
        if not hasattr(tc, "channels_map") or not tc.channels_map:
            return
        for ch_name in tc.channels_map:
            self.add_points(message.author.name.lower(), ch_name, 1, "chat")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        pass

    async def _check_twitch_subs(self):
        cfg = CONFIG.get("twitch") or {}
        client_id = cfg.get("live", {}).get("client_id", "")
        client_secret = cfg.get("live", {}).get("client_secret", "")
        channel_name = cfg.get("channel", "")
        if not client_id or not client_secret or not channel_name:
            return set()
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                token_resp = await session.post("https://id.twitch.tv/oauth2/token", params={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "client_credentials",
                })
                token_data = await token_resp.json()
                app_token = token_data.get("access_token")
                if not app_token:
                    return set()

                users_resp = await session.get("https://api.twitch.tv/helix/users", params={
                    "login": channel_name,
                }, headers={"Client-Id": client_id, "Authorization": f"Bearer {app_token}"})
                users_data = await users_resp.json()
                broadcaster_id = (users_data.get("data") or [{}])[0].get("id")
                if not broadcaster_id:
                    return set()

                subs_resp = await session.get("https://api.twitch.tv/helix/subscriptions", params={
                    "broadcaster_id": broadcaster_id,
                    "first": 100,
                }, headers={"Client-Id": client_id, "Authorization": f"Bearer {app_token}"})
                subs_data = await subs_resp.json()
                sub_logins = set()
                for s in subs_data.get("data", []):
                    sub_logins.add(s.get("user_login", "").lower())
                return sub_logins
        except Exception:
            log.exception("Engagement: ошибка проверки подписчиков Twitch")
            return set()

    @tasks.loop(minutes=15)
    async def role_sync_loop(self):
        guild_id = CONFIG.get("guild_id", 0)
        if not guild_id:
            return
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        role_id = CONFIG.get("twitch", {}).get("sub_role_id", 0)
        if not role_id:
            return
        role = guild.get_role(int(role_id))
        if not role:
            return

        sub_logins = await self._check_twitch_subs()
        self._sub_cache = sub_logins

        for member in guild.members:
            if member.bot:
                continue
            name = member.name.lower()
            has_sub_role = role in member.roles
            is_sub = name in sub_logins
            if is_sub and not has_sub_role:
                try:
                    await member.add_roles(role, reason="Twitch subscriber")
                except discord.Forbidden:
                    pass
            elif not is_sub and has_sub_role:
                try:
                    await member.remove_roles(role, reason="No longer Twitch subscriber")
                except discord.Forbidden:
                    pass

    @role_sync_loop.before_loop
    async def before_role_sync(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.role_sync_loop.is_running():
            self.role_sync_loop.start()


class TwitchChatGames(commands.Cog):
    """Minigames, polls, streaks in Twitch chat via twitchio."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._active_polls: dict = {}
        self._cooldowns: dict = {}

    def _check_cooldown(self, user: str, cmd: str, seconds: int = 10) -> bool:
        key = f"{user}:{cmd}"
        now = time.time()
        if now - self._cooldowns.get(key, 0) < seconds:
            return False
        self._cooldowns[key] = now
        return True

    async def handle_command(self, user: str, text: str, channel):
        parts = text.strip().split(None, 1)
        if not parts:
            return
        cmd = parts[0].lower().lstrip("!")
        args = parts[1] if len(parts) > 1 else ""

        tc = self.bot.twitch_client
        if tc is None:
            return
        engagement = self.bot.get_cog("Engagement")

        if cmd == "монетка" or cmd == "coin":
            await self._coinflip(user, channel, engagement)
        elif cmd == "рулетка" or cmd == "roulette":
            await self._roulette(user, args, channel, engagement)
        elif cmd == "баланс" or cmd == "balance":
            await self._balance(user, channel, engagement)
        elif cmd == "деньги" or cmd == "pay":
            await self._daily(user, channel, engagement)
        elif cmd == "голосование" or cmd == "poll":
            await self._poll_start(user, args, channel)
        elif cmd == "голос" or cmd == "vote":
            await self._poll_vote(user, args, channel)
        elif cmd == "итоги" or cmd == "results":
            await self._poll_results(user, channel)
        elif cmd == "предсказание" or cmd == "predict":
            await self._predict(user, args, channel, engagement)
        elif cmd == "стрик" or cmd == "streak":
            await self._streak(user, channel)

    async def _coinflip(self, user, channel, engagement):
        if not self._check_cooldown(user, "coin", 5):
            return
        result = random.choice(["Орёл", "Решка"])
        win = random.choice([True, False])
        if win and engagement:
            engagement.add_points(user, channel.name, 5, "coinflip win")
            await channel.send(f"🎰 {user}: {result} — ты выиграл 5 очков!")
        else:
            await channel.send(f"🎰 {user}: {result}")

    async def _roulette(self, user, args, channel, engagement):
        if not self._check_cooldown(user, "roulette", 10):
            return
        bet = 10
        if args:
            try:
                bet = max(1, min(1000, int(args)))
            except ValueError:
                bet = 10
        if engagement and engagement.get_points(user, channel.name) < bet:
            await channel.send(f"❌ {user}: недостаточно очков (нужно {bet})")
            return
        multiplier = random.choice([0, 0, 0, 2, 3, 5])
        if multiplier == 0:
            if engagement:
                engagement.spend_points(user, channel.name, bet)
            await channel.send(f"🎰 {user}: выпало 0 — ты потерял {bet} очков!")
        else:
            win = bet * multiplier
            if engagement:
                engagement.spend_points(user, channel.name, bet)
                engagement.add_points(user, channel.name, win, "roulette win")
            await channel.send(f"🎰 {user}: x{multiplier}! Выиграно {win} очков!")

    async def _balance(self, user, channel, engagement):
        if not self._check_cooldown(user, "bal", 5):
            return
        pts = engagement.get_points(user, channel.name) if engagement else 0
        await channel.send(f"💰 {user}: {pts} очков")

    async def _daily(self, user, channel, engagement):
        if not self._check_cooldown(user, "daily", 86400):
            await channel.send(f"⏳ {user}: уже получил сегодня. Приходи завтра!")
            return
        if engagement:
            engagement.add_points(user, channel.name, 50, "daily")
        await channel.send(f"🎁 {user}: +50 очков ежедневная награда!")

    async def _poll_start(self, user, args, channel):
        if not args:
            return
        parts = [p.strip() for p in args.split("|")]
        if len(parts) < 3:
            await channel.send("Формат: !голосование Вопрос | Вариант1 | Вариант2 | ...")
            return
        question = parts[0]
        options = parts[1:]
        self._active_polls[channel.name] = {
            "question": question,
            "options": {i: {"text": t, "votes": set()} for i, t in enumerate(options)},
            "author": user,
        }
        text = " | ".join(f"{i+1}) {t}" for i, t in enumerate(options))
        await channel.send(f"📊 Голосование: {question}\n{text}\nГолосуй: !голос <номер>")

    async def _poll_vote(self, user, args, channel):
        poll = self._active_polls.get(channel.name)
        if not poll:
            await channel.send("Нет активного голосования. Начни: !голосование")
            return
        try:
            choice = int(args) - 1
        except (ValueError, TypeError):
            await channel.send("Используй: !голос <номер варианта>")
            return
        if choice not in poll["options"]:
            await channel.send("Неверный номер варианта")
            return
        for opt in poll["options"].values():
            opt["votes"].discard(user)
        poll["options"][choice]["votes"].add(user)
        total = sum(len(o["votes"]) for o in poll["options"].values())
        await channel.send(f"✅ {user} проголосовал за вариант {choice+1} ({total} голосов)")

    async def _poll_results(self, user, channel):
        poll = self._active_polls.get(channel.name)
        if not poll:
            await channel.send("Нет активного голосования")
            return
        lines = [f"📊 {poll['question']}: "]
        for i, opt in poll["options"].items():
            v = len(opt["votes"])
            lines.append(f"  {i+1}) {opt['text']}: {v} голосов")
        await channel.send(" | ".join(lines))

    async def _predict(self, user, args, channel, engagement):
        if not self._check_cooldown(user, "predict", 30):
            return
        if engagement:
            engagement.add_points(user, channel.name, 10, "prediction")
        outcomes = ["Да", "Нет", "Может быть"]
        choice = random.choice(outcomes)
        await channel.send(f"🔮 {user}: {choice}")

    async def _streak(self, user, channel):
        engagement = self.bot.get_cog("Engagement")
        if not engagement:
            return
        today = int(time.time() // 86400)
        streak_data = engagement.streaks
        key = f"{channel.name}:{user}"
        entry = streak_data.setdefault(key, {"days": 0, "last_day": 0})
        if entry["last_day"] == today:
            await channel.send(f"🔥 {user}: стрик {entry['days']} дней (уже отмечен сегодня)")
            return
        if entry["last_day"] == today - 1:
            entry["days"] += 1
        else:
            entry["days"] = 1
        entry["last_day"] = today
        engagement._save_streaks()

        if entry["days"] >= 7 and entry["days"] % 7 == 0:
            engagement.add_points(user, channel.name, 100, "streak milestone")
            await channel.send(f"🔥 {user}: стрик {entry['days']} дней! +100 очков бонус!")
        else:
            await channel.send(f"🔥 {user}: стрик {entry['days']} дней подряд!")


async def setup(bot: commands.Bot):
    await bot.add_cog(Engagement(bot))
    await bot.add_cog(TwitchChatGames(bot))
