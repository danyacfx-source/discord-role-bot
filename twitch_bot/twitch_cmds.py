import time

from db import counter_add, counter_get, counter_list
from config import CONFIG
from twitch_bot import overlay_state
from twitch_bot.question_queue import add as qadd, count as qcount, list_queue, pop_first, remove as qremove, clear as qclear
from twitch_bot.raid_state import end_raid, reset, start_raid, state as raid_state
from twitch_bot.tarkov_roulette import roulette
from twitch_bot.song_queue import SongQueue, extract_video_id

class CommandHandler:
    def __init__(self, config, bot):
        cfg = config.get("commands") or {}
        self.prefix = cfg.get("prefix", config.get("prefix", "!"))
        self.bot = bot
        self._cooldowns = {}
        self.commands = {}
        for cmd in cfg.get("list", []):
            name = str(cmd["name"]).lower().lstrip(self.prefix)
            self.commands[name] = cmd
        self.counter_names = set(cfg.get("counters", []))
        music_cfg = cfg.get("music") or {}
        self.songs = music_cfg.get("songs") or []
        self.votes = {}
        self._voted_users = {}
        self.song_queue = SongQueue()

    def _is_staff(self, author):
        return author.is_mod or author.is_broadcaster

    async def handle(self, message):
        content = (message.content or "").strip()
        if not content.startswith(self.prefix):
            return
        parts = content[len(self.prefix):].split(maxsplit=1)
        if not parts:
            return
        name = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""
        if name == "commands":
            await self._list_commands(message)
            return
        if name == "tod":
            await self._cmd_tod(message, args)
            return
        if name == "add":
            await self._cmd_add(message, args)
            return
        if name == "квест":
            await self._cmd_raid_quest(message)
            return
        if name == "карта":
            await self._cmd_loadout(message)
            return
        if name == "аллергия":
            await self._cmd_allergy(message)
            return
        if name == "рейд":
            await self._cmd_raid_start(message)
            return
        if name == "экст":
            await self._cmd_raid_end(message, survived=True)
            return
        if name == "дод":
            await self._cmd_raid_end(message, survived=False)
            return
        if name == "стат":
            await self._cmd_raid_status(message)
            return
        if name == "рейды":
            await self._cmd_raid_count(message)
            return
        if name == "музыка":
            await self._cmd_music(message)
            return
        if name == "vote":
            await self._cmd_vote(message, args)
            return
        if name == "итоги":
            await self._cmd_tally(message)
            return
        if name == "таймаут":
            await self._cmd_timeout(message, args)
            return
        if name == "банить":
            await self._cmd_ban(message, args)
            return
        if name == "разбанить":
            await self._cmd_unban(message, args)
            return
        if name == "вопрос":
            await self._cmd_question_add(message, args)
            return
        if name == "вопросы":
            await self._cmd_question_list(message)
            return
        if name == "открыть":
            await self._cmd_question_open(message)
            return
        if name == "убрать":
            await self._cmd_question_remove(message, args)
            return
        if name == "вопросы_чисто":
            await self._cmd_question_clear(message)
            return
        if name == "песня":
            await self._cmd_song_request(message, args)
            return
        if name == "skip":
            await self._cmd_song_skip(message)
            return
        if name == "queue":
            await self._cmd_song_queue(message)
            return
        if name == "clearqueue":
            await self._cmd_song_clear(message)
            return
        cmd = self.commands.get(name)
        if cmd is None:
            return
        author = message.author
        if cmd.get("mod_only") and not (author.is_mod or author.is_broadcaster):
            return
        key = (name, author.name.lower())
        now = time.time()
        last = self._cooldowns.get(key, 0)
        if now - last < cmd.get("cooldown", 0):
            return
        self._cooldowns[key] = now
        text = cmd.get("text", "")
        if args and "{args}" in text:
            text = text.replace("{args}", args)
        try:
            text = text.format(user=author.name, channel=message.channel.name)
        except (KeyError, IndexError):
            pass
        await message.channel.send(text)

    async def _list_commands(self, message):
        names = set(self.commands) | set(self.counter_names) | {"tod", "add", "квест", "карта", "аллергия", "музыка", "vote", "стат", "рейды", "вопрос"}
        listing = ", ".join(self.prefix + n for n in sorted(names))
        await message.channel.send(f"Доступные команды: {listing}")

    async def _cmd_tod(self, message, args):
        channel = message.channel.name
        if not args:
            value = counter_get(channel, "tod")
            await message.channel.send(f"💀 Смертей: {value}")
            return
        if not self._is_staff(message.author):
            await message.channel.send("Изменять счётчик могут только мод/стример.")
            return
        delta = self._parse_delta(args)
        if delta == 0:
            await message.channel.send("Пример: !tod +1 / !tod -1")
            return
        value = counter_add(channel, "tod", delta)
        await message.channel.send(f"💀 Смертей: {value} ({'+' if delta > 0 else ''}{delta})")

    async def _cmd_add(self, message, args):
        channel = message.channel.name
        if not args:
            rows = counter_list(channel)
            if not rows:
                await message.channel.send("Счётчики пусты. Пример: !add ledx")
                return
            text = " | ".join(f"{n}: {v}" for n, v in rows if n != "tod")
            await message.channel.send(f"📦 Лут: {text}")
            return
        parts = args.split(maxsplit=1)
        item = parts[0].lower()
        delta = 1
        if len(parts) > 1:
            parsed = self._parse_delta(parts[1])
            if parsed != 0 and self._is_staff(message.author):
                delta = parsed
        value = counter_add(channel, item, delta)
        sign = f" (+{delta})" if delta > 0 else f" ({delta})"
        await message.channel.send(f"📦 {item}: {value}{sign}")

    async def _cmd_raid_quest(self, message):
        if not roulette._check_cooldown(message.author, "квест", 15):
            return
        text = roulette.raid_quest()
        overlay_state.set("quest", text)
        await message.channel.send(text)

    async def _cmd_loadout(self, message):
        if not roulette._check_cooldown(message.author, "карта", 15):
            return
        text = roulette.loadout()
        overlay_state.set("map", text)
        await message.channel.send(text)

    async def _cmd_allergy(self, message):
        if not roulette._check_cooldown(message.author, "аллергия", 15):
            return
        text = roulette.allergy()
        overlay_state.set("allergy", text)
        await message.channel.send(text)

    async def _cmd_raid_start(self, message):
        if not self._is_staff(message.author):
            return
        start_raid()
        overlay_state.set("raid", "Рейд идёт!")
        await message.channel.send("🎮 Рейд начался! Удачи в рейде!")

    async def _cmd_raid_end(self, message, survived):
        if not self._is_staff(message.author):
            return
        end_raid(survived)
        st = raid_state()
        streak = st.get("streak", 0)
        best = st.get("best_streak", 0)
        if survived:
            overlay_state.set("raid", f"✅ Выжил! Серия: {streak}")
            text = f"✅ Рейд завершён — выжил! Серия выживаний: **{streak}**"
            if best:
                text += f" (рекорд: {best})"
        else:
            overlay_state.set("raid", "💀 Умер в рейде")
            text = f"💀 Рейд завершён — умер. Серия сброшена (рекорд: {best})"
        await message.channel.send(text)

    async def _cmd_raid_status(self, message):
        st = raid_state()
        status = st.get("status")
        if not status:
            await message.channel.send("Рейдов не было. !рейд чтобы начать")
            return
        streak = st.get("streak", 0)
        best = st.get("best_streak", 0)
        await message.channel.send(
            f"📊 Статус: {status} · Серия: {streak} · Рекорд: {best}"
        )

    async def _cmd_raid_count(self, message):
        st = raid_state()
        total = st.get("total_raids", 0)
        last_map = st.get("last_map")
        text = f"🎮 Всего рейдов: **{total}**"
        if last_map:
            text += f" · Последняя карта: {last_map}"
        await message.channel.send(text)

    async def _cmd_question_add(self, message, args):
        text = args.strip()
        if not text:
            await message.channel.send("Пример: !вопрос Зачем нужен хелкат?")
            return
        if len(text) > 280:
            await message.channel.send("Вопрос слишком длинный (макс. 280 символов).")
            return
        user = message.author.name
        key = ("вопрос", user.lower())
        now = time.time()
        last = self._cooldowns.get(key, 0)
        if now - last < 15:
            return
        self._cooldowns[key] = now
        total = qadd(user, text)
        await message.channel.send(f"❓ Вопрос принят! В очереди: {total}")

    async def _cmd_question_list(self, message):
        if not self._is_staff(message.author):
            return
        rows = list_queue()
        if not rows:
            await message.channel.send("Очередь вопросов пуста.")
            return
        lines = []
        for i, item in enumerate(rows, 1):
            lines.append(f"{i}. {item['text']} — {item['user']}")
        text = "\n".join(lines)
        if len(text) > 1400:
            text = text[:1400] + "\n…"
        await message.channel.send(f"📋 Вопросы ({len(rows)}):\n{text}")

    async def _cmd_question_open(self, message):
        if not self._is_staff(message.author):
            return
        item = pop_first()
        if item is None:
            await message.channel.send("Очередь вопросов пуста.")
            return
        await message.channel.send(
            f"🎤 Вопрос от {item['user']}: «{item['text']}»\nОсталось: {qcount()}"
        )

    async def _cmd_question_remove(self, message, args):
        if not self._is_staff(message.author):
            return
        try:
            idx = int(args.strip()) - 1
        except (ValueError, TypeError):
            await message.channel.send("Пример: !убрать 2")
            return
        item = qremove(idx)
        if item is None:
            await message.channel.send("Такого номера нет.")
            return
        await message.channel.send(f"🗑️ Удалён вопрос «{item['text']}» ({item['user']})")

    async def _cmd_question_clear(self, message):
        if not self._is_staff(message.author):
            return
        qclear()
        await message.channel.send("🧹 Очередь вопросов очищена.")

    async def _cmd_music(self, message):
        if not self.songs:
            await message.channel.send("Список музыки пуст.")
            return
        lines = "\n".join(f"{i}. {s}" for i, s in enumerate(self.songs, 1))
        await message.channel.send(f"🎵 Музыка для голосования:\n{lines}\nГолосуй: !vote <номер>")

    async def _cmd_vote(self, message, args):
        if not self.songs:
            return
        try:
            idx = int(args.strip())
        except ValueError:
            await message.channel.send("Пример: !vote 3")
            return
        if idx < 1 or idx > len(self.songs):
            await message.channel.send(
                f"Номер от 1 до {len(self.songs)}. Список: !музыка"
            )
            return
        user = message.author.name.lower()
        if user in self._voted_users:
            await message.channel.send(
                f"{message.author.name}, ты уже голосовал. !музыка чтобы посмотреть"
            )
            return
        self._voted_users[user] = idx
        self.votes[idx] = self.votes.get(idx, 0) + 1
        await message.channel.send(
            f"🗳️ {message.author.name} голосует за: {self.songs[idx-1]}"
        )

    async def _cmd_tally(self, message):
        if not self._is_staff(message.author):
            await message.channel.send("Итоги может показать только мод/стример.")
            return
        if not self.votes:
            await message.channel.send("Голосов пока нет.")
            return
        ranking = sorted(self.votes.items(), key=lambda kv: kv[1], reverse=True)
        top = ranking[0][0]
        lines = "\n".join(
            f"{i}. {self.songs[idx-1]} — {v}" for i, (idx, v) in enumerate(ranking, 1)
        )
        await message.channel.send(
            f"🎶 Итоги голосования:\n{lines}\n\nПобедитель: {self.songs[top-1]} 🏆"
        )
        self.votes = {}
        self._voted_users = {}

    async def _cmd_timeout(self, message, args):
        if not self._is_staff(message.author):
            return
        parts = args.split()
        if len(parts) < 2:
            await message.channel.send("Пример: !таймаут ник 60 причина")
            return
        user, duration = parts[0], parts[1]
        reason = " ".join(parts[2:])
        ok = await self.bot.moderation.timeout_user(user, duration, reason)
        if ok:
            await message.channel.send(
                f"⏱️ {user} — тайм-аут {duration} сек" + (f" ({reason})" if reason else "")
            )

    async def _cmd_ban(self, message, args):
        if not self._is_staff(message.author):
            return
        parts = args.split()
        if not parts:
            await message.channel.send("Пример: !банить ник причина")
            return
        user = parts[0]
        reason = " ".join(parts[1:])
        ok = await self.bot.moderation.ban_user(user, reason)
        if ok:
            await message.channel.send(
                f"🚫 {user} забанен" + (f" ({reason})" if reason else "")
            )

    async def _cmd_unban(self, message, args):
        if not self._is_staff(message.author):
            return
        user = args.strip()
        if not user:
            await message.channel.send("Пример: !разбанить ник")
            return
        ok = await self.bot.moderation.unban_user(user)
        if ok:
            await message.channel.send(f"✅ {user} разбанен")

    @staticmethod
    def _parse_delta(raw: str) -> int:
        raw = raw.strip()
        if raw in ("+", "-"):
            return 0
        try:
            return int(raw)
        except ValueError:
            return 0

    async def _cmd_song_request(self, message, args):
        url = args.strip()
        if not url:
            await message.channel.send("Пример: !Песня https://youtube.com/watch?v=...")
            return
        video_id = extract_video_id(url)
        if not video_id:
            await message.channel.send("Не удалось распознать ссылку на YouTube.")
            return
        full_url = f"https://www.youtube.com/watch?v={video_id}"
        title = f"YouTube ({video_id})"
        requester = message.author.name
        self.song_queue.add(full_url, title, requester)
        pos = self.song_queue.length()
        await message.channel.send(
            f"🎵 {requester} добавил трек в очередь! Позиция: {pos}"
        )
        try:
            owner_id = CONFIG.get("owner_id")
            if owner_id:
                owner = await self.bot.fetch_user(owner_id)
                await owner.send(f"🎵 {requester} заказал трек: {full_url}")
        except Exception:
            pass

    async def _cmd_song_skip(self, message):
        if not self._is_staff(message.author):
            return
        track = self.song_queue.skip()
        if track:
            await message.channel.send(f"⏭️ Пропущен: {track['title']}")
        else:
            await message.channel.send("Очередь пуста.")

    async def _cmd_song_queue(self, message):
        queue = self.song_queue.get_queue()
        current = self.song_queue.get_current()
        if not queue and not current:
            await message.channel.send("🎵 Очередь пуста.")
            return
        lines = []
        if current:
            lines.append(f"▶️ Сейчас: {current['title']} (заказал: {current['requester']})")
        for i, track in enumerate(queue[:5], 1):
            lines.append(f"{i}. {track['title']} ({track['requester']})")
        if len(queue) > 5:
            lines.append(f"... и ещё {len(queue) - 5}")
        await message.channel.send("\n".join(lines))

    async def _cmd_song_clear(self, message):
        if not self._is_staff(message.author):
            return
        self.song_queue.clear()
        await message.channel.send("🧹 Очередь песен очищена.")
