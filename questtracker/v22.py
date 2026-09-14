import discord
from redbot.core import commands

from .v21 import QuestTracker as QuestTrackerV21


class QuestTracker(QuestTrackerV21):
    """QuestTracker 3.0.2: embed pulito e menzione ruolo sotto l'embed."""

    __version__ = "3.0.2"

    async def _build_embed_for_guild(self, guild, entry, config, *, test=False):
        embed, settings, values = await super()._build_embed_for_guild(
            guild, entry, config, test=test
        )

        # Nessuna scritta piccola in fondo all'embed.
        embed.remove_footer()

        # La menzione del ruolo viene inviata come messaggio separato subito
        # sotto l'embed, cosi Discord genera davvero la notifica del ruolo.
        role = guild.get_role(settings.get("role_id") or 0)
        if role is not None:
            if embed.title:
                embed.title = embed.title.replace(role.mention, "").strip()
            if embed.description:
                embed.description = embed.description.replace(role.mention, "").strip()

        return embed, settings, values

    async def _send_quest_embed(self, channel, guild, entry, config, *, test=False):
        embed, settings, _ = await self._build_embed_for_guild(
            guild, entry, config, test=test
        )

        # Prima l'embed, poi la menzione: in Discord il secondo messaggio
        # appare realmente sotto l'embed.
        embed_message = await channel.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        role = guild.get_role(settings.get("role_id") or 0)
        if (
            not test
            and role is not None
            and bool(settings.get("ping_role", True))
        ):
            await channel.send(
                role.mention,
                allowed_mentions=discord.AllowedMentions(
                    roles=True,
                    users=False,
                    everyone=False,
                ),
            )

        return embed_message

    async def command_footer(self, ctx: commands.Context, testo=None):
        """Il footer e disattivato per mantenere l'embed semplice."""
        await ctx.send(
            "✅ Footer disattivato: le Quest non mostrano più scritte piccole in fondo all'embed."
        )
