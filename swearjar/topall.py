import math

import discord
from discord import app_commands
from redbot.core import commands
from redbot.core.bot import Red


PAGE_SIZE = 20
EMBEDS_PER_MESSAGE = 10


class SwearJarTopAll(commands.Cog):
    """Comando slash per la classifica completa SwearJar."""

    def __init__(self, bot: Red, swearjar_cog):
        self.bot = bot
        self.swearjar = swearjar_cog

    @app_commands.command(
        name="topall",
        description="Mostra la classifica completa delle bestemmie del server.",
    )
    @app_commands.guild_only()
    async def topall(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "Questo comando puo essere usato solo in un server.",
                ephemeral=True,
            )

        await interaction.response.defer(thinking=True)

        all_members = await self.swearjar.config.all_members(guild)
        ranking = sorted(
            [
                (int(uid), int(data.get("count", 0) or 0))
                for uid, data in all_members.items()
                if int(data.get("count", 0) or 0) > 0
            ],
            key=lambda item: (-item[1], item[0]),
        )

        if not ranking:
            return await interaction.followup.send(
                "La leaderboard e ancora vuota.",
                ephemeral=True,
            )

        total = sum(count for _, count in ranking)
        total_pages = math.ceil(len(ranking) / PAGE_SIZE)
        embeds = []
        medals = ["🥇", "🥈", "🥉"]

        for page_index in range(total_pages):
            start = page_index * PAGE_SIZE
            page_entries = ranking[start:start + PAGE_SIZE]
            lines = []

            for offset, (uid, count) in enumerate(page_entries):
                position = start + offset + 1
                prefix = medals[position - 1] if position <= 3 else f"**{position}.**"
                lines.append(f"{prefix} <@{uid}> — **{count}**")

            embed = discord.Embed(
                title="🏆 Classifica completa Swear Jar",
                description="\n".join(lines),
                colour=discord.Colour.gold(),
            )
            embed.add_field(
                name="Totale server",
                value=f"**{total}** bestemmie rilevate",
                inline=True,
            )
            embed.add_field(
                name="Membri in classifica",
                value=f"**{len(ranking)}**",
                inline=True,
            )
            embed.set_footer(
                text=f"Pagina {page_index + 1}/{total_pages} • {PAGE_SIZE} utenti per pagina"
            )
            embeds.append(embed)

        allowed_mentions = discord.AllowedMentions(
            users=True,
            roles=False,
            everyone=False,
        )

        for index in range(0, len(embeds), EMBEDS_PER_MESSAGE):
            await interaction.followup.send(
                embeds=embeds[index:index + EMBEDS_PER_MESSAGE],
                allowed_mentions=allowed_mentions,
            )
