import re
from datetime import timezone
from zoneinfo import ZoneInfo

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


ITALY_TZ = ZoneInfo("Europe/Rome")
DURATION_RE = re.compile(r"^(\d+)(s|m|h|d|w)$", re.I)


class AgeGuard(commands.Cog):
    """Espelle automaticamente gli account Discord troppo recenti."""

    __author__ = "danyx64"
    __version__ = "1.0.1"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=937410526481773204, force_registration=True)
        self.config.register_guild(
            enabled=False,
            min_age_seconds=7 * 24 * 3600,
            log_channel_id=None,
            dm_message=(
                "Ciao {user}, sei stato rimosso da {guild} perché il tuo account Discord "
                "è troppo recente. Età account: {account_age}. Minimo richiesto: {min_age}."
            ),
            log_message=(
                "Utente: {user}\n"
                "ID: {user_id}\n"
                "Età account: {account_age}\n"
                "Creato: {account_created}\n"
                "Minimo richiesto: {min_age}\n"
                "DM: {dm_status}\n"
                "Azione: Kick account troppo recente\n"
                "Data: {date}\n"
                "Ora: {time}"
            ),
            history=[],
        )

    @staticmethod
    def _format_duration(seconds: int) -> str:
        seconds = max(0, int(seconds))
        weeks, seconds = divmod(seconds, 604800)
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        parts = []
        if weeks:
            parts.append(f"{weeks}w")
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if seconds and not parts:
            parts.append(f"{seconds}s")
        return " ".join(parts) or "0s"

    @staticmethod
    def _parse_duration(value: str):
        match = DURATION_RE.fullmatch(value.strip())
        if not match:
            return None
        amount = int(match.group(1))
        unit = match.group(2).lower()
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
        return amount * mult

    def _placeholders(self, member: discord.Member, min_age_seconds: int, dm_status: str = "—"):
        now = discord.utils.utcnow()
        created = member.created_at
        age_seconds = max(0, int((now - created).total_seconds()))
        local_now = now.astimezone(ITALY_TZ)
        local_created = created.astimezone(ITALY_TZ)
        return {
            "user": member.mention,
            "mention": member.mention,
            "user_mention": member.mention,
            "username": member.name,
            "displayname": member.display_name,
            "user_tag": str(member),
            "user_id": str(member.id),
            "user_avatar": str(member.display_avatar.url),
            "guild": member.guild.name,
            "server": member.guild.name,
            "guild_id": str(member.guild.id),
            "server_id": str(member.guild.id),
            "member_count": str(member.guild.member_count or len(member.guild.members)),
            "account_created": local_created.strftime("%d/%m/%Y %H:%M:%S"),
            "account_age": self._format_duration(age_seconds),
            "account_age_seconds": str(age_seconds),
            "min_age": self._format_duration(min_age_seconds),
            "min_age_seconds": str(min_age_seconds),
            "dm_status": dm_status,
            "date": local_now.strftime("%d/%m/%Y"),
            "time": local_now.strftime("%H:%M:%S"),
            "datetime": local_now.strftime("%d/%m/%Y %H:%M:%S"),
        }

    @staticmethod
    def _render(template: str, values: dict) -> str:
        text = str(template)
        for key, value in values.items():
            text = text.replace("{" + key + "}", str(value))
        return text

    async def _append_history(self, guild: discord.Guild, member: discord.Member, age_seconds: int, dm_sent: bool):
        history = await self.config.guild(guild).history()
        now = discord.utils.utcnow()
        history.append({
            "user_id": member.id,
            "username": str(member),
            "age_seconds": age_seconds,
            "created_at": member.created_at.isoformat(),
            "kicked_at": now.isoformat(),
            "dm_sent": dm_sent,
        })
        await self.config.guild(guild).history.set(history[-100:])

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        guild = member.guild
        data = await self.config.guild(guild).all()
        if not data["enabled"]:
            return

        min_age = int(data["min_age_seconds"])
        now = discord.utils.utcnow()
        age_seconds = int((now - member.created_at).total_seconds())
        if age_seconds >= min_age:
            return

        me = guild.me
        if me is None or not me.guild_permissions.kick_members:
            return
        if member == guild.owner or member.top_role >= me.top_role:
            return

        dm_sent = False
        values = self._placeholders(member, min_age, "—")
        dm_text = self._render(data["dm_message"], values)[:2000]
        try:
            await member.send(dm_text, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
            dm_sent = True
        except (discord.Forbidden, discord.HTTPException):
            dm_sent = False

        try:
            await member.kick(reason=f"AgeGuard: account age {age_seconds}s < required {min_age}s")
        except (discord.Forbidden, discord.HTTPException):
            return

        await self._append_history(guild, member, age_seconds, dm_sent)

        channel_id = data.get("log_channel_id")
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            values = self._placeholders(member, min_age, "inviato" if dm_sent else "non inviato")
            log_text = self._render(data["log_message"], values)
            embed = discord.Embed(description=log_text[:4096], colour=discord.Colour.orange())
            try:
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                pass

    @commands.group(name="ageguard", aliases=["ag"], invoke_without_command=True)
    @commands.guild_only()
    async def ageguard(self, ctx: commands.Context):
        """Configura la protezione contro account Discord troppo recenti."""
        await ctx.send_help(ctx.command)

    @ageguard.command(name="enable")
    @commands.admin_or_permissions(administrator=True)
    async def ag_enable(self, ctx: commands.Context):
        """Abilita AgeGuard nel server."""
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("AgeGuard abilitato.")

    @ageguard.command(name="disable")
    @commands.admin_or_permissions(administrator=True)
    async def ag_disable(self, ctx: commands.Context):
        """Disabilita AgeGuard nel server."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("AgeGuard disabilitato.")

    @ageguard.command(name="minage")
    @commands.admin_or_permissions(administrator=True)
    async def ag_minage(self, ctx: commands.Context, duration: str):
        """Imposta l'età minima account: es. `12h`, `3d`, `2w`."""
        seconds = self._parse_duration(duration)
        if seconds is None or seconds < 60:
            return await ctx.send("Durata non valida. Usa ad esempio `12h`, `3d`, `2w` (minimo 1m).")
        if seconds > 365 * 24 * 3600:
            return await ctx.send("Il limite massimo configurabile è 365 giorni.")
        await self.config.guild(ctx.guild).min_age_seconds.set(seconds)
        await ctx.send(f"Età minima impostata a **{self._format_duration(seconds)}**.")

    @ageguard.command(name="setchannel")
    @commands.admin_or_permissions(manage_guild=True)
    async def ag_setchannel(self, ctx: commands.Context, channel_id: int):
        """Imposta il canale dove registrare i kick eseguiti da AgeGuard."""
        channel = ctx.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send("Canale testuale non trovato. Usa l'ID del canale.")
        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            return await ctx.send("Mi servono Visualizza canale, Invia messaggi e Incorpora link in quel canale.")
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
        await ctx.send(f"Canale log impostato su {channel.mention} (`{channel.id}`).")

    @ageguard.group(name="message", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def ag_message(self, ctx: commands.Context):
        """Visualizza e modifica i messaggi personalizzabili di AgeGuard."""
        await ctx.send_help(ctx.command)

    @ag_message.command(name="dm")
    async def ag_message_dm(self, ctx: commands.Context, *, text: str):
        """Modifica il DM inviato prima del kick."""
        if len(text) > 2000:
            return await ctx.send("Il messaggio DM può avere massimo 2000 caratteri.")
        await self.config.guild(ctx.guild).dm_message.set(text)
        await ctx.send("Messaggio DM aggiornato.")

    @ag_message.command(name="log")
    async def ag_message_log(self, ctx: commands.Context, *, text: str):
        """Modifica il testo dell'embed inviato nel canale log."""
        if len(text) > 4000:
            return await ctx.send("Il messaggio log può avere massimo 4000 caratteri.")
        await self.config.guild(ctx.guild).log_message.set(text)
        await ctx.send("Messaggio log aggiornato.")

    @ag_message.command(name="view")
    async def ag_message_view(self, ctx: commands.Context):
        """Mostra i messaggi DM e log attualmente configurati."""
        data = await self.config.guild(ctx.guild).all()
        embed = discord.Embed(title="Messaggi AgeGuard", colour=discord.Colour.blurple())
        embed.add_field(name="DM prima del kick", value=data["dm_message"][:1024] or "—", inline=False)
        embed.add_field(name="Log nel canale", value=data["log_message"][:1024] or "—", inline=False)
        await ctx.send(embed=embed)

    @ag_message.command(name="usage")
    async def ag_message_usage(self, ctx: commands.Context):
        """Mostra tutti i placeholder disponibili nei messaggi."""
        text = (
            "`{user}` / `{mention}` / `{user_mention}` → menzione utente\n"
            "`{username}` → username\n"
            "`{displayname}` → nome visualizzato\n"
            "`{user_tag}` → nome Discord completo\n"
            "`{user_id}` → ID utente\n"
            "`{user_avatar}` → URL avatar\n"
            "`{guild}` / `{server}` → nome server\n"
            "`{guild_id}` / `{server_id}` → ID server\n"
            "`{member_count}` → numero membri\n"
            "`{account_created}` → data/ora creazione account\n"
            "`{account_age}` → età account leggibile\n"
            "`{account_age_seconds}` → età account in secondi\n"
            "`{min_age}` → età minima configurata\n"
            "`{min_age_seconds}` → soglia in secondi\n"
            "`{dm_status}` → inviato/non inviato (utile nel log)\n"
            "`{date}` → data\n"
            "`{time}` → ora\n"
            "`{datetime}` → data + ora"
        )
        await ctx.send(embed=discord.Embed(title="Placeholder AgeGuard", description=text, colour=discord.Colour.blurple()))

    @ageguard.command(name="status")
    @commands.admin_or_permissions(manage_guild=True)
    async def ag_status(self, ctx: commands.Context):
        """Mostra configurazione e stato attuale di AgeGuard."""
        data = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(data["log_channel_id"]) if data["log_channel_id"] else None
        await ctx.send(
            f"Stato: **{'attivo' if data['enabled'] else 'disattivato'}**\n"
            f"Età minima: **{self._format_duration(data['min_age_seconds'])}**\n"
            f"Canale log: {channel.mention if channel else '—'}"
        )

    @ageguard.command(name="history")
    @commands.admin_or_permissions(manage_guild=True)
    async def ag_history(self, ctx: commands.Context, amount: int = 10):
        """Mostra gli ultimi account espulsi da AgeGuard."""
        amount = max(1, min(int(amount), 25))
        history = await self.config.guild(ctx.guild).history()
        if not history:
            return await ctx.send("Nessun kick registrato da AgeGuard.")
        for item in reversed(history[-amount:]):
            kicked = discord.utils.parse_time(item["kicked_at"])
            created = discord.utils.parse_time(item["created_at"])
            embed = discord.Embed(colour=discord.Colour.orange())
            embed.description = (
                f"Utente: <@{item['user_id']}>\n"
                f"ID: `{item['user_id']}`\n"
                f"Età account: {self._format_duration(item['age_seconds'])}\n"
                f"Creato: {created.astimezone(ITALY_TZ).strftime('%d/%m/%Y %H:%M:%S') if created else '—'}\n"
                f"DM: {'inviato' if item['dm_sent'] else 'non inviato'}\n"
                f"Data: {kicked.astimezone(ITALY_TZ).strftime('%d/%m/%Y') if kicked else '—'}\n"
                f"Ora: {kicked.astimezone(ITALY_TZ).strftime('%H:%M:%S') if kicked else '—'}"
            )
            await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @ageguard.command(name="test")
    @commands.admin_or_permissions(manage_guild=True)
    async def ag_test(self, ctx: commands.Context, user_id: int = None):
        """Controlla l'età di un membro senza espellerlo."""
        member = ctx.guild.get_member(user_id or ctx.author.id)
        if member is None:
            return await ctx.send("Membro non trovato nel server.")
        min_age = await self.config.guild(ctx.guild).min_age_seconds()
        age_seconds = int((discord.utils.utcnow() - member.created_at).total_seconds())
        result = "PASSA" if age_seconds >= min_age else "VERREBBE ESPULSO"
        await ctx.send(
            f"Utente: {member.mention}\n"
            f"Età account: **{self._format_duration(age_seconds)}**\n"
            f"Minimo: **{self._format_duration(min_age)}**\n"
            f"Esito: **{result}**",
            allowed_mentions=discord.AllowedMentions.none(),
        )
