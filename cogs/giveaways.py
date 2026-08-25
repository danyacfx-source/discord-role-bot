import asyncio
import random
import time
import logging

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("giveaway")


class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: int):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id

    @discord.ui.button(label="Participate!", style=discord.ButtonStyle.success, emoji="\U0001f389", custom_id="giveaway_join")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("Giveaways")
        if cog is None:
            return
        ga = cog.active.get(self.giveaway_id)
        if ga is None:
            await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in ga["participants"]:
            ga["participants"].discard(uid)
            await interaction.response.send_message("You left the giveaway.", ephemeral=True)
        else:
            ga["participants"].add(uid)
            await interaction.response.send_message(
                f"You joined! ({len(ga['participants'])} participants)", ephemeral=True
            )
        try:
            await interaction.message.edit(embed=cog._build_embed(ga))
        except Exception:
            pass


class Giveaways(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active: dict[int, dict] = {}
        self._next_id = 1
        bot.add_view(GiveawayView(0))

    def _build_embed(self, ga: dict) -> discord.Embed:
        remaining = max(0, int(ga["end_time"] - time.time()))
        m, s = divmod(remaining, 60)
        h, m = divmod(m, 60)
        time_str = f"{h}h {m}m {s}s" if h else f"{m}m {s}s"
        e = discord.Embed(
            title=ga["title"],
            description=ga["description"] or "React with the button to enter!",
            color=discord.Color.gold(),
        )
        e.add_field(name="Prize", value=ga["prize"], inline=True)
        e.add_field(name="Participants", value=str(len(ga["participants"])), inline=True)
        e.add_field(name="Time left", value=time_str, inline=True)
        if ga.get("winner_count", 1) > 1:
            e.set_footer(text=f"{ga['winner_count']} winners will be chosen")
        return e

    @app_commands.command(name="giveaway", description="Start a giveaway")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        prize="What are you giving away?",
        duration="Duration in minutes",
        description="Optional description",
        winners="Number of winners (default 1)",
    )
    async def giveaway_cmd(
        self,
        interaction: discord.Interaction,
        prize: str,
        duration: int,
        description: str = "",
        winners: int = 1,
    ):
        if duration < 1 or duration > 10080:
            await interaction.response.send_message("Duration: 1-10080 minutes.", ephemeral=True)
            return
        if winners < 1 or winners > 20:
            winners = 1

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
            "participants": set(),
            "message_id": None,
            "author_id": interaction.user.id,
        }
        self.active[ga_id] = ga

        view = GiveawayView(ga_id)
        embed = self._build_embed(ga)
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        ga["message_id"] = msg.id

        asyncio.create_task(self._finish_giveaway(ga_id))

    async def _finish_giveaway(self, ga_id: int):
        ga = self.active.get(ga_id)
        if ga is None:
            return
        await asyncio.sleep(max(1, int(ga["end_time"] - time.time())))

        ga = self.active.pop(ga_id, None)
        if ga is None:
            return

        channel = self.bot.get_channel(ga["channel_id"])
        if channel is None:
            return

        participants = list(ga["participants"])
        count = min(ga["winner_count"], len(participants))

        if count == 0:
            embed = discord.Embed(
                title=ga["title"],
                description="No participants — giveaway cancelled.",
                color=discord.Color.red(),
            )
        else:
            winners_list = random.sample(participants, count)
            mentions = ", ".join(f"<@{uid}>" for uid in winners_list)
            embed = discord.Embed(
                title=f"Giveaway ended: {ga['prize']}",
                description=f"Winner(s): {mentions}\nCongratulations!",
                color=discord.Color.green(),
            )

        try:
            if ga.get("message_id"):
                msg = await channel.fetch_message(ga["message_id"])
                await msg.edit(embed=embed, view=None)
            else:
                await channel.send(embed=embed)
        except Exception:
            log.exception("Giveaway: failed to post results")


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaways(bot))
