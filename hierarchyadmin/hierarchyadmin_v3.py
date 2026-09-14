import discord
from redbot.core import commands

from .hierarchyadmin_v2 import HierarchyAdmin as BaseHierarchyAdmin


class HierarchyAdmin(BaseHierarchyAdmin):
    """HierarchyAdmin v1.2: diagnostica anche la gestione di un messaggio preciso."""

    __version__ = "1.2.1"

    @BaseHierarchyAdmin.ha.command(name="candelete", aliases=["checkmessage", "messagedelete"])
    @commands.admin_or_permissions(manage_messages=True)
    async def ha_candelete(self, ctx: commands.Context, message_id: int, channel_id: int = None):
        """Controlla se puoi gestire/eliminare un messaggio: `.ha candelete MESSAGE_ID [CANALE_ID]`."""
        channel = ctx.channel
        if channel_id is not None:
            channel = await self._channel(ctx, channel_id)
            if channel is None:
                return

        if not hasattr(channel, "fetch_message"):
            return await ctx.send("Questo tipo di canale non permette di recuperare messaggi con questo comando.")

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            return await ctx.send("Messaggio non trovato in quel canale. Controlla ID messaggio e ID canale.")
        except discord.Forbidden:
            return await ctx.send("Il bot non ha accesso a quel messaggio/canale.")
        except discord.HTTPException as exc:
            return await ctx.send(f"Discord ha rifiutato il recupero del messaggio: `{exc}`")

        member = ctx.author
        effective = channel.permissions_for(member)
        bot_perms = channel.permissions_for(ctx.guild.me) if ctx.guild.me else None
        author = message.author
        message_type = getattr(message.type, "name", str(message.type))

        # Per un normale messaggio altrui, manage_messages e' il permesso rilevante.
        own_message = author.id == member.id
        expected = own_message or effective.manage_messages or effective.administrator

        lines = [
            f"**Messaggio:** `{message.id}`",
            f"**Canale:** {getattr(channel, 'mention', channel.name)} (`{channel.id}`)",
            f"**Autore:** {author.mention} (`{author.id}`)",
            f"**Tipo Discord:** `{message_type}` (`{message.type.value}`)",
            f"**Creato:** <t:{int(message.created_at.timestamp())}:F>",
            f"**Tuo messaggio:** {'Si' if own_message else 'No'}",
            "",
            "**Permessi del tuo account nel canale:**",
            f"{'OK' if effective.administrator else 'NO'} `administrator`",
            f"{'OK' if effective.manage_messages else 'NO'} `manage_messages`",
            f"{'OK' if effective.read_message_history else 'NO'} `read_message_history`",
            f"{'OK' if effective.view_channel else 'NO'} `view_channel`",
            "",
            f"**Diagnosi:** {'DOVRESTI poter eliminare questo messaggio.' if expected else 'NON dovresti poter eliminare un messaggio altrui con questi permessi.'}",
        ]

        if message.is_system():
            lines.append("Il messaggio risulta di tipo **system**; alcune azioni/UI possono differire dai messaggi normali.")
        else:
            lines.append("Il messaggio risulta un normale messaggio gestibile tramite le regole standard di Discord.")

        if bot_perms is not None:
            lines.extend([
                "",
                "**Permessi del bot nello stesso canale:**",
                f"{'OK' if bot_perms.manage_messages else 'NO'} `manage_messages`",
                f"{'OK' if bot_perms.read_message_history else 'NO'} `read_message_history`",
                f"{'OK' if bot_perms.view_channel else 'NO'} `view_channel`",
            ])

        embed = discord.Embed(
            title="Diagnostica eliminazione messaggio",
            description="\n".join(lines)[:4096],
            colour=discord.Colour.blurple(),
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
