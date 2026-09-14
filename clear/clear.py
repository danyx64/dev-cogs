from __future__ import annotations

from typing import Optional

import discord
from redbot.core import app_commands, commands
from redbot.core.bot import Red


MAX_CLEAR_MESSAGES = 1000
NOTICE_SECONDS = 5


class ChannelClear(commands.Cog):
    """Pulisce i canali con comandi testuali e slash."""

    __author__ = "danyx64"
    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot

    @staticmethod
    def _text_channel(channel) -> Optional[discord.TextChannel]:
        return channel if isinstance(channel, discord.TextChannel) else None

    @staticmethod
    def _member_can_manage_messages(channel: discord.TextChannel, member: discord.Member) -> bool:
        permissions = channel.permissions_for(member)
        return permissions.administrator or permissions.manage_messages

    @staticmethod
    def _member_can_manage_channel(channel: discord.TextChannel, member: discord.Member) -> bool:
        permissions = channel.permissions_for(member)
        return permissions.administrator or permissions.manage_channels

    @staticmethod
    def _bot_can_clear(channel: discord.TextChannel) -> bool:
        me = channel.guild.me
        if me is None:
            return False
        permissions = channel.permissions_for(me)
        return permissions.manage_messages and permissions.read_message_history

    @staticmethod
    def _bot_can_recreate(channel: discord.TextChannel) -> bool:
        me = channel.guild.me
        if me is None:
            return False
        permissions = channel.permissions_for(me)
        return permissions.manage_channels

    @staticmethod
    async def _delete_prefix_command(ctx: commands.Context) -> None:
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

    @staticmethod
    async def _prefix_notice(channel: discord.TextChannel, text: str) -> None:
        try:
            await channel.send(text, delete_after=NOTICE_SECONDS)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _recreate_channel(self, channel: discord.TextChannel, reason: str) -> discord.TextChannel:
        """Clona il canale, elimina il vecchio e rimette il clone nella stessa posizione."""
        old_position = channel.position
        new_channel = await channel.clone(reason=reason)

        try:
            await channel.delete(reason=reason)
        except Exception:
            # Se non riusciamo a cancellare il vecchio, evitiamo di lasciare un doppione.
            try:
                await new_channel.delete(reason="Rollback clearall")
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
            raise

        try:
            await new_channel.edit(position=old_position, reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            # Il canale e comunque stato ricreato correttamente; al massimo resta in una posizione diversa.
            pass

        return new_channel

    # ------------------------------------------------------------------
    # COMANDI CON PREFISSO: .clear, .clearall, .clearuser
    # ------------------------------------------------------------------

    @commands.command(name="clear")
    @commands.guild_only()
    async def prefix_clear(self, ctx: commands.Context, numero: int):
        """Cancella gli ultimi N messaggi. Esempio: .clear 50"""
        channel = self._text_channel(ctx.channel)
        if channel is None or not isinstance(ctx.author, discord.Member):
            return

        if not self._member_can_manage_messages(channel, ctx.author):
            return await self._prefix_notice(channel, "❌ Ti serve il permesso **Gestisci messaggi**.")
        if not self._bot_can_clear(channel):
            return await self._prefix_notice(
                channel,
                "❌ Mi servono i permessi **Gestisci messaggi** e **Leggi cronologia messaggi**.",
            )
        if numero < 1 or numero > MAX_CLEAR_MESSAGES:
            return await self._prefix_notice(
                channel,
                f"❌ Inserisci un numero tra **1** e **{MAX_CLEAR_MESSAGES}**.",
            )

        await self._delete_prefix_command(ctx)
        try:
            deleted = await channel.purge(
                limit=numero,
                reason=f".clear eseguito da {ctx.author} ({ctx.author.id})",
            )
        except (discord.Forbidden, discord.HTTPException):
            return await self._prefix_notice(channel, "❌ Non sono riuscito a cancellare i messaggi.")

        await self._prefix_notice(channel, f"✅ Cancellati **{len(deleted)}** messaggi.")

    @commands.command(name="clearall")
    @commands.guild_only()
    async def prefix_clearall(self, ctx: commands.Context):
        """Ricrea completamente il canale corrente. Esempio: .clearall"""
        channel = self._text_channel(ctx.channel)
        if channel is None or not isinstance(ctx.author, discord.Member):
            return

        if not self._member_can_manage_channel(channel, ctx.author):
            return await self._prefix_notice(channel, "❌ Ti serve il permesso **Gestisci canali**.")
        if not self._bot_can_recreate(channel):
            return await self._prefix_notice(channel, "❌ Mi serve il permesso **Gestisci canali**.")

        try:
            new_channel = await self._recreate_channel(
                channel,
                reason=f".clearall eseguito da {ctx.author} ({ctx.author.id})",
            )
        except (discord.Forbidden, discord.HTTPException):
            return await self._prefix_notice(channel, "❌ Non sono riuscito a ricreare il canale.")

        await self._prefix_notice(new_channel, "✅ Canale ricreato e cronologia azzerata.")

    @commands.command(name="clearuser")
    @commands.guild_only()
    async def prefix_clearuser(self, ctx: commands.Context, utente: discord.Member):
        """Cancella tutti i messaggi di un utente nel canale. Esempio: .clearuser @utente"""
        channel = self._text_channel(ctx.channel)
        if channel is None or not isinstance(ctx.author, discord.Member):
            return

        if not self._member_can_manage_messages(channel, ctx.author):
            return await self._prefix_notice(channel, "❌ Ti serve il permesso **Gestisci messaggi**.")
        if not self._bot_can_clear(channel):
            return await self._prefix_notice(
                channel,
                "❌ Mi servono i permessi **Gestisci messaggi** e **Leggi cronologia messaggi**.",
            )

        await self._delete_prefix_command(ctx)
        try:
            deleted = await channel.purge(
                limit=None,
                check=lambda message: message.author.id == utente.id,
                reason=f".clearuser {utente} ({utente.id}) eseguito da {ctx.author} ({ctx.author.id})",
            )
        except (discord.Forbidden, discord.HTTPException):
            return await self._prefix_notice(channel, "❌ Non sono riuscito a cancellare i messaggi dell'utente.")

        await self._prefix_notice(
            channel,
            f"✅ Cancellati **{len(deleted)}** messaggi di {utente.mention}.",
        )

    # ------------------------------------------------------------------
    # COMANDI SLASH: SOLO /clear e /clearall
    # Le risposte sono sempre ephemeral, quindi non sporcano il canale.
    # ------------------------------------------------------------------

    @app_commands.command(name="clear", description="Cancella gli ultimi messaggi del canale.")
    @app_commands.describe(numero="Numero di messaggi da cancellare")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def slash_clear(
        self,
        interaction: discord.Interaction,
        numero: app_commands.Range[int, 1, MAX_CLEAR_MESSAGES],
    ):
        channel = self._text_channel(interaction.channel)
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if channel is None or member is None:
            return await interaction.response.send_message(
                "❌ Questo comando funziona solo nei canali testuali di un server.",
                ephemeral=True,
            )

        if not self._member_can_manage_messages(channel, member):
            return await interaction.response.send_message(
                "❌ Ti serve il permesso **Gestisci messaggi**.",
                ephemeral=True,
            )
        if not self._bot_can_clear(channel):
            return await interaction.response.send_message(
                "❌ Mi servono i permessi **Gestisci messaggi** e **Leggi cronologia messaggi**.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            deleted = await channel.purge(
                limit=numero,
                reason=f"/clear eseguito da {member} ({member.id})",
            )
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.edit_original_response(
                content="❌ Non sono riuscito a cancellare i messaggi."
            )

        await interaction.edit_original_response(
            content=f"✅ Cancellati **{len(deleted)}** messaggi."
        )

    @app_commands.command(name="clearall", description="Ricrea il canale corrente e azzera tutta la cronologia.")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.guild_only()
    async def slash_clearall(self, interaction: discord.Interaction):
        channel = self._text_channel(interaction.channel)
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if channel is None or member is None:
            return await interaction.response.send_message(
                "❌ Questo comando funziona solo nei canali testuali di un server.",
                ephemeral=True,
            )

        if not self._member_can_manage_channel(channel, member):
            return await interaction.response.send_message(
                "❌ Ti serve il permesso **Gestisci canali**.",
                ephemeral=True,
            )
        if not self._bot_can_recreate(channel):
            return await interaction.response.send_message(
                "❌ Mi serve il permesso **Gestisci canali**.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        old_position = channel.position
        reason = f"/clearall eseguito da {member} ({member.id})"

        try:
            new_channel = await channel.clone(reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.edit_original_response(
                content="❌ Non sono riuscito a clonare il canale."
            )

        # Conferma privata prima di eliminare il canale che contiene l'interazione.
        await interaction.edit_original_response(
            content=f"✅ Canale ricreato: {new_channel.mention}. La vecchia cronologia viene eliminata."
        )

        try:
            await channel.delete(reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            try:
                await new_channel.delete(reason="Rollback /clearall")
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
            try:
                await interaction.edit_original_response(
                    content="❌ Non sono riuscito a eliminare il vecchio canale; il clone e stato rimosso."
                )
            except discord.HTTPException:
                pass
            return

        try:
            await new_channel.edit(position=old_position, reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            pass
