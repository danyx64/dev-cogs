import asyncio
import logging
from collections import defaultdict

import discord
from redbot.core import Config, commands

LOG = logging.getLogger("red.quarantena")


class Quarantena(commands.Cog):
    """Quarantena automatica con parole configurabili."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=918423735521, force_registration=True)
        self.config.register_guild(enabled=False, words=[], role_id=0, counter=0, isolated=[])
        self._locks = defaultdict(asyncio.Lock)

    @staticmethod
    def matches(text, words):
        text = (text or "").casefold()
        return any(w.casefold() in text for w in words if w.strip())

    def name_matches(self, member, words):
        return any(self.matches(s, words) for s in (member.nick, member.name, member.global_name))

    async def isolate(self, member, reason):
        guild = member.guild
        if member.bot or member == guild.owner or member.guild_permissions.administrator:
            return
        async with self._locks[guild.id]:
            cfg = self.config.guild(guild)
            data = await cfg.all()
            if not data["enabled"] or member.id in data["isolated"]:
                return
            role = guild.get_role(data["role_id"])
            me = guild.me
            if not role or not me or not me.guild_permissions.manage_roles:
                LOG.warning("Quarantena: ruolo o permessi mancanti in %s", guild.id)
                return
            if role >= me.top_role or member.top_role >= me.top_role:
                return
            number = data["counter"] + 1
            await cfg.counter.set(number)
            if me.guild_permissions.manage_nicknames:
                try:
                    await member.edit(nick=f"User{number}", reason=reason)
                except (discord.Forbidden, discord.HTTPException):
                    LOG.exception("Rinomina fallita per %s", member.id)
            # Replace all removable roles with the quarantine role in one request.
            # @everyone is implicit; managed roles and roles above the bot
            # cannot be removed through the Discord API.
            retained = [
                existing for existing in member.roles
                if existing != guild.default_role
                and existing != role
                and (existing.managed or existing >= me.top_role)
            ]
            desired_roles = retained + [role]
            try:
                await member.edit(roles=desired_roles, reason=reason)
            except (discord.Forbidden, discord.HTTPException):
                LOG.exception("Sostituzione ruoli fallita per %s", member.id)
                return
            if retained:
                LOG.warning(
                    "Utente %s: alcuni ruoli non rimovibili sono rimasti: %s",
                    member.id,
                    [existing.id for existing in retained],
                )
            async with cfg.isolated() as isolated:
                if member.id not in isolated:
                    isolated.append(member.id)
        await self.delete_history(member)

    async def delete_history(self, member):
        guild = member.guild
        for channel in [*guild.text_channels, *guild.threads]:
            perms = channel.permissions_for(guild.me)
            if not (perms.view_channel and perms.read_message_history and perms.manage_messages):
                continue
            try:
                async for msg in channel.history(limit=None):
                    if msg.author.id == member.id:
                        try:
                            await msg.delete(reason="Quarantena automatica")
                        except discord.NotFound:
                            pass
                        except (discord.Forbidden, discord.HTTPException):
                            LOG.warning("Errore cancellazione messaggio %s", msg.id)
            except (discord.Forbidden, discord.HTTPException):
                LOG.warning("Cronologia non accessibile in %s", channel.id)

    async def check_message(self, message):
        if not message.guild or not isinstance(message.author, discord.Member):
            return
        data = await self.config.guild(message.guild).all()
        if data["enabled"] and self.matches(message.content, data["words"]):
            # Delete the violating message immediately, even if the user
            # was previously quarantined or the history sweep cannot run.
            try:
                await message.delete()
            except discord.NotFound:
                pass
            except (discord.Forbidden, discord.HTTPException):
                LOG.warning("Cannot delete violating message %s", message.id)
            await self.isolate(message.author, "Parola vietata nel messaggio")

    @commands.Cog.listener()
    async def on_message(self, message):
        await self.check_message(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.content != after.content:
            await self.check_message(after)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload):
        if not payload.guild_id or "content" not in payload.data:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        data = await self.config.guild(guild).all()
        if not data["enabled"] or not self.matches(payload.data.get("content"), data["words"]):
            return
        channel = guild.get_channel(payload.channel_id) or self.bot.get_channel(payload.channel_id)
        if not channel:
            return
        try:
            msg = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
            return
        await self.check_message(msg)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        data = await self.config.guild(member.guild).all()
        if data["enabled"] and self.name_matches(member, data["words"]):
            await self.isolate(member, "Nome vietato all'ingresso")

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if (before.nick, before.name, before.global_name) == (after.nick, after.name, after.global_name):
            return
        data = await self.config.guild(after.guild).all()
        if data["enabled"] and self.name_matches(after, data["words"]):
            await self.isolate(after, "Nome o nickname vietato")

    @commands.group(name="quarantena", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def quarantena(self, ctx):
        await ctx.send("Comandi: ruolo <ID>, aggiungi <frase>, rimuovi <frase>, lista, blocca/sblocca/consenti <ID ruolo> <ID canale>, attiva, disattiva, stato")

    @quarantena.command(name="ruolo")
    async def role(self, ctx, role_id: int):
        role = ctx.guild.get_role(role_id)
        if not role:
            return await ctx.send("Ruolo non trovato.")
        await self.config.guild(ctx.guild).role_id.set(role_id)
        await ctx.send(f"Ruolo impostato: {role.mention}")

    @quarantena.command(name="aggiungi")
    async def add(self, ctx, *, word: str):
        word = word.strip()
        if not 1 <= len(word) <= 100:
            return await ctx.send("La parola o frase deve contenere 1-100 caratteri.")
        async with self.config.guild(ctx.guild).words() as words:
            if any(w.casefold() == word.casefold() for w in words):
                return await ctx.send("Parola gia presente.")
            words.append(word)
        await ctx.send("Parola aggiunta.")

    @quarantena.command(name="rimuovi")
    async def remove(self, ctx, *, word: str):
        async with self.config.guild(ctx.guild).words() as words:
            found = next((w for w in words if w.casefold() == word.strip().casefold()), None)
            if found is None:
                return await ctx.send("Parola assente dalla lista.")
            words.remove(found)
        await ctx.send("Parola rimossa.")

    @quarantena.command(name="lista")
    async def list_words(self, ctx):
        words = await self.config.guild(ctx.guild).words()
        listing = ", ".join(discord.utils.escape_markdown(w) for w in words)[:1750] if words else "(vuota)"
        await ctx.send("Parole vietate: " + listing)

    @quarantena.command(name="attiva")
    async def enable(self, ctx):
        if not await self.config.guild(ctx.guild).role_id():
            return await ctx.send("Imposta prima il ruolo con quarantena ruolo <ID>.")
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("Quarantena attiva.")

    @quarantena.command(name="disattiva")
    async def disable(self, ctx):
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("Quarantena disattivata.")

    @quarantena.command(name="blocca")
    async def block_channel(self, ctx, role_id: int, channel_id: int):
        guild = ctx.guild
        role = guild.get_role(role_id)
        channel = guild.get_channel(channel_id)
        if not role or role.is_default():
            return await ctx.send("Ruolo non valido (non usare @everyone).")
        if not channel:
            return await ctx.send("Canale o categoria non trovato.")
        me = guild.me
        if not me or not me.guild_permissions.manage_roles or role >= me.top_role:
            return await ctx.send("Il bot non ha permessi o gerarchia sufficienti.")
        if not channel.permissions_for(me).manage_roles:
            return await ctx.send("Il bot non puo modificare questo canale.")
        overwrite = channel.overwrites_for(role)
        for name, enabled in discord.Permissions.all_channel():
            if enabled:
                setattr(overwrite, name, False)
        try:
            await channel.set_permissions(role, overwrite=overwrite, reason=f"Quarantena: {ctx.author.id}")
        except (discord.Forbidden, discord.HTTPException):
            return await ctx.send("Non riesco a impostare i permessi.")
        await ctx.send(f"Permessi di {role.mention} negati su {channel.name}. Altri ruoli possono consentire l'accesso.")

    @quarantena.command(name="sblocca")
    async def unblock_channel(self, ctx, role_id: int, channel_id: int):
        """Remove the channel-specific denies set for a role."""
        role = ctx.guild.get_role(role_id)
        channel = ctx.guild.get_channel(channel_id)
        if role is None or role.is_default() or channel is None:
            return await ctx.send("ID ruolo o canale non valido.")
        me = ctx.guild.me
        if not me or not channel.permissions_for(me).manage_roles:
            return await ctx.send("Il bot non ha Gestisci ruoli su questo canale.")
        overwrite = channel.overwrites_for(role)
        for name, _ in discord.Permissions.all_channel():
            setattr(overwrite, name, None)
        try:
            await channel.set_permissions(
                role, overwrite=overwrite, reason=f"Quarantena sblocca: {ctx.author.id}"
            )
        except (discord.Forbidden, discord.HTTPException):
            return await ctx.send("Non riesco a ripristinare i permessi: verifica i permessi del bot.")
        await ctx.send(
            f"Permessi espliciti di {role.mention} azzerati su {channel.name}. "
            "Ora eredita quelli da @everyone e dagli altri ruoli."
        )

    @quarantena.command(name="consenti")
    async def allow_channel(self, ctx, role_id: int, channel_id: int):
        """Allow standard access and chat for a role on one channel."""
        role = ctx.guild.get_role(role_id)
        channel = ctx.guild.get_channel(channel_id)
        if role is None or role.is_default() or channel is None:
            return await ctx.send("ID ruolo o canale non valido.")
        me = ctx.guild.me
        if not me or not channel.permissions_for(me).manage_roles:
            return await ctx.send("Il bot non ha Gestisci ruoli su questo canale.")
        overwrite = channel.overwrites_for(role)
        for name, _ in discord.Permissions.all_channel():
            setattr(overwrite, name, None)
        overwrite.view_channel = True
        if isinstance(channel, (discord.TextChannel, discord.CategoryChannel)):
            overwrite.send_messages = True
            overwrite.read_message_history = True
        if isinstance(channel, discord.VoiceChannel):
            overwrite.connect = True
            overwrite.speak = True
        try:
            await channel.set_permissions(
                role, overwrite=overwrite, reason=f"Quarantena consenti: {ctx.author.id}"
            )
        except (discord.Forbidden, discord.HTTPException):
            return await ctx.send("Non riesco a concedere accesso: verifica i permessi del bot.")
        await ctx.send(f"Accesso standard consentito a {role.mention} su {channel.name}.")

    @quarantena.command(name="stato")
    async def status(self, ctx):
        data = await self.config.guild(ctx.guild).all()
        await ctx.send(f"Attiva: {data['enabled']} | Ruolo: {data['role_id']} | Parole: {len(data['words'])} | Prossimo nick: User{data['counter']+1} | Isolati: {len(data['isolated'])}")
