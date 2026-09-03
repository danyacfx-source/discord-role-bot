import asyncio
import json
import random
import time
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db import (
    giveaway_set_participants,
    giveaway_save,
    giveaway_next_id,
    giveaways_find_by_message,
    giveaways_load_active,
)

log = logging.getLogger("giveaway")


class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: int):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        button = discord.ui.Button(
            label="Участвовать!",
            style=discord.ButtonStyle.success,
            emoji="🎉",
            custom_id=f"giveaway_{giveaway_id}",
        )
        button.callback = self.join_button
        self.add_item(button)

    async def join_button(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Giveaways")
        if cog is None:
            return
        ga = cog.active.get(self.giveaway_id)
        if ga is None:
            await interaction.response.send_message("Розыгрыш уже завершён.", ephemeral=True)
            return
        uid = interaction.user.id
        member = interaction.guild.get_member(uid)

        if ga.get("min_days"):
            days = self._member_days(member)
            if days < ga["min_days"]:
                await interaction.response.send_message(
                    f"Чтобы участвовать, нужно быть на сервере минимум **{ga['min_days']} дн.** "
                    f"(ты здесь {days} дн.).",
                    ephemeral=True,
                )
                return

        async with cog._participants_lock:
            if uid in ga["participants"]:
                ga["participants"].discard(uid)
                await interaction.response.send_message("Ты покинул розыгрыш.", ephemeral=True)
            else:
                ga["participants"].add(uid)
                await interaction.response.send_message(
                    f"Ты участвуешь! ({len(ga['participants'])} участ.)", ephemeral=True
                )
            await self._persist(cog, ga)
        try:
            await interaction.message.edit(embed=cog._build_embed(ga))
        except Exception:
            pass

    @staticmethod
    def _member_days(member) -> int:
        if member is None or member.joined_at is None:
            return 0
        return (datetime.now(timezone.utc) - member.joined_at).days

    @staticmethod
    async def _persist(cog, ga: dict):
        ga["participants_json"] = json.dumps(sorted(ga["participants"]))
        await asyncio.get_running_loop().run_in_executor(None, giveaway_save, dict(ga))


class Giveaways(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active: dict[int, dict] = {}
        self._next_id = 1
        self._started = False
        # Только когда кнопку нажимают несколько раз одновременно, изменение
        # участников сериализуется блокировкой, иначе возможны гонки при
        # чтении/записи множества участников (дефект D24).
        self._participants_lock = asyncio.Lock()
        # Храним ссылки на фоновые задачи завершения розыгрышей, иначе сборщик
        # мусора может удалить их до срабатывания (дефект D08).
        self._tasks: set[asyncio.Task] = set()

    @commands.Cog.listener()
    async def on_ready(self):
        if self._started:
            return
        self._started = True
        # Счётчик ID берём из БД, а не только из активных розыгрышей — иначе
        # после перезапуска без активных он сбросится в 1 и перезапишет историю
        # завершённых розыгрышей (дефект D08).
        self._next_id = giveaway_next_id() + 1
        for ga in giveaways_load_active():
            ga_id = ga["id"]
            self._next_id = max(self._next_id, ga_id + 1)
            try:
                ga["participants"] = set(json.loads(ga["participants"]))
            except (TypeError, ValueError):
                ga["participants"] = set()
            self.active[ga_id] = ga
            self.bot.add_view(GiveawayView(ga_id))
            self._spawn_finish(ga_id)
            log.info("Giveaway: восстановлен розыгрыш #%s («%s»)", ga_id, ga["prize"])

    def _spawn_finish(self, ga_id: int):
        task = asyncio.create_task(self._finish_giveaway(ga_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _build_embed(self, ga: dict) -> discord.Embed:
        remaining = max(0, int(ga["end_time"] - time.time()))
        m, s = divmod(remaining, 60)
        h, m = divmod(m, 60)
        time_str = f"{h}ч {m}м {s}с" if h else f"{m}м {s}с"
        e = discord.Embed(
            title=ga["title"],
            description=ga["description"] or "Нажми кнопку, чтобы участвовать!",
            color=discord.Color.gold(),
        )
        e.add_field(name="Приз", value=ga["prize"], inline=True)
        e.add_field(name="Участников", value=str(len(ga["participants"])), inline=True)
        e.add_field(name="Осталось", value=time_str, inline=True)
        if ga.get("min_days"):
            e.add_field(name="Условие", value=f"От {ga['min_days']} дн. на сервере", inline=False)
        if ga.get("winner_count", 1) > 1:
            e.set_footer(text=f"Победителей: {ga['winner_count']}")
        return e

    @app_commands.command(name="giveaway", description="Запустить розыгрыш (только для админов)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        prize="Что разыгрываем?",
        duration="Длительность в минутах (1-10080)",
        description="Описание (необязательно)",
        winners="Количество победителей (по умолчанию 1)",
        min_days="Мин. дней на сервере для участия (по умолчанию 0)",
    )
    async def giveaway_cmd(
        self,
        interaction: discord.Interaction,
        prize: str,
        duration: int,
        description: str = "",
        winners: int = 1,
        min_days: int = 0,
    ):
        if duration < 1 or duration > 10080:
            await interaction.response.send_message("Длительность: 1-10080 минут.", ephemeral=True)
            return
        if winners < 1 or winners > 20:
            winners = 1
        if not description:
            description = ""

        ga_id = self._next_id
        self._next_id += 1
        end_time = time.time() + duration * 60

        ga = {
            "id": ga_id,
            "title": f"Giveaway: {prize}",
            "prize": prize,
            "description": description,
            "winner_count": winners,
            "end_time": end_time,
            "channel_id": interaction.channel_id,
            "guild_id": interaction.guild_id,
            "message_id": None,
            "author_id": interaction.user.id,
            "min_days": max(0, min_days),
            "participants": set(),
            "participants_json": "[]",
            "status": "active",
        }
        self.active[ga_id] = ga

        view = GiveawayView(ga_id)
        embed = self._build_embed(ga)
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        ga["message_id"] = msg.id
        ga["participants_json"] = json.dumps([])
        await asyncio.get_running_loop().run_in_executor(None, giveaway_save, dict(ga))

        self._spawn_finish(ga_id)

    @app_commands.command(name="reroll", description="Перевыбрать победителя розыгрыша (только для админов)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        message_link="Ссылка на сообщение розыгрыша",
        winners="Сколько победителей выбрать",
    )
    async def reroll_cmd(
        self,
        interaction: discord.Interaction,
        message_link: str,
        winners: int = 1,
    ):
        message_id = self._extract_message_id(message_link)
        if not message_id:
            await interaction.response.send_message(
                "Не удалось распознать ссылку на сообщение.", ephemeral=True
            )
            return
        ga = await asyncio.get_running_loop().run_in_executor(
            None, giveaways_find_by_message, message_id
        )
        if ga is None or ga["status"] != "finished":
            await interaction.response.send_message(
                "Завершённый розыгрыш с таким сообщением не найден.", ephemeral=True
            )
            return
        try:
            participants = json.loads(ga["participants"])
        except (TypeError, ValueError):
            participants = []
        if not participants:
            await interaction.response.send_message(
                "В этом розыгрыше не было участников.", ephemeral=True
            )
            return
        count = min(max(1, winners), len(participants))
        winners_list = random.sample(participants, count)
        mentions = ", ".join(f"<@{uid}>" for uid in winners_list)
        embed = discord.Embed(
            title=f"Реролл: {ga['prize']}",
            description=f"Новый победитель: {mentions}\nПоздравляем! 🎉",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @staticmethod
    def _extract_message_id(link: str) -> int | None:
        if link.isdigit():
            return int(link)
        try:
            return int(link.split("/")[-1])
        except (IndexError, ValueError):
            return None

    async def _finish_giveaway(self, ga_id: int):
        ga = self.active.get(ga_id)
        if ga is None:
            return
        await asyncio.sleep(max(1, int(ga["end_time"] - time.time())))

        ga = self.active.pop(ga_id, None)
        if ga is None:
            return

        participants = list(ga["participants"])
        count = min(ga["winner_count"], len(participants))

        if count == 0:
            embed = discord.Embed(
                title=ga["title"],
                description="Не было участников — розыгрыш отменён.",
                color=discord.Color.red(),
            )
        else:
            winners_list = random.sample(participants, count)
            mentions = ", ".join(f"<@{uid}>" for uid in winners_list)
            embed = discord.Embed(
                title=f"Розыгрыш завершён: {ga['prize']}",
                description=f"Победитель: {mentions}\nПоздравляем! 🎉",
                color=discord.Color.green(),
            )

        try:
            ga["participants_json"] = json.dumps(sorted(participants))
            ga["status"] = "finished"
            await asyncio.get_running_loop().run_in_executor(
                None, giveaway_save, dict(ga)
            )
        except Exception:
            log.exception("Giveaway: не удалось сохранить итог")

        channel = self.bot.get_channel(ga["channel_id"])
        if channel is None:
            return
        try:
            if ga.get("message_id"):
                msg = await channel.fetch_message(ga["message_id"])
                await msg.edit(embed=embed, view=None)
            else:
                await channel.send(embed=embed)
        except Exception:
            log.exception("Giveaway: не удалось опубликовать результаты")


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaways(bot))