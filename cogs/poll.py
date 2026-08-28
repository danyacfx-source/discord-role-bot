import discord
from discord import app_commands
from discord.ext import commands


class Poll(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="poll", description="Создать опрос в чате")
    @app_commands.guild_only()
    @app_commands.describe(
        question="Текст вопроса",
        option1="Первый вариант ответа",
        option2="Второй вариант ответа",
        option3="Третий вариант (необязательно)",
        option4="Четвёртый вариант (необязательно)",
        option5="Пятый вариант (необязательно)",
        duration_hours="Длительность опроса в часах (1-168, по умолчанию 24)",
        multiple="Разрешить несколько ответов (по умолчанию нет)",
    )
    async def poll(
        self,
        interaction: discord.Interaction,
        question: str,
        option1: str,
        option2: str,
        option3: str = None,
        option4: str = None,
        option5: str = None,
        duration_hours: app_commands.Range[int, 1, 168] = 24,
        multiple: bool = False,
    ):
        if len(question) > 300:
            await interaction.response.send_message(
                "Вопрос слишком длинный (максимум 300 символов).", ephemeral=True
            )
            return
        poll = discord.Poll(question=question, duration=duration_hours, multiple=multiple)
        poll.add_answer(text=option1)
        poll.add_answer(text=option2)
        for opt in (option3, option4, option5):
            if opt:
                poll.add_answer(text=opt)
        try:
            await interaction.response.send_message(poll=poll)
        except (discord.HTTPException, discord.Forbidden) as e:
            await interaction.response.send_message(
                f"Не удалось создать опрос: {e}", ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Poll(bot))