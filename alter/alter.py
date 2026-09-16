from __future__ import annotations

import asyncio
import base64
import codecs
import logging
import random
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

MODE_DESCRIPTIONS = {
    "random": "sceglie un effetto casuale a ogni messaggio",
    "original": "ripubblica il testo originale",
    "fixed": "usa il messaggio configurato con .alter messaggio",
    "spoiler": "nasconde tutto il testo dietro uno spoiler",
    "spoilerwords": "nasconde ogni parola in uno spoiler separato",
    "spoilerrandom": "nasconde casualmente molte parole",
    "mock": "alterna maiuscole e minuscole",
    "reverse": "inverte l'intero testo",
    "wordreverse": "inverte ogni parola singolarmente",
    "shuffle": "mischia l'ordine delle parole",
    "redact": "oscura casualmente alcune parole",
    "typo": "inserisce errori casuali nelle parole",
    "stutter": "aggiunge balbettii casuali",
    "leet": "converte il testo in leetspeak",
    "rot13": "applica ROT13",
    "base64": "codifica il testo in Base64",
    "binary": "converte il testo in binario UTF-8",
    "morse": "converte lettere e numeri in codice Morse",
}

RANDOM_MODES = tuple(
    mode for mode in MODE_DESCRIPTIONS if mode not in {"random", "original", "fixed"}
)

MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".",
    "F": "..-.", "G": "--.", "H": "....", "I": "..", "J": ".---",
    "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---",
    "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-",
    "U": "..-", "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--",
    "Z": "--..", "0": "-----", "1": ".----", "2": "..---", "3": "...--",
    "4": "....-", "5": ".....", "6": "-....", "7": "--...", "8": "---..",
    "9": "----.", ".": ".-.-.-", ",": "--..--", "?": "..--..", "!": "-.-.--",
}


class Alter(commands.Cog):
    """Ripubblica i messaggi con un webhook usando nome/avatar dell'autore e un effetto configurabile."""

    __author__ = "danyx64"
    __version__ = "1.1.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_ID, force_registration=True)
        self.config.register_guild(
            enabled=False,
            channel_id=None,
            webhook_id=None,
            message_template=DEFAULT_MESSAGE,
            mode="random",
        )
        self._webhook_cache: Dict[int, discord.Webhook] = {}
        self._webhook_locks: Dict[int, asyncio.Lock] = {}
        self._rng = random.SystemRandom()

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
        webhook = await channel.create_webhook(name=WEBHOOK_NAME, reason="Alter cog webhook proxy")
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
    def _render_template(template: str, message: discord.Message) -> str:
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
        return rendered.replace("\\n", "\n").strip()

    @staticmethod
    def _mock(text: str) -> str:
        upper = True
        output = []
        for char in text:
            if char.isalpha():
                output.append(char.upper() if upper else char.lower())
                upper = not upper
            else:
                output.append(char)
        return "".join(output)

    def _spoiler_random(self, text: str) -> str:
        return re.sub(
            r"\S+",
            lambda m: f"||{m.group(0)}||" if self._rng.random() < 0.65 else m.group(0),
            text,
        )

    def _redact(self, text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            word = match.group(0)
            if self._rng.random() < 0.55:
                return "█" * min(max(len(word), 3), 12)
            return word

        return re.sub(r"\S+", repl, text)

    def _typo(self, text: str) -> str:
        def mutate(word: str) -> str:
            if len(word) < 4 or self._rng.random() > 0.45:
                return word
            chars = list(word)
            action = self._rng.choice(("drop", "swap", "double"))
            index = self._rng.randrange(1, len(chars) - 1)
            if action == "drop":
                del chars[index]
            elif action == "swap" and index + 1 < len(chars):
                chars[index], chars[index + 1] = chars[index + 1], chars[index]
            else:
                chars.insert(index, chars[index])
            return "".join(chars)

        return re.sub(r"\b[^\s]+\b", lambda m: mutate(m.group(0)), text)

    def _stutter(self, text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            word = match.group(0)
            if len(word) < 2 or self._rng.random() > 0.45:
                return word
            repeats = self._rng.randint(1, 2)
            return (word[0] + "-") * repeats + word

        return re.sub(r"\b\w+\b", repl, text)

    @staticmethod
    def _leet(text: str) -> str:
        table = str.maketrans({
            "a": "4", "A": "4", "e": "3", "E": "3", "i": "1", "I": "1",
            "o": "0", "O": "0", "s": "5", "S": "5", "t": "7", "T": "7",
            "b": "8", "B": "8", "g": "9", "G": "9",
        })
        return text.translate(table)

    @staticmethod
    def _morse(text: str) -> str:
        words = []
        for word in text.upper().split():
            encoded = [MORSE.get(char, char) for char in word]
            words.append(" ".join(encoded))
        return " / ".join(words)

    def _transform_text(self, mode: str, message: discord.Message) -> str:
        text = message.content or ""
        attachments = " ".join(a.url for a in message.attachments)

        if mode == "random":
            mode = self._rng.choice(RANDOM_MODES)

        if mode == "original":
            transformed = text
        elif mode == "fixed":
            transformed = DEFAULT_MESSAGE
        elif mode == "spoiler":
            transformed = f"||{text}||" if text else ""
        elif mode == "spoilerwords":
            transformed = re.sub(r"\S+", lambda m: f"||{m.group(0)}||", text)
        elif mode == "spoilerrandom":
            transformed = self._spoiler_random(text)
        elif mode == "mock":
            transformed = self._mock(text)
        elif mode == "reverse":
            transformed = text[::-1]
        elif mode == "wordreverse":
            transformed = re.sub(r"\S+", lambda m: m.group(0)[::-1], text)
        elif mode == "shuffle":
            words = text.split()
            self._rng.shuffle(words)
            transformed = " ".join(words)
        elif mode == "redact":
            transformed = self._redact(text)
        elif mode == "typo":
            transformed = self._typo(text)
        elif mode == "stutter":
            transformed = self._stutter(text)
        elif mode == "leet":
            transformed = self._leet(text)
        elif mode == "rot13":
            transformed = codecs.encode(text, "rot_13")
        elif mode == "base64":
            transformed = base64.b64encode(text.encode("utf-8")).decode("ascii") if text else ""
        elif mode == "binary":
            transformed = " ".join(f"{byte:08b}" for byte in text.encode("utf-8"))
        elif mode == "morse":
            transformed = self._morse(text)
        else:
            transformed = text

        if attachments:
            transformed = (transformed + "\n" + attachments).strip()
        return transformed[:2000] or "\u200b"

    async def _render_message(self, message: discord.Message) -> str:
        conf = self.config.guild(message.guild)
        mode = str(await conf.mode() or "random").lower()
        if mode == "fixed":
            template = str(await conf.message_template() or DEFAULT_MESSAGE)
            rendered = self._render_template(template, message)
            if message.attachments and "{attachments}" not in template:
                rendered = (rendered + "\n" + " ".join(a.url for a in message.attachments)).strip()
            return rendered[:2000] or "\u200b"
        return self._transform_text(mode, message)

    async def _send_as_author(
        self,
        webhook: discord.Webhook,
        message: discord.Message,
        content: str,
    ) -> None:
        username = message.author.display_name.strip()[:80] or message.author.name[:80]
        await webhook.send(
            content=content,
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

        try:
            ctx = await self.bot.get_context(message)
            if ctx.valid:
                return
        except Exception:
            pass

        webhook = await self._ensure_webhook(message.channel)
        if webhook is None:
            return

        content = await self._render_message(message)

        try:
            await self._send_as_author(webhook, message, content)
        except discord.NotFound:
            self._webhook_cache.pop(message.guild.id, None)
            await conf.webhook_id.set(None)
            webhook = await self._ensure_webhook(message.channel)
            if webhook is None:
                return
            try:
                await self._send_as_author(webhook, message, content)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                log.warning("Alter webhook retry failed in guild %s: %r", message.guild.id, exc)
                return
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Alter webhook send failed in guild %s: %r", message.guild.id, exc)
            return

        try:
            await message.delete()
        except discord.NotFound:
            pass
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning(
                "Alter could not delete message %s in guild %s: %r",
                message.id,
                message.guild.id,
                exc,
            )

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
        if old_channel_id == channel.id:
            webhook = await self._ensure_webhook(channel)
            if webhook is None:
                return await ctx.send("❌ Non sono riuscito a creare/trovare il webhook Alter.")
            await conf.enabled.set(True)
            return await ctx.send(
                f"✅ Alter attivato in {channel.mention}. Webhook riutilizzato: **{webhook.name}** (`{webhook.id}`)."
            )

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

    @alter.command(name="mode", aliases=["modalita", "effetto"])
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_mode(self, ctx: commands.Context, mode: Optional[str] = None) -> None:
        """Mostra o cambia la modalita di Alter."""
        conf = self.config.guild(ctx.guild)
        if mode is None:
            current = str(await conf.mode() or "random")
            return await ctx.send(
                f"Modalita attuale: **{current}**. Usa `.alter modes` per vedere tutte le opzioni."
            )

        selected = mode.strip().lower()
        aliases = {
            "spoilerall": "spoiler",
            "spoiler-all": "spoiler",
            "spoiler-words": "spoilerwords",
            "spoiler-random": "spoilerrandom",
            "word-reverse": "wordreverse",
            "b64": "base64",
        }
        selected = aliases.get(selected, selected)
        if selected not in MODE_DESCRIPTIONS:
            return await ctx.send(
                "❌ Modalita sconosciuta. Usa `.alter modes` per vedere tutte le opzioni."
            )

        await conf.mode.set(selected)
        await ctx.send(f"✅ Modalita Alter impostata su **{selected}** — {MODE_DESCRIPTIONS[selected]}.")

    @alter.command(name="modes", aliases=["modalita-lista", "effetti", "listamode"])
    async def alter_modes(self, ctx: commands.Context) -> None:
        """Elenca tutte le modalita disponibili."""
        current = str(await self.config.guild(ctx.guild).mode() or "random")
        lines = []
        for mode, description in MODE_DESCRIPTIONS.items():
            marker = "👉" if mode == current else "•"
            lines.append(f"{marker} `{mode}` — {description}")
        embed = discord.Embed(
            title="Alter - modalita disponibili",
            description="\n".join(lines),
            colour=discord.Colour.blurple(),
        )
        embed.set_footer(text="Cambia con: .alter mode <modalita>")
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @alter.command(name="messaggio", aliases=["message", "testo"])
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_message(self, ctx: commands.Context, *, testo: Optional[str] = None) -> None:
        """Imposta il template usato dalla modalita fixed."""
        conf = self.config.guild(ctx.guild)
        if testo is None:
            current = str(await conf.message_template() or DEFAULT_MESSAGE)
            return await ctx.send(
                "**Messaggio della modalita `fixed`**\n"
                f"```\n{current}\n```\n"
                "Placeholder: `{content}` `{name}` `{username}` `{mention}` `{id}` "
                "`{channel}` `{server}` `{attachments}`.\n"
                "Reset: `.alter messaggio reset`."
            )

        template = str(testo).strip()
        if template.lower() in {"reset", "default", "predefinito"}:
            await conf.message_template.set(DEFAULT_MESSAGE)
            return await ctx.send(f"✅ Messaggio `fixed` ripristinato a: `{DEFAULT_MESSAGE}`")
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
        await ctx.send("✅ Messaggio `fixed` aggiornato. Attivalo con `.alter mode fixed`.")

    @alter.command(name="test", aliases=["prova", "preview"])
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_test(self, ctx: commands.Context, mode: Optional[str] = None) -> None:
        """Invia un test usando il tuo nome/avatar; opzionalmente prova una modalita specifica."""
        conf = self.config.guild(ctx.guild)
        channel = ctx.guild.get_channel((await conf.channel_id()) or 0)
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send("❌ Prima usa `.alter setup #canale`.")

        webhook = await self._ensure_webhook(channel)
        if webhook is None:
            return await ctx.send("❌ Webhook Alter non disponibile.")

        if mode is None:
            content = await self._render_message(ctx.message)
            used_mode = str(await conf.mode() or "random")
        else:
            used_mode = mode.strip().lower()
            if used_mode not in MODE_DESCRIPTIONS:
                return await ctx.send("❌ Modalita non valida. Usa `.alter modes`.")
            if used_mode == "fixed":
                content = self._render_template(str(await conf.message_template() or DEFAULT_MESSAGE), ctx.message)
            else:
                content = self._transform_text(used_mode, ctx.message)

        try:
            await self._send_as_author(webhook, ctx.message, content[:2000] or "\u200b")
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            return await ctx.send(f"❌ Invio test fallito: `{type(exc).__name__}`")
        await ctx.send(f"✅ Test **{used_mode}** inviato in {channel.mention}.")

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

        mode = str(settings.get("mode") or "random")
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
        embed.add_field(name="Modalita", value=f"**{mode}**", inline=True)
        embed.add_field(name="Descrizione", value=MODE_DESCRIPTIONS.get(mode, "-"), inline=False)
        if mode == "fixed":
            embed.add_field(name="Messaggio fixed", value=f"```\n{template[:950]}\n```", inline=False)
        embed.set_footer(text=f"Alter v{self.__version__}")
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @alter.command(name="remove", aliases=["rimuovi", "reset"])
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_remove(self, ctx: commands.Context) -> None:
        """Disattiva Alter, elimina il webhook e resetta la configurazione."""
        conf = self.config.guild(ctx.guild)
        await conf.enabled.set(False)
        await self._delete_configured_webhook(ctx.guild)
        await conf.channel_id.set(None)
        await conf.webhook_id.set(None)
        await conf.message_template.set(DEFAULT_MESSAGE)
        await conf.mode.set("random")
        self._webhook_cache.pop(ctx.guild.id, None)
        await ctx.send("✅ Alter rimosso: webhook eliminato e configurazione resettata.")

    @alter.command(name="version", aliases=["versione"])
    async def alter_version(self, ctx: commands.Context) -> None:
        await ctx.send(f"Alter **v{self.__version__}**.")
