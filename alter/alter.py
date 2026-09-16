from __future__ import annotations

import asyncio
import logging
import re
from typing import Dict, Optional

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


log = logging.getLogger("red.danyx64.alter")

CONFIG_ID = 0xA17E2026
WEBHOOK_NAME = "Alter"
DEFAULT_MESSAGE = "BT Failed: Too many attempts"
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")
ALLOWED_PLACEHOLDERS = {
    "content",
    "name",
    "username",
    "mention",
    "id",
    "channel",
    "server",
    "attachments",
}


class Alter(commands.Cog):
    """Sostituisce i messaggi in un canale usando un webhook con nome/avatar dell'autore."""

    __author__ = "danyx64"
    __version__ = "1.0.1"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_ID, force_registration=True)
        self.config.register_guild(
            enabled=False,
            channel_id=None,
            webhook_id=None,
            message_template=DEFAULT_MESSAGE,
        )
        self._webhook_cache: Dict[int, discord.Webhook] = {}
        self._webhook_locks: Dict[int, asyncio.Lock] = {}

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
        return

    def _lock_for(self, guild_id: int) -> asyncio.Lock:
        lock = self._webhook_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._webhook_locks[guild_id] = lock
        return lock

    @staticmethod
    def _missing_permissions(channel: discord.TextChannel) -> list[str]:
        me = channel.guild.me
        if me is None:
            return ["Manage Webhooks", "Manage Messages", "View Channel"]
        perms = channel.permissions_for(me)
        missing = []
        if not perms.manage_webhooks:
            missing.append("Manage Webhooks")
        if not perms.manage_messages:
            missing.append("Manage Messages")
        if not perms.view_channel:
            missing.append("View Channel")
        return missing

    async def _find_webhook(
        self,
        channel: discord.TextChannel,
        webhook_id: Optional[int],
    ) -> Optional[discord.Webhook]:
        if not webhook_id:
            return None

        cached = self._webhook_cache.get(channel.guild.id)
        if cached is not None and cached.id == webhook_id and cached.channel_id == channel.id:
            return cached

        try:
            webhooks = await channel.webhooks()
        except (discord.Forbidden, discord.HTTPException):
            return None

        for webhook in webhooks:
            if webhook.id == webhook_id and webhook.type is discord.WebhookType.incoming:
                self._webhook_cache[channel.guild.id] = webhook
                return webhook
        return None

    async def _create_webhook(self, channel: discord.TextChannel) -> discord.Webhook:
        webhook = await channel.create_webhook(
            name=WEBHOOK_NAME,
            reason="Alter cog webhook proxy",
        )
        self._webhook_cache[channel.guild.id] = webhook
        await self.config.guild(channel.guild).webhook_id.set(webhook.id)
        return webhook

    async def _ensure_webhook(self, channel: discord.TextChannel) -> Optional[discord.Webhook]:
        async with self._lock_for(channel.guild.id):
            conf = self.config.guild(channel.guild)
            webhook = await self._find_webhook(channel, await conf.webhook_id())
            if webhook is not None:
                return webhook

            missing = self._missing_permissions(channel)
            if missing:
                log.warning(
                    "Alter cannot create webhook in guild %s channel %s; missing: %s",
                    channel.guild.id,
                    channel.id,
                    ", ".join(missing),
                )
                return None

            try:
                return await self._create_webhook(channel)
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning(
                    "Alter failed to create webhook in guild %s channel %s: %r",
                    channel.guild.id,
                    channel.id,
                    exc,
                )
                return None

    async def _delete_configured_webhook(self, guild: discord.Guild) -> None:
        conf = self.config.guild(guild)
        channel = guild.get_channel((await conf.channel_id()) or 0)
        webhook_id = await conf.webhook_id()
        self._webhook_cache.pop(guild.id, None)
        if not isinstance(channel, discord.TextChannel) or not webhook_id:
            return

        webhook = await self._find_webhook(channel, webhook_id)
        if webhook is None:
            return
        try:
            await webhook.delete(reason="Alter configuration removed")
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

    @staticmethod
    def _unknown_placeholders(template: str) -> list[str]:
        return sorted(
            {
                match.group(1)
                for match in PLACEHOLDER_RE.finditer(template)
                if match.group(1) not in ALLOWED_PLACEHOLDERS
            }
        )

    @staticmethod
    def _render_message(template: str, message: discord.Message) -> str:
        values = {
            "content": message.content or "",
            "name": message.author.display_name,
            "username": message.author.name,
            "mention": message.author.mention,
            "id": str(message.author.id),
            "channel": message.channel.mention,
            "server": message.guild.name if message.guild else "",
            "attachments": " ".join(a.url for a in message.attachments),
        }
        rendered = template
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", str(value))
        return rendered.replace("\\n", "\n").strip()[:2000]

    async def _send_as_author(
        self,
        webhook: discord.Webhook,
        message: discord.Message,
        content: str,
    ) -> None:
        username = message.author.display_name.strip()[:80] or message.author.name[:80]
        await webhook.send(
            content=content or "\u200b",
            username=username,
            avatar_url=str(message.author.display_avatar.url),
            allowed_mentions=discord.AllowedMentions.none(),
            wait=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or not isinstance(message.channel, discord.TextChannel):
            return
        if message.webhook_id is not None or message.author.bot:
            return

        conf = self.config.guild(message.guild)
        if not await conf.enabled() or message.channel.id != await conf.channel_id():
            return

        # I comandi del bot non vengono alterati, quindi `.alter ...` resta sempre utilizzabile.
        try:
            ctx = await self.bot.get_context(message)
            if ctx.valid:
                return
        except Exception:
            pass

        webhook = await self._ensure_webhook(message.channel)
        if webhook is None:
            return

        content = self._render_message(
            str(await conf.message_template() or DEFAULT_MESSAGE),
            message,
        )

        try:
            await message.delete()
        except discord.NotFound:
            return
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning(
                "Alter could not delete message %s in guild %s: %r",
                message.id,
                message.guild.id,
                exc,
            )
            return

        try:
            await self._send_as_author(webhook, message, content)
            return
        except discord.NotFound:
            # Webhook eliminato tra controllo e invio: lo ricreiamo una volta.
            self._webhook_cache.pop(message.guild.id, None)
            await conf.webhook_id.set(None)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Alter webhook send failed in guild %s: %r", message.guild.id, exc)
            return

        retry_webhook = await self._ensure_webhook(message.channel)
        if retry_webhook is None:
            return
        try:
            await self._send_as_author(retry_webhook, message, content)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            log.warning("Alter webhook retry failed in guild %s: %r", message.guild.id, exc)

    @commands.group(name="alter", invoke_without_command=True)
    @commands.guild_only()
    async def alter(self, ctx: commands.Context) -> None:
        """Configura Alter."""
        await ctx.send_help(ctx.command)

    @alter.command(name="setup", aliases=["canale", "channel"])
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_setup(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Imposta il canale e crea/riusa automaticamente il webhook Alter."""
        missing = self._missing_permissions(channel)
        if missing:
            return await ctx.send(
                "❌ Nel canale mi mancano questi permessi: **" + ", ".join(missing) + "**."
            )

        conf = self.config.guild(ctx.guild)
        old_channel_id = await conf.channel_id()

        # Stesso canale: riusa il webhook esistente; se e stato cancellato, lo ricrea.
        if old_channel_id == channel.id:
            webhook = await self._ensure_webhook(channel)
            if webhook is None:
                return await ctx.send("❌ Non sono riuscito a creare/trovare il webhook Alter.")
            await conf.enabled.set(True)
            return await ctx.send(
                f"✅ Alter attivato in {channel.mention}. Webhook riutilizzato: **{webhook.name}** (`{webhook.id}`)."
            )

        # Cambio canale: elimina il vecchio webhook Alter prima di crearne uno nuovo.
        if old_channel_id:
            await self._delete_configured_webhook(ctx.guild)

        await conf.channel_id.set(channel.id)
        await conf.webhook_id.set(None)
        self._webhook_cache.pop(ctx.guild.id, None)
        webhook = await self._ensure_webhook(channel)
        if webhook is None:
            return await ctx.send("❌ Non sono riuscito a creare il webhook Alter.")

        await conf.enabled.set(True)
        await ctx.send(
            f"✅ Alter attivato in {channel.mention}. Webhook creato: **{webhook.name}** (`{webhook.id}`)."
        )

    @alter.command(name="messaggio", aliases=["message", "testo"])
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_message(self, ctx: commands.Context, *, testo: Optional[str] = None) -> None:
        """Imposta il messaggio che sostituisce quello dell'utente."""
        conf = self.config.guild(ctx.guild)
        if testo is None:
            current = str(await conf.message_template() or DEFAULT_MESSAGE)
            return await ctx.send(
                "**Messaggio Alter attuale**\n"
                f"```\n{current}\n```\n"
                "Placeholder: `{content}` `{name}` `{username}` `{mention}` `{id}` "
                "`{channel}` `{server}` `{attachments}`.\n"
                "Reset: `.alter messaggio reset`. Per andare a capo usa `\\n`."
            )

        template = str(testo).strip()
        if template.lower() in {"reset", "default", "predefinito"}:
            await conf.message_template.set(DEFAULT_MESSAGE)
            return await ctx.send(f"✅ Messaggio ripristinato a: `{DEFAULT_MESSAGE}`")
        if not template:
            return await ctx.send("❌ Il messaggio non puo essere vuoto.")
        if len(template) > 1900:
            return await ctx.send("❌ Il template puo avere massimo 1900 caratteri.")

        unknown = self._unknown_placeholders(template)
        if unknown:
            return await ctx.send(
                "❌ Placeholder sconosciuti: " + ", ".join(f"`{{{name}}}`" for name in unknown)
            )

        await conf.message_template.set(template)
        await ctx.send("✅ Messaggio Alter aggiornato. Usa `.alter test` per provarlo.")

    @alter.command(name="test", aliases=["prova", "preview"])
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_test(self, ctx: commands.Context) -> None:
        """Invia un test usando il tuo nome/avatar senza eliminare il comando."""
        conf = self.config.guild(ctx.guild)
        channel = ctx.guild.get_channel((await conf.channel_id()) or 0)
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send("❌ Prima usa `.alter setup #canale`.")

        webhook = await self._ensure_webhook(channel)
        if webhook is None:
            return await ctx.send("❌ Webhook Alter non disponibile.")

        content = self._render_message(str(await conf.message_template() or DEFAULT_MESSAGE), ctx.message)
        try:
            await self._send_as_author(webhook, ctx.message, content)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            return await ctx.send(f"❌ Invio test fallito: `{type(exc).__name__}`")
        await ctx.send(f"✅ Test inviato in {channel.mention}.")

    @alter.command(name="on", aliases=["enable", "attiva"])
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_on(self, ctx: commands.Context) -> None:
        conf = self.config.guild(ctx.guild)
        channel = ctx.guild.get_channel((await conf.channel_id()) or 0)
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send("❌ Prima usa `.alter setup #canale`.")
        if await self._ensure_webhook(channel) is None:
            return await ctx.send("❌ Non riesco a creare/trovare il webhook Alter.")
        await conf.enabled.set(True)
        await ctx.send("✅ Alter attivato.")

    @alter.command(name="off", aliases=["disable", "disattiva"])
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_off(self, ctx: commands.Context) -> None:
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("⏸️ Alter disattivato. Il webhook resta salvato per la prossima attivazione.")

    @alter.command(name="status")
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_status(self, ctx: commands.Context) -> None:
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(settings.get("channel_id") or 0)
        webhook = None
        if isinstance(channel, discord.TextChannel):
            webhook = await self._find_webhook(channel, settings.get("webhook_id"))
        template = str(settings.get("message_template") or DEFAULT_MESSAGE)

        embed = discord.Embed(title="Alter", colour=discord.Colour.blurple())
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
        embed.add_field(
            name="Webhook",
            value=f"✅ `{webhook.id}`" if webhook else "❌ assente / da ricreare",
            inline=True,
        )
        embed.add_field(name="Messaggio", value=f"```\n{template[:950]}\n```", inline=False)
        embed.set_footer(text=f"Alter v{self.__version__}")
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @alter.command(name="remove", aliases=["rimuovi", "reset"])
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_remove(self, ctx: commands.Context) -> None:
        """Disattiva Alter, elimina il suo webhook e resetta la configurazione."""
        conf = self.config.guild(ctx.guild)
        await conf.enabled.set(False)
        await self._delete_configured_webhook(ctx.guild)
        await conf.channel_id.set(None)
        await conf.webhook_id.set(None)
        await conf.message_template.set(DEFAULT_MESSAGE)
        self._webhook_cache.pop(ctx.guild.id, None)
        await ctx.send("✅ Alter rimosso: webhook eliminato e configurazione resettata.")

    @alter.command(name="version", aliases=["versione"])
    async def alter_version(self, ctx: commands.Context) -> None:
        await ctx.send(f"Alter **v{self.__version__}**.")
