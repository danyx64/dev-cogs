from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import discord
from redbot.core import Config, checks, commands


class AdaptiveSlowmode(commands.Cog):
    """Slowmode adattivo: regola automaticamente il rallentamento in base all'attivita del canale."""

    __author__ = "BeeHiveSafety / traduzione e adattamento danyx64"
    __version__ = "1.0.0-it"

    # IMPORTANTE: identifier, nome classe e chiavi restano compatibili con
    # BeeHiveSafety/BeeHiveCogs -> adaptiveslowmode, cosi Red riusa la Config esistente.
    DEFAULTS = {
        "enabled": False,
        "min_slowmode": 0,
        "max_slowmode": 120,
        "target_msgs_per_min": 20,
        "channels": [],
        "log_channel": None,
    }

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xBEEBEE01, force_registration=True)
        self.config.register_guild(**self.DEFAULTS)

        self._message_cache: Dict[int, deque] = defaultdict(lambda: deque(maxlen=500))
        self._minute_stats: Dict[int, deque] = defaultdict(lambda: deque(maxlen=5))
        self._lock = asyncio.Lock()
        self._minute_tick = 0
        self._last_log_message: Dict[int, discord.Message] = {}
        self._last_log_view: Dict[int, discord.ui.View] = {}
        self._slowmode_task = self.bot.loop.create_task(self._run_slowmode_task())

    def cog_unload(self):
        if getattr(self, "_slowmode_task", None):
            self._slowmode_task.cancel()

    @staticmethod
    def _seconds_text(value: int) -> str:
        return f"{value} secondo" if value == 1 else f"{value} secondi"

    @staticmethod
    def _messages_text(value: int) -> str:
        return f"{value} messaggio" if value == 1 else f"{value} messaggi"

    @staticmethod
    def _bot_can_edit(channel: discord.TextChannel) -> bool:
        me = channel.guild.me
        if me is None:
            return False
        perms = channel.permissions_for(me)
        return perms.view_channel and perms.manage_channels

    @commands.group(name="adaptiveslowmode", aliases=["slowmodeadattivo", "asm"], invoke_without_command=True)
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def adaptiveslowmode(self, ctx: commands.Context):
        """Configura lo slowmode adattivo."""
        await ctx.send_help(ctx.command)

    @adaptiveslowmode.command(name="enable", aliases=["attiva", "on"])
    @checks.admin_or_permissions(manage_guild=True)
    async def enable(self, ctx: commands.Context):
        """Attiva lo slowmode adattivo nel server."""
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send(
            embed=discord.Embed(
                title="Slowmode adattivo",
                description="✅ Slowmode adattivo attivato.",
                color=0x2BBD8E,
            )
        )

    @adaptiveslowmode.command(name="disable", aliases=["disattiva", "off"])
    @checks.admin_or_permissions(manage_guild=True)
    async def disable(self, ctx: commands.Context):
        """Disattiva lo slowmode adattivo nel server."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send(
            embed=discord.Embed(
                title="Slowmode adattivo",
                description="⏸️ Slowmode adattivo disattivato.",
                color=0xFF4545,
            )
        )

    @adaptiveslowmode.command(name="min", aliases=["minimo"])
    @checks.admin_or_permissions(manage_guild=True)
    async def minimum(self, ctx: commands.Context, seconds: int):
        """Imposta lo slowmode minimo in secondi."""
        if seconds < 0 or seconds > 21600:
            return await ctx.send("❌ Il minimo deve essere compreso tra 0 e 21600 secondi.")

        current_max = await self.config.guild(ctx.guild).max_slowmode()
        if seconds > current_max:
            return await ctx.send(f"❌ Il minimo non puo superare il massimo attuale di **{current_max}** secondi.")

        await self.config.guild(ctx.guild).min_slowmode.set(seconds)
        await ctx.send(
            embed=discord.Embed(
                title="Slowmode adattivo",
                description=f"Slowmode minimo impostato a **{self._seconds_text(seconds)}**.",
                color=discord.Color.blue(),
            )
        )

    @adaptiveslowmode.command(name="max", aliases=["massimo"])
    @checks.admin_or_permissions(manage_guild=True)
    async def maximum(self, ctx: commands.Context, seconds: int):
        """Imposta lo slowmode massimo in secondi."""
        if seconds < 0 or seconds > 21600:
            return await ctx.send("❌ Il massimo deve essere compreso tra 0 e 21600 secondi.")

        current_min = await self.config.guild(ctx.guild).min_slowmode()
        if seconds < current_min:
            return await ctx.send(f"❌ Il massimo non puo essere inferiore al minimo attuale di **{current_min}** secondi.")

        await self.config.guild(ctx.guild).max_slowmode.set(seconds)
        await ctx.send(
            embed=discord.Embed(
                title="Slowmode adattivo",
                description=f"Slowmode massimo impostato a **{self._seconds_text(seconds)}**.",
                color=discord.Color.blue(),
            )
        )

    @adaptiveslowmode.command(name="target", aliases=["obiettivo"])
    @checks.admin_or_permissions(manage_guild=True)
    async def target(self, ctx: commands.Context, msgs_per_min: int):
        """Imposta il numero obiettivo di messaggi al minuto."""
        if msgs_per_min < 1:
            return await ctx.send("❌ L'obiettivo deve essere almeno **1 messaggio al minuto**.")

        await self.config.guild(ctx.guild).target_msgs_per_min.set(msgs_per_min)
        await ctx.send(
            embed=discord.Embed(
                title="Slowmode adattivo",
                description=f"Obiettivo impostato a **{self._messages_text(msgs_per_min)} al minuto**.",
                color=discord.Color.blue(),
            )
        )

    @adaptiveslowmode.command(name="add", aliases=["aggiungi"])
    @checks.admin_or_permissions(manage_guild=True)
    async def add(self, ctx: commands.Context, channel: discord.TextChannel):
        """Aggiunge un canale allo slowmode adattivo."""
        if not self._bot_can_edit(channel):
            return await ctx.send("❌ Mi serve il permesso **Gestisci canali** in quel canale.")

        async with self.config.guild(ctx.guild).channels() as channels:
            if channel.id not in channels:
                channels.append(channel.id)

        await ctx.send(
            embed=discord.Embed(
                title="Slowmode adattivo",
                description=f"✅ {channel.mention} aggiunto allo slowmode adattivo.",
                color=0x2BBD8E,
            )
        )

    @adaptiveslowmode.command(name="remove", aliases=["rimuovi"])
    @checks.admin_or_permissions(manage_guild=True)
    async def remove(self, ctx: commands.Context, channel: discord.TextChannel):
        """Rimuove un canale dallo slowmode adattivo."""
        async with self.config.guild(ctx.guild).channels() as channels:
            if channel.id in channels:
                channels.remove(channel.id)

        await ctx.send(
            embed=discord.Embed(
                title="Slowmode adattivo",
                description=f"🗑️ {channel.mention} rimosso dallo slowmode adattivo.",
                color=0xFF4545,
            )
        )

    @adaptiveslowmode.command(name="settings", aliases=["impostazioni", "status", "stato"])
    @checks.admin_or_permissions(manage_guild=True)
    async def settings(self, ctx: commands.Context):
        """Mostra configurazione e stato attuali."""
        conf = await self.config.guild(ctx.guild).all()
        log_channel = ctx.guild.get_channel(conf["log_channel"]) if conf["log_channel"] else None
        channels = [ctx.guild.get_channel(cid) for cid in conf["channels"]]
        channel_mentions = [channel.mention for channel in channels if isinstance(channel, discord.TextChannel)]

        embed = discord.Embed(title="Impostazioni slowmode adattivo", color=0xFFFFFE)
        embed.add_field(name="Stato", value="✅ Attivo" if conf["enabled"] else "⏸️ Disattivato", inline=False)
        embed.add_field(name="Slowmode minimo", value=self._seconds_text(conf["min_slowmode"]), inline=True)
        embed.add_field(name="Slowmode massimo", value=self._seconds_text(conf["max_slowmode"]), inline=True)
        embed.add_field(name="Obiettivo", value=f"{conf['target_msgs_per_min']} messaggi/min", inline=True)
        embed.add_field(name="Canale log", value=log_channel.mention if log_channel else "Nessuno", inline=False)
        embed.add_field(name="Canali monitorati", value="\n".join(channel_mentions) if channel_mentions else "Nessun canale configurato", inline=False)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @adaptiveslowmode.command(name="logs", aliases=["log"])
    @checks.admin_or_permissions(manage_guild=True)
    async def logs(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Imposta il canale dei log; senza canale lo rimuove."""
        if channel is None:
            await self.config.guild(ctx.guild).log_channel.set(None)
            await ctx.send(
                embed=discord.Embed(
                    title="Slowmode adattivo",
                    description="Canale log rimosso.",
                    color=discord.Color.orange(),
                )
            )
            return

        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(
            embed=discord.Embed(
                title="Slowmode adattivo",
                description=f"✅ Canale log impostato su {channel.mention}.",
                color=0x2BBD8E,
            )
        )

    async def _send_log(self, guild: discord.Guild, embed: discord.Embed, view: Optional[discord.ui.View] = None):
        log_channel_id = await self.config.guild(guild).log_channel()
        if not log_channel_id:
            return

        log_channel = guild.get_channel(log_channel_id)
        if not isinstance(log_channel, discord.TextChannel):
            return

        me = guild.me
        if me is None or not log_channel.permissions_for(me).send_messages:
            return

        channel_id = None
        if view is not None and hasattr(view, "channel"):
            channel_id = view.channel.id

        if channel_id is not None:
            previous = self._last_log_message.get(channel_id)
            previous_view = self._last_log_view.get(channel_id)
            if previous is not None and previous_view is not None:
                try:
                    for item in previous_view.children:
                        item.disabled = True
                    await previous.edit(view=previous_view)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

        try:
            sent = await log_channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            return

        if channel_id is not None:
            self._last_log_message[channel_id] = sent
            if view is not None:
                self._last_log_view[channel_id] = view

    @adaptiveslowmode.command(name="survey", aliases=["sondaggio", "calibra"])
    @checks.admin_or_permissions(manage_guild=True)
    async def survey(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Misura 5 minuti di attivita e calibra automaticamente i valori."""
        channel = channel or ctx.channel
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send("❌ Questo comando deve essere usato in un canale testuale.")

        await ctx.send(
            embed=discord.Embed(
                title="Calibrazione attivita in corso",
                description=f"Sto misurando per **5 minuti** l'attivita di {channel.mention}. Al termine impostero automaticamente i valori consigliati.",
                color=0x2BBD8E,
            )
        )

        start = datetime.now(timezone.utc)
        count = 0

        def check(message: discord.Message):
            return message.channel.id == channel.id and not message.author.bot and message.created_at >= start

        while (datetime.now(timezone.utc) - start).total_seconds() < 300:
            remaining = 300 - (datetime.now(timezone.utc) - start).total_seconds()
            if remaining <= 0:
                break
            try:
                await self.bot.wait_for("message", timeout=remaining, check=check)
                count += 1
            except asyncio.TimeoutError:
                break

        msgs_per_min = count / 5
        if msgs_per_min > 60:
            min_slow, max_slow = 2, 10
        elif msgs_per_min > 20:
            min_slow, max_slow = 0, 10
        else:
            min_slow, max_slow = 0, 5

        conf = self.config.guild(ctx.guild)
        await conf.target_msgs_per_min.set(max(1, int(msgs_per_min)))
        await conf.min_slowmode.set(min_slow)
        await conf.max_slowmode.set(max_slow)
        async with conf.channels() as channels:
            if channel.id not in channels:
                channels.append(channel.id)

        result = (
            f"**Canale:** {channel.mention}\n"
            f"**Messaggi negli ultimi 5 minuti:** {count}\n"
            f"**Media:** {msgs_per_min:.2f} messaggi/min\n"
            f"**Nuovo obiettivo:** {max(1, int(msgs_per_min))} messaggi/min\n"
            f"**Slowmode minimo:** {min_slow}s\n"
            f"**Slowmode massimo:** {max_slow}s\n\n"
            "Il canale e stato aggiunto allo slowmode adattivo."
        )
        embed = discord.Embed(title="Risultati calibrazione", description=result, color=0x2BBD8E)
        await ctx.send(embed=embed)
        await self._send_log(ctx.guild, embed)

    @adaptiveslowmode.command(name="version", aliases=["versione"])
    async def version(self, ctx: commands.Context):
        await ctx.send(f"AdaptiveSlowmode **v{self.__version__}**")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return

        conf = await self.config.guild(message.guild).all()
        if not conf["enabled"] or message.channel.id not in conf["channels"]:
            return

        async with self._lock:
            self._message_cache[message.channel.id].append((datetime.now(timezone.utc), message.author.id))

    async def _run_slowmode_task(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await self.slowmode_task()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Un errore in un server/canale non deve fermare il monitoraggio globale.
                pass
            await asyncio.sleep(60)

    class SlowmodeLogView(discord.ui.View):
        def __init__(self, cog: "AdaptiveSlowmode", channel: discord.TextChannel, current: int, min_slow: int, max_slow: int):
            super().__init__(timeout=300)
            self.cog = cog
            self.channel = channel
            self.current = current
            self.min_slow = min_slow
            self.max_slow = max_slow
            self.add_item(AdaptiveSlowmode.IncreaseSlowmodeButton(cog, channel, current, max_slow))
            self.add_item(AdaptiveSlowmode.DecreaseSlowmodeButton(cog, channel, current, min_slow))

    class IncreaseSlowmodeButton(discord.ui.Button):
        def __init__(self, cog: "AdaptiveSlowmode", channel: discord.TextChannel, current: int, max_slow: int):
            super().__init__(
                label="Aumenta slowmode",
                style=discord.ButtonStyle.grey,
                emoji="⏫",
                custom_id=f"increase_slowmode_{channel.id}",
                row=0,
                disabled=current >= max_slow,
            )
            self.cog = cog
            self.channel = channel
            self.current = current
            self.max_slow = max_slow

        async def callback(self, interaction: discord.Interaction):
            if not interaction.user.guild_permissions.manage_channels:
                return await interaction.response.send_message("Non hai il permesso di modificare lo slowmode.", ephemeral=True)

            new_value = min(self.channel.slowmode_delay + 1, self.max_slow)
            try:
                await self.channel.edit(slowmode_delay=new_value, reason="Aumento manuale dallo slowmode adattivo")
                await interaction.response.send_message(
                    f"Slowmode di {self.channel.mention} aumentato a **{new_value} secondi**.",
                    ephemeral=True,
                )
            except discord.Forbidden:
                await interaction.response.send_message("Non ho il permesso di modificare questo canale.", ephemeral=True)
            except discord.HTTPException:
                await interaction.response.send_message("Discord ha rifiutato la modifica dello slowmode.", ephemeral=True)

    class DecreaseSlowmodeButton(discord.ui.Button):
        def __init__(self, cog: "AdaptiveSlowmode", channel: discord.TextChannel, current: int, min_slow: int):
            super().__init__(
                label="Riduci slowmode",
                style=discord.ButtonStyle.grey,
                emoji="⏬",
                custom_id=f"decrease_slowmode_{channel.id}",
                row=0,
                disabled=current <= min_slow,
            )
            self.cog = cog
            self.channel = channel
            self.current = current
            self.min_slow = min_slow

        async def callback(self, interaction: discord.Interaction):
            if not interaction.user.guild_permissions.manage_channels:
                return await interaction.response.send_message("Non hai il permesso di modificare lo slowmode.", ephemeral=True)

            new_value = max(self.channel.slowmode_delay - 1, self.min_slow)
            try:
                await self.channel.edit(slowmode_delay=new_value, reason="Riduzione manuale dallo slowmode adattivo")
                await interaction.response.send_message(
                    f"Slowmode di {self.channel.mention} ridotto a **{new_value} secondi**.",
                    ephemeral=True,
                )
            except discord.Forbidden:
                await interaction.response.send_message("Non ho il permesso di modificare questo canale.", ephemeral=True)
            except discord.HTTPException:
                await interaction.response.send_message("Discord ha rifiutato la modifica dello slowmode.", ephemeral=True)

    async def slowmode_task(self):
        now = datetime.now(timezone.utc)
        report_every = 5

        for guild in self.bot.guilds:
            conf = await self.config.guild(guild).all()
            if not conf["enabled"]:
                continue

            min_slow = max(0, int(conf["min_slowmode"]))
            max_slow = min(21600, max(min_slow, int(conf["max_slowmode"])))
            target = max(1, int(conf["target_msgs_per_min"]))

            for channel_id in list(conf["channels"]):
                channel = guild.get_channel(channel_id)
                if not isinstance(channel, discord.TextChannel):
                    continue

                async with self._lock:
                    cache = self._message_cache[channel_id]
                    while cache and (now - cache[0][0]).total_seconds() > 300:
                        cache.popleft()

                    minute_ago = now - timedelta(seconds=60)
                    minute_count = sum(1 for timestamp, _ in cache if timestamp > minute_ago)
                    self._minute_stats[channel_id].append(minute_count)

                current = channel.slowmode_delay
                if minute_count > target:
                    new_value = min(current + 1, max_slow)
                elif minute_count < (target // 2):
                    new_value = max(current - 1, min_slow)
                else:
                    new_value = current

                if new_value != current and self._bot_can_edit(channel):
                    try:
                        await channel.edit(
                            slowmode_delay=new_value,
                            reason="Regolazione automatica in base all'attivita del canale",
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        pass

        self._minute_tick += 1
        if self._minute_tick < report_every:
            return
        self._minute_tick = 0

        for guild in self.bot.guilds:
            conf = await self.config.guild(guild).all()
            if not conf["enabled"]:
                continue

            target = max(1, int(conf["target_msgs_per_min"]))
            min_slow = max(0, int(conf["min_slowmode"]))
            max_slow = min(21600, max(min_slow, int(conf["max_slowmode"])))

            for channel_id in list(conf["channels"]):
                channel = guild.get_channel(channel_id)
                if not isinstance(channel, discord.TextChannel):
                    continue

                stats = list(self._minute_stats[channel_id])
                stats = [0] * (5 - len(stats)) + stats
                current = channel.slowmode_delay

                async with self._lock:
                    five_minutes_ago = datetime.now(timezone.utc) - timedelta(minutes=5)
                    user_ids = {uid for timestamp, uid in self._message_cache[channel_id] if timestamp > five_minutes_ago}

                mentions = []
                for user_id in user_ids:
                    member = guild.get_member(user_id)
                    if member is not None:
                        mentions.append(member.mention)
                mentions.sort()

                now_ts = int(datetime.now(timezone.utc).timestamp())
                minute_lines = []
                for minutes_ago, amount in zip(range(4, -1, -1), stats):
                    timestamp = now_ts - minutes_ago * 60
                    minute_lines.append(f"<t:{timestamp}:R>: {amount} msg")

                embed = discord.Embed(
                    title="Lo slowmode adattivo sta monitorando l'attivita",
                    color=0xFFFFFE,
                )
                embed.add_field(name="Canale", value=channel.mention, inline=True)
                embed.add_field(name="Slowmode attuale", value=self._seconds_text(current), inline=True)
                embed.add_field(name="Obiettivo al minuto", value=f"{target} messaggi/min", inline=True)
                embed.add_field(name="Ultimi 5 minuti", value="\n".join(minute_lines), inline=False)
                embed.add_field(
                    name="Utenti visti di recente",
                    value=", ".join(mentions) if mentions else "Nessun utente rilevato",
                    inline=False,
                )

                view = self.SlowmodeLogView(self, channel, current, min_slow, max_slow)
                await self._send_log(guild, embed, view=view)
