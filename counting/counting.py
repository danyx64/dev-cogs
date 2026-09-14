from __future__ import annotations

import asyncio
from typing import Dict, Optional

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


DEFAULT_MESSAGES: Dict[str, str] = {
    "same": "{user}, non puoi contare due volte di fila. Aspetta che conti un altro utente.",
    "wrong": "{user}, numero sbagliato. Il prossimo numero e **{next}**.",
    "text": "{user}, in questo canale puoi scrivere solo il prossimo numero: **{next}**.",
    "edit": "{user}, i messaggi del counting non si modificano. Il conteggio resta a **{count}**.",
}

MESSAGE_ALIASES = {
    "same": "same",
    "sameuser": "same",
    "stessoutente": "same",
    "wrong": "wrong",
    "number": "wrong",
    "numero": "wrong",
    "text": "text",
    "testo": "text",
    "invalid": "text",
    "edit": "edit",
    "modifica": "edit",
}


class Counting(commands.Cog):
    """Counting semplice: niente reset sugli errori, niente doppi turni consecutivi."""

    __author__ = "danyx64"
    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=73124590831674215, force_registration=True)
        self.config.register_guild(
            enabled=False,
            channel_id=None,
            count=0,
            last_user_id=None,
            reaction="✅",
            notice_seconds=5,
            messages=dict(DEFAULT_MESSAGES),
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
    def _message_key(value: str) -> Optional[str]:
        return MESSAGE_ALIASES.get(value.lower().strip())

    @staticmethod
    def _validate_template(template: str) -> Optional[str]:
        try:
            template.format(user="@utente", next=2, count=1, channel="#counting")
        except (KeyError, ValueError, IndexError) as exc:
            return str(exc)
        return None

    @staticmethod
    def _render(template: str, message: discord.Message, *, expected: int, count: int) -> str:
        if not template:
            return ""
        try:
            return template.format(
                user=message.author.mention,
                next=expected,
                count=count,
                channel=message.channel.mention,
            )
        except (KeyError, ValueError, IndexError):
            return ""

    async def _delete_message(self, message: discord.Message) -> None:
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

    async def _send_notice(
        self,
        channel: discord.TextChannel,
        text: str,
        seconds: int,
    ) -> None:
        if not text:
            return
        try:
            await channel.send(
                text,
                delete_after=seconds if seconds > 0 else None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _reject(
        self,
        message: discord.Message,
        settings: dict,
        kind: str,
        *,
        expected: int,
    ) -> None:
        await self._delete_message(message)
        templates = settings.get("messages") or {}
        template = str(templates.get(kind) or "")
        text = self._render(template, message, expected=expected, count=int(settings.get("count") or 0))
        await self._send_notice(
            message.channel,
            text,
            int(settings.get("notice_seconds") or 0),
        )

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

            # Niente due numeri validi consecutivi dallo stesso utente.
            if settings.get("last_user_id") == message.author.id:
                await self._reject(message, settings, "same", expected=expected)
                return

            # Solo il numero puro: niente testo, allegati, sticker o altre cose.
            has_extra = bool(message.attachments or message.stickers)
            if has_extra or not message.content.isdigit():
                await self._reject(message, settings, "text", expected=expected)
                return

            # Richiede esattamente la rappresentazione del numero atteso.
            # Quindi 001 non vale come 1.
            if message.content != str(expected):
                await self._reject(message, settings, "wrong", expected=expected)
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
            # Modificare un messaggio non cambia e non resetta il conteggio:
            # il messaggio modificato viene semplicemente eliminato.
            settings = await self.config.guild(guild).all()
            current = int(settings.get("count") or 0)
            await self._delete_message(message)
            templates = settings.get("messages") or {}
            template = str(templates.get("edit") or "")
            text = self._render(template, message, expected=current + 1, count=current)
            await self._send_notice(channel, text, int(settings.get("notice_seconds") or 0))

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
        if len(emoji) > 100:
            return await ctx.send("❌ Emoji non valida.")
        await self.config.guild(ctx.guild).reaction.set(emoji.strip())
        await ctx.send(f"✅ Reazione corretta impostata su {emoji.strip()}")

    @counting.command(name="messagetime", aliases=["messagetimeout", "tempo"])
    @commands.admin_or_permissions(manage_guild=True)
    async def counting_message_time(self, ctx: commands.Context, secondi: commands.Range[int, 0, 60]):
        """Secondi prima di eliminare gli avvisi. 0 = non eliminarli automaticamente."""
        await self.config.guild(ctx.guild).notice_seconds.set(secondi)
        if secondi == 0:
            await ctx.send("✅ Gli avvisi resteranno nel canale finche non vengono eliminati manualmente.")
        else:
            await ctx.send(f"✅ Gli avvisi spariranno dopo **{secondi} secondi**.")

    @counting.command(name="message", aliases=["messaggio"])
    @commands.admin_or_permissions(manage_guild=True)
    async def counting_message(
        self,
        ctx: commands.Context,
        tipo: str,
        *,
        testo: Optional[str] = None,
    ):
        """Personalizza un messaggio: same, wrong, text, edit. Usa off o reset."""
        key = self._message_key(tipo)
        if key is None:
            return await ctx.send("❌ Tipo valido: `same`, `wrong`, `text`, `edit`.")

        conf = self.config.guild(ctx.guild)
        messages = await conf.messages()

        if testo is None:
            current = str((messages or {}).get(key) or "")
            return await ctx.send(
                f"**{key}**: {current if current else '`OFF`'}\n"
                "Placeholder: `{user}` `{next}` `{count}` `{channel}`",
                allowed_mentions=discord.AllowedMentions.none(),
            )

        value = testo.strip()
        if value.lower() == "off":
            value = ""
        elif value.lower() == "reset":
            value = DEFAULT_MESSAGES[key]
        else:
            if len(value) > 1800:
                return await ctx.send("❌ Messaggio troppo lungo: massimo 1800 caratteri.")
            error = self._validate_template(value)
            if error:
                return await ctx.send(
                    "❌ Placeholder o parentesi non validi. Usa solo: "
                    "`{user}` `{next}` `{count}` `{channel}`."
                )

        messages = dict(messages or {})
        messages[key] = value
        await conf.messages.set(messages)
        await ctx.send(f"✅ Messaggio `{key}` aggiornato" + (" e disattivato." if not value else "."))

    @counting.command(name="messages", aliases=["messaggi"])
    @commands.admin_or_permissions(manage_guild=True)
    async def counting_messages(self, ctx: commands.Context):
        """Mostra tutti i messaggi personalizzabili."""
        settings = await self.config.guild(ctx.guild).all()
        messages = settings.get("messages") or {}
        lines = []
        for key in ("same", "wrong", "text", "edit"):
            value = str(messages.get(key) or "")
            lines.append(f"**{key}:** {value if value else '`OFF`'}")

        embed = discord.Embed(
            title="🔢 Counting - messaggi",
            description="\n\n".join(lines),
            colour=discord.Colour.blurple(),
        )
        embed.add_field(
            name="Placeholder",
            value="`{user}` `{next}` `{count}` `{channel}`",
            inline=False,
        )
        embed.add_field(
            name="Modifica rapida",
            value=(
                "`.counting message wrong <testo>`\n"
                "`.counting message wrong off`\n"
                "`.counting message wrong reset`"
            ),
            inline=False,
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

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
