import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import CONFIG, DATA_DIR

log = logging.getLogger("schedule")

SCHEDULE_FILE = DATA_DIR / "schedule.json"

MSK = timezone(timedelta(hours=3))

WEEKDAY_RU = {
    0: "пн", 1: "вт", 2: "ср", 3: "чт",
    4: "пт", 5: "сб", 6: "вс",
}
WEEKDAY_RU_TO_NUM = {v: k for k, v in WEEKDAY_RU.items()}
DAY_NAMES_RU = {
    0: "Понедельник", 1: "Вторник", 2: "Среда",
    3: "Четверг", 4: "Пятница", 5: "Суббота", 6: "Воскресенье",
}


def _load():
    if SCHEDULE_FILE.exists():
        try:
            return json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
        except Exception:
            log.exception("Ошибка чтения %s", SCHEDULE_FILE)
    return {"entries": [], "reminder_minutes": 30, "notified": {}}


def _save(data):
    try:
        SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SCHEDULE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SCHEDULE_FILE)
    except Exception:
        log.exception("Ошибка записи %s", SCHEDULE_FILE)


def _next_occurrence(entry, now_msk):
    """Возвращает ближайшее время начала стрима (MSK) от now_msk."""
    hour, minute = entry["hour"], entry["minute"]
    target = now_msk.replace(hour=hour, minute=minute, second=0, microsecond=0)
    for _ in range(8):
        if target.weekday() in entry["days"] and target > now_msk:
            return target
        target += timedelta(days=1)
    return None


def _parse_days(text):
    """Парсит строки типа 'пн,ср,пт' или 'пн-ср' или 'пн пт вс'."""
    text = text.lower().replace(" ", "")
    parts = text.replace(",", "-").split("-")
    days = set()
    for p in parts:
        p = p.strip()
        if p in WEEKDAY_RU_TO_NUM:
            days.add(WEEKDAY_RU_TO_NUM[p])
        else:
            for short, num in WEEKDAY_RU_TO_NUM.items():
                if short.startswith(p[:2]):
                    days.add(num)
                    break
    return sorted(days)


class Schedule(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cfg = CONFIG.get("schedule") or {}
        self.data = _load()
        self._last_check_day = None

    async def cog_load(self):
        if not self.cfg.get("enabled", True):
            log.info("Schedule: модуль отключён")
            return
        self.check_reminders.start()

    async def cog_unload(self):
        if self.check_reminders.is_running():
            self.check_reminders.cancel()

    @tasks.loop(seconds=60)
    async def check_reminders(self):
        try:
            await self._check_once()
        except Exception:
            log.exception("Schedule: ошибка цикла")

    @check_reminders.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    async def _check_once(self):
        now_msk = datetime.now(MSK)
        today_key = now_msk.strftime("%Y-%m-%d")
        channel_id = self.cfg.get("channel_id")
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return

        reminder_min = self.data.get("reminder_minutes", 30)
        notified_today = self.data.get("notified", {}).get(today_key, [])
        new_notified = list(notified_today)
        changed = False

        for entry in self.data.get("entries", []):
            entry_id = entry.get("id", 0)
            if entry_id in notified_today:
                continue
            nxt = _next_occurrence(entry, now_msk)
            if nxt is None:
                continue
            delta = (nxt - now_msk).total_seconds() / 60
            if 0 < delta <= reminder_min:
                await self._send_reminder(channel, entry, nxt, now_msk)
                new_notified.append(entry_id)
                changed = True

        if changed:
            if "notified" not in self.data:
                self.data["notified"] = {}
            self.data["notified"][today_key] = new_notified
            _save(self.data)

    async def _send_reminder(self, channel, entry, start_msk, now_msk):
        title = entry.get("title") or "Стрим"
        duration = entry.get("duration_minutes", 120)
        hour = start_msk.hour
        minute = start_msk.minute
        time_str = f"{hour:02d}:{minute:02d}"
        days = ", ".join(DAY_NAMES_RU[d] for d in entry.get("days", []))

        embed = discord.Embed(
            title=f"🎬 Скоро стрим!",
            description=f"**{title}** начинается в **{time_str} МСК**",
            color=0x53FC18,
        )
        embed.add_field(name="День", value=days, inline=True)
        embed.add_field(name="Время", value=f"{time_str} МСК", inline=True)
        embed.add_field(name="Длительность", value=f"~{duration} мин", inline=True)

        delta_min = int((start_msk - now_msk).total_seconds() / 60)
        embed.set_footer(text=f"Начало через {delta_min} мин · kick.com/dendosich")

        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(
            label="Открыть Kick",
            url="https://kick.com/dendosich",
            style=discord.ButtonStyle.link,
        ))

        try:
            await channel.send(embed=embed, view=view)
            log.info("Schedule: напоминание '%s' в %s", title, time_str)
        except Exception:
            log.exception("Schedule: ошибка отправки напоминания")

    # --- Команды ---

    @app_commands.command(name="schedule", description="Расписание стримов")
    @app_commands.describe(
        action="Что сделать",
        days="Дни недели: пн,ср,пт",
        time="Время старта (ЧЧ:ММ, МСК)",
        title="Название стрима",
        duration="Длительность в минутах",
        entry_id="ID записи (для удаления)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Показать расписание", value="view"),
            app_commands.Choice(name="Добавить стрим", value="add"),
            app_commands.Choice(name="Удалить стрим", value="remove"),
            app_commands.Choice(name="Очистить всё", value="clear"),
        ],
    )
    async def schedule_cmd(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        days: str = None,
        time: str = None,
        title: str = None,
        duration: int = None,
        entry_id: int = None,
    ):
        if action.value == "view":
            await self._cmd_view(interaction)
        elif action.value == "add":
            await self._cmd_add(interaction, days, time, title, duration)
        elif action.value == "remove":
            await self._cmd_remove(interaction, entry_id)
        elif action.value == "clear":
            await self._cmd_clear(interaction)

    async def _cmd_view(self, interaction: discord.Interaction):
        entries = self.data.get("entries", [])
        if not entries:
            await interaction.response.send_message(
                "Расписание пустое. Добавь стрим: `/schedule add`", ephemeral=True
            )
            return
        now_msk = datetime.now(MSK)
        lines = []
        for e in sorted(entries, key=lambda x: (x.get("hour", 0), x.get("minute", 0))):
            days_str = " ".join(WEEKDAY_RU[d] for d in e.get("days", []))
            time_str = f"{e['hour']:02d}:{e['minute']:02d}"
            nxt = _next_occurrence(e, now_msk)
            nxt_str = ""
            if nxt:
                delta = int((nxt - now_msk).total_seconds() / 60)
                if delta < 60:
                    nxt_str = f" — через {delta} мин"
                elif delta < 1440:
                    nxt_str = f" — через {delta // 60}ч {delta % 60}м"
                else:
                    nxt_str = f" — {nxt.strftime('%d.%m')}"
            title = e.get("title") or "Стрим"
            lines.append(f"**#{e['id']}** `{days_str}` {time_str} МСК — {title}{nxt_str}")

        embed = discord.Embed(
            title="📅 Расписание стримов",
            description="\n".join(lines),
            color=0x53FC18,
        )
        reminder = self.data.get("reminder_minutes", 30)
        embed.set_footer(text=f"Напоминание за {reminder} мин до старта")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _cmd_add(self, interaction, days, time, title, duration):
        if not days or not time:
            await interaction.response.send_message(
                "Укажи **days** (пн,ср,пт) и **time** (20:00). "
                "Пример: `/schedule add days=пн,ср,пт time=20:00 title=Tarkov`",
                ephemeral=True,
            )
            return

        parsed_days = _parse_days(days)
        if not parsed_days:
            await interaction.response.send_message(
                "Не удалось распознать дни. Используй: пн, вт, ср, чт, пт, сб, вс",
                ephemeral=True,
            )
            return

        time = time.strip()
        if ":" not in time:
            await interaction.response.send_message(
                "Формат времени: ЧЧ:ММ (например, 20:00)", ephemeral=True
            )
            return
        try:
            hour, minute = map(int, time.split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "Неверное время. Формат: ЧЧ:ММ (например, 20:00)", ephemeral=True
            )
            return

        entries = self.data.get("entries", [])
        next_id = max((e.get("id", 0) for e in entries), default=0) + 1

        entry = {
            "id": next_id,
            "days": parsed_days,
            "hour": hour,
            "minute": minute,
            "title": title or "Стрим",
            "duration_minutes": duration or 120,
        }
        entries.append(entry)
        self.data["entries"] = entries
        _save(self.data)

        days_str = ", ".join(DAY_NAMES_RU[d] for d in parsed_days)
        await interaction.response.send_message(
            f"✅ Стрим #{next_id} добавлен:\n"
            f"📅 {days_str}\n"
            f"🕐 {hour:02d}:{minute:02d} МСК\n"
            f"🎮 {entry['title']}\n"
            f"⏱ {entry['duration_minutes']} мин",
            ephemeral=True,
        )

    async def _cmd_remove(self, interaction, entry_id):
        if entry_id is None:
            await interaction.response.send_message(
                "Укажи ID записи. Посмотри: `/schedule view`", ephemeral=True
            )
            return
        entries = self.data.get("entries", [])
        before = len(entries)
        entries = [e for e in entries if e.get("id") != entry_id]
        if len(entries) == before:
            await interaction.response.send_message(
                f"Запись #{entry_id} не найдена.", ephemeral=True
            )
            return
        self.data["entries"] = entries
        _save(self.data)
        await interaction.response.send_message(
            f"✅ Стрим #{entry_id} удалён.", ephemeral=True
        )

    async def _cmd_clear(self, interaction):
        self.data["entries"] = []
        self.data["notified"] = {}
        _save(self.data)
        await interaction.response.send_message(
            "✅ Расписание очищено.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Schedule(bot))
