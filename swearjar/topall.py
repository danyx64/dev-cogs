import math
from typing import List, Tuple

import discord
from discord import app_commands
from redbot.core import commands
from redbot.core.bot import Red


PAGE_SIZE = 20


class FullLeaderboardView(discord.ui.View):
    """Paginazione interattiva della classifica completa SwearJar."""

    def __init__(
        self,
        author_id: int,
        ranking: List[Tuple[int, int]],
        total: int,
    ):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.ranking = ranking
        self.total = total
        self.page = 0
        self.total_pages = max(1, math.ceil(len(ranking) / PAGE_SIZE))

    def build_embed(self) -> discord.Embed:
        start = self.page * PAGE_SIZE
        end = start + PAGE_SIZE
        page_entries = self.ranking[start:end]

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for offset, (uid, count) in enumerate(page_entries):
            position = start + offset + 1
            prefix = medals[position - 1] if position <= 3 else f"**{position}.**"
            lines.append(f"{prefix} <@{uid}> — **{count}**")

        embed = discord.Embed(
            title="🏆 Classifica completa Swear Jar",
            description="\n".join(lines) if lines else "La leaderboard e ancora vuota.",
            colour=discord.Colour.gold(),
        )
        embed.add_field(
            name="Totale server",
            value=f"**{self.total}** bestemmie rilevate",
            inline=True,
        )
        embed.add_field(
            name="Membri in classifica",
            value=f"**{len(self.ranking)}**",
            inline=True,
        )
        embed.set_footer(text=f"Pagina {self.page + 1}/{self.total_pages} • {PAGE_SIZE} utenti per pagina")

        self.previous.disabled = self.page <= 0
        self.next.disabled = self.page >= self.total_pages - 1
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message(
            "Solo chi ha aperto la classifica puo cambiare pagina.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.total_pages - 1:
            self.page += 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


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
        view = FullLeaderboardView(interaction.user.id, ranking, total)

        await interaction.followup.send(
            embed=view.build_embed(),
            view=view,
            allowed_mentions=discord.AllowedMentions(
                users=True,
                roles=False,
                everyone=False,
            ),
        )
