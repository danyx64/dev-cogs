from __future__ import annotations

import asyncio
from typing import Dict, Optional

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


SAME_USER_MESSAGE = "Non puoi contare da solo."
SAME_USER_MESSAGE_SECONDS = 5


class Counting(commands.Cog):
    """Counting semplice: niente reset sugli errori, niente doppi turni consecutivi."""

    __author__ = "danyx64"
    __version__ = "1.1.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=73124590831674215, force_registration=True)
        self.config.register_guild(
            enabled=False,
            channel_id=None,
            count=0,
            last_user_id=None,
            reaction="✅",
        )
        self._locks: Dict[int, asyncio.Lock] = {}

    def _lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[guild_id] = lock
        return lock

    @staticmethod
    def _bot_has_channel_perms(channel: discord.TextChannel) -> bool:
        me = channel.guild.me
        if me is None:
            return False
        perms = channel.permissions_for(me)
        return (
            perms.view_channel
            and perms.send_messages
            and perms.read_message_history
            and perms.manage_messages
            and perms.add_reactions
        )

    @staticmethod
    async def _delete_message(message: discord.Message) -> None:
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

    @staticmethod
    async def _send_same_user_notice(channel: discord.TextChannel) -> None:
        try:
            await channel.send(
                SAME_USER_MESSAGE,
                delete_after=SAME_USER_MESSAGE_SECONDS,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or message.webhook_id is not None:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return

        async with self._lock(message.guild.id):
            settings = await self.config.guild(message.guild).all()
            if not settings.get("enabled"):
                return
            if message.channel.id != settings.get("channel_id"):
                return

            current = int(settings.get("count") or 0)
            expected = current + 1

            # Nel canale counting deve esserci solo il numero puro.
            # Testo, allegati, sticker o qualsiasi altra cosa vengono eliminati in silenzio.
            has_extra = bool(message.attachments or message.stickers)
            if has_extra or not message.content.isdigit():
                await self._delete_message(message)
                return

            # Un numero sbagliato viene eliminato senza alcun messaggio e senza reset.
            # Anche formati come 001 non valgono come 1.
            if message.content != str(expected):
                await self._delete_message(message)
                return

            # Solo se l'utente prova davvero a mandare il prossimo numero corretto
            # due volte di fila mostriamo l'unico avviso del cog.
            if settings.get("last_user_id") == message.author.id:
                await self._delete_message(message)
                await self._send_same_user_notice(message.channel)
                return

            await self.config.guild(message.guild).count.set(expected)
            await self.config.guild(message.guild).last_user_id.set(message.author.id)

            reaction = str(settings.get("reaction") or "✅")
            try:
                await message.add_reaction(reaction)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException, TypeError):
                # Il conteggio rimane valido anche se Discord rifiuta la reazione.
                pass

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        if payload.guild_id is None or "content" not in payload.data:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        settings = await self.config.guild(guild).all()
        if not settings.get("enabled") or payload.channel_id != settings.get("channel_id"):
            return

        channel = guild.get_channel(payload.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

        if message.author.bot or message.webhook_id is not None:
            return

        async with self._lock(guild.id):
            # I messaggi del counting modificati vengono semplicemente eliminati.
            # Il conteggio salvato non viene resettato e non viene inviato alcun avviso.
            await self._delete_message(message)

    @commands.group(name="counting", aliases=["count"], invoke_without_command=True)
    @commands.guild_only()
    async def counting(self, ctx: commands.Context):
        """Configura e controlla il counting."""
        await ctx.send_help(ctx.command)

    @counting.command(name="setup")
    @commands.admin_or_permissions(manage_guild=True)
    async def counting_setup(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        current: Optional[int] = None,
    ):
        """Imposta il canale. Opzionalmente imposta anche il numero corrente."""
        if not self._bot_has_channel_perms(channel):
            return await ctx.send(
                "❌ In quel canale mi servono **Visualizza canale**, **Invia messaggi**, "
                "**Leggi cronologia**, **Gestisci messaggi** e **Aggiungi reazioni**."
            )
        if current is not None and current < 0:
            return await ctx.send("❌ Il numero corrente non puo essere negativo.")

        conf = self.config.guild(ctx.guild)
        await conf.channel_id.set(channel.id)
        await conf.enabled.set(True)
        if current is not None:
            await conf.count.set(current)
            await conf.last_user_id.set(None)

        value = await conf.count()
        await ctx.send(
            f"✅ Counting attivo in {channel.mention}. Numero corrente: **{value}**; "
            f"il prossimo e **{value + 1}**."
        )

    @counting.command(name="on", aliases=["enable", "attiva"])
    @commands.admin_or_permissions(manage_guild=True)
    async def counting_on(self, ctx: commands.Context):
        channel_id = await self.config.guild(ctx.guild).channel_id()
        channel = ctx.guild.get_channel(channel_id or 0)
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send("❌ Prima usa `.counting setup #canale`.")
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("✅ Counting attivato.")

    @counting.command(name="off", aliases=["disable", "disattiva"])
    @commands.admin_or_permissions(manage_guild=True)
    async def counting_off(self, ctx: commands.Context):
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("⏸️ Counting disattivato. Il numero corrente viene conservato.")

    @counting.command(name="setcount", aliases=["set", "numero"])
    @commands.admin_or_permissions(manage_guild=True)
    async def counting_setcount(self, ctx: commands.Context, numero: int):
        """Imposta manualmente il numero corrente senza creare messaggi."""
        if numero < 0:
            return await ctx.send("❌ Il numero non puo essere negativo.")
        conf = self.config.guild(ctx.guild)
        await conf.count.set(numero)
        await conf.last_user_id.set(None)
        await ctx.send(f"✅ Numero corrente impostato a **{numero}**. Prossimo: **{numero + 1}**.")

    @counting.command(name="reaction", aliases=["reazione"])
    @commands.admin_or_permissions(manage_guild=True)
    async def counting_reaction(self, ctx: commands.Context, emoji: str = "✅"):
        """Imposta la reazione aggiunta ai conteggi corretti."""
        emoji = emoji.strip()
        if not emoji or len(emoji) > 100:
            return await ctx.send("❌ Emoji non valida.")
        await self.config.guild(ctx.guild).reaction.set(emoji)
        await ctx.send(f"✅ Reazione corretta impostata su {emoji}")

    @counting.command(name="status")
    async def counting_status(self, ctx: commands.Context):
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(settings.get("channel_id") or 0)
        last_user = ctx.guild.get_member(settings.get("last_user_id") or 0)
        current = int(settings.get("count") or 0)

        embed = discord.Embed(title="🔢 Counting", colour=discord.Colour.blurple())
        embed.add_field(
            name="Stato",
            value="✅ Attivo" if settings.get("enabled") else "⏸️ Disattivato",
            inline=True,
        )
        embed.add_field(
            name="Canale",
            value=channel.mention if isinstance(channel, discord.TextChannel) else "Non configurato",
            inline=True,
        )
        embed.add_field(name="Numero corrente", value=str(current), inline=True)
        embed.add_field(name="Prossimo numero", value=str(current + 1), inline=True)
        embed.add_field(
            name="Ultimo utente",
            value=last_user.mention if last_user else "Nessuno",
            inline=True,
        )
        embed.add_field(name="Reazione", value=str(settings.get("reaction") or "✅"), inline=True)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @counting.command(name="version", aliases=["versione"])
    async def counting_version(self, ctx: commands.Context):
        await ctx.send(f"Counting **v{self.__version__}**")