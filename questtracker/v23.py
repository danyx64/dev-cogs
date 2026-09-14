import discord
from redbot.core import commands

from .questtracker import _hero_image, _platforms, _reward_image, _task_text
from .v2 import _cta_url, _rewards
from .v22 import QuestTracker as QuestTrackerV22


class QuestTracker(QuestTrackerV22):
    """QuestTracker 3.1.0: layout fisso, niente TXT, menzione sotto l'embed."""

    __version__ = "3.1.0"

    async def _build_embed_for_guild(self, guild, entry, config, *, test=False):
        """Costruisce sempre lo stesso embed, ignorando embed_template.txt."""
        settings = await self.config.guild(guild).all()
        data = self._quest_payload(entry, config)
        app = config.get("application") or {}
        rewards = _rewards(config)
        first_reward = rewards[0] if rewards else {}

        prefix = "TEST • " if test else ""
        embed = discord.Embed(
            title=f"{prefix}Nuova Quest - {data['quest']}",
            url=_cta_url(config) or None,
            colour=discord.Colour.blurple(),
        )

        info = (
            f"**Durata:** {data['start']} - {data['end']}\n"
            f"**Piattaforme:** {_platforms(config)}\n"
            f"**Gioco/App:** {data['game']} (`{data['application_id']}`)"
        )
        embed.add_field(name="📋 Informazioni Quest", value=info, inline=False)
        embed.add_field(name="✅ Obiettivi", value=_task_text(config)[:1024], inline=False)

        reward_type = "Orbs" if first_reward.get("orb_quantity") is not None else str(first_reward.get("type") or "Ricompensa virtuale")
        reward_text = f"**Tipo:** {reward_type}\n**Nome:** {data['reward']}"
        if first_reward.get("orb_quantity") is not None:
            reward_text += f"\n**Orb Amount:** {first_reward.get('orb_quantity')}"
        if data.get("sku") and data["sku"] != "Sconosciuto":
            reward_text += f"\n**SKU ID:** `{data['sku']}`"
        embed.add_field(name="🎁 Ricompense", value=reward_text[:1024], inline=False)

        hero = _hero_image(data["quest_id"], config)
        if hero:
            embed.set_image(url=hero)

        reward_image = _reward_image(data["quest_id"], config)
        if reward_image:
            embed.set_thumbnail(url=reward_image)

        # Niente footer, niente timestamp, niente menzione dentro l'embed.
        embed.remove_footer()
        return embed, settings, data

    async def _send_role_below(self, channel, guild, settings, *, test=False):
        """Invia il ruolo come secondo messaggio, quindi visivamente sotto l'embed."""
        role = guild.get_role(settings.get("role_id") or 0)
        if role is None:
            return

        if test:
            # Nel test si vede la menzione ma non parte la notifica reale.
            await channel.send(
                role.mention,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        if bool(settings.get("ping_role", True)):
            await channel.send(
                role.mention,
                allowed_mentions=discord.AllowedMentions(
                    roles=True,
                    users=False,
                    everyone=False,
                ),
            )

    async def _send_quest_embed(self, channel, guild, entry, config, *, test=False):
        embed, settings, _ = await self._build_embed_for_guild(
            guild, entry, config, test=test
        )

        embed_message = await channel.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self._send_role_below(channel, guild, settings, test=test)
        return embed_message

    async def command_test(self, ctx: commands.Context):
        """Anteprima identica alla notifica reale, ma senza pingare davvero il ruolo."""
        async with ctx.typing():
            active = self._active_quests(await self._fetch_quests())
        if not active:
            return await ctx.send("❌ Al momento non trovo Quest attive nei feed.")

        _, entry, config = active[0]
        await self._send_quest_embed(
            ctx.channel,
            ctx.guild,
            entry,
            config,
            test=True,
        )

    async def command_message(self, ctx: commands.Context, testo=None):
        """Il layout e ora fisso nel codice."""
        await ctx.send(
            "ℹ️ Il layout Quest è ora fisso nel cog e non usa più `embed_template.txt`. "
            "Dimmi cosa vuoi cambiare e lo aggiorno direttamente nel codice."
        )

    async def command_title(self, ctx: commands.Context, testo=None):
        await ctx.send(
            "ℹ️ Il titolo Quest è ora fisso nel cog: `Nuova Quest - <nome quest>`."
        )

    async def command_footer(self, ctx: commands.Context, testo=None):
        await ctx.send("✅ Il footer è disattivato permanentemente.")
