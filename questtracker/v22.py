import discord
from redbot.core import commands

from .v21 import QuestTracker as QuestTrackerV21


class QuestTracker(QuestTrackerV21):
    """QuestTracker 3.0.1: embed pulito senza footer."""

    __version__ = "3.0.1"

    async def _build_embed_for_guild(self, guild, entry, config, *, test=False):
        embed, settings, values = await super()._build_embed_for_guild(
            guild, entry, config, test=test
        )
        # Nessuna scritta piccola in fondo all'embed.
        embed.remove_footer()
        return embed, settings, values

    async def command_footer(self, ctx: commands.Context, testo=None):
        """Il footer e disattivato per mantenere l'embed semplice."""
        await ctx.send("✅ Footer disattivato: le Quest non mostrano più scritte piccole in fondo all'embed.")
