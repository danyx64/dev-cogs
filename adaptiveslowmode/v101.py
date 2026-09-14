from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord

from .adaptiveslowmode import AdaptiveSlowmode as BaseAdaptiveSlowmode


async def _detailed_status(self: "AdaptiveSlowmode", ctx):
    """Mostra uno status dettagliato senza modificare la Config esistente."""
    conf = await self.config.guild(ctx.guild).all()

    log_channel_id = conf.get("log_channel")
    log_channel = ctx.guild.get_channel(log_channel_id) if log_channel_id else None

    min_slow = max(0, int(conf.get("min_slowmode") or 0))
    max_slow = max(min_slow, int(conf.get("max_slowmode") or 0))
    target = max(1, int(conf.get("target_msgs_per_min") or 1))
    channel_ids = list(conf.get("channels") or [])

    embed = discord.Embed(
        title="📊 Stato slowmode adattivo",
        description=(
            "Qui trovi tutte le impostazioni attive e i canali attualmente monitorati."
        ),
        color=0x2BBD8E if conf.get("enabled") else 0xFFB347,
    )
    embed.add_field(
        name="Stato",
        value="✅ Attivo" if conf.get("enabled") else "⏸️ Disattivato",
        inline=True,
    )
    embed.add_field(name="Obiettivo", value=f"{target} msg/min", inline=True)
    embed.add_field(name="Intervallo slowmode", value=f"{min_slow}s → {max_slow}s", inline=True)
    embed.add_field(
        name="Canale log",
        value=log_channel.mention if isinstance(log_channel, discord.TextChannel) else "Nessuno",
        inline=True,
    )
    embed.add_field(name="Canali configurati", value=str(len(channel_ids)), inline=True)
    embed.add_field(name="Versione", value=self.__version__, inline=True)

    now = datetime.now(timezone.utc)
    minute_ago = now - timedelta(seconds=60)
    channel_lines = []

    for channel_id in channel_ids:
        channel = ctx.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            channel_lines.append(f"❌ `ID {channel_id}` — canale non trovato")
            continue

        cache = self._message_cache.get(channel.id, [])
        recent_messages = sum(1 for timestamp, _ in cache if timestamp > minute_ago)
        editable = "✅" if self._bot_can_edit(channel) else "⚠️"
        channel_lines.append(
            f"{editable} {channel.mention} — **{channel.slowmode_delay}s** — "
            f"{recent_messages} msg ultimo minuto"
        )

    if not channel_lines:
        embed.add_field(
            name="Canali monitorati",
            value="Nessun canale configurato.",
            inline=False,
        )
    else:
        chunks = []
        current = ""
        for line in channel_lines:
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) > 1000:
                chunks.append(current)
                current = line
            else:
                current = candidate
        if current:
            chunks.append(current)

        for index, chunk in enumerate(chunks[:4]):
            name = "Canali monitorati" if index == 0 else "Canali monitorati (continua)"
            embed.add_field(name=name, value=chunk, inline=False)

        if len(chunks) > 4:
            embed.add_field(
                name="Nota",
                value="La lista e troppo lunga per un singolo embed; alcuni canali non sono mostrati.",
                inline=False,
            )

    embed.add_field(
        name="Legenda",
        value="✅ il bot puo modificare lo slowmode • ⚠️ permessi insufficienti • ❌ canale non trovato",
        inline=False,
    )

    await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


# Il comando originale `settings` ha gia gli alias `status` e `stato`.
# Sostituiamo solo il callback: nome comando, alias, permessi e Config restano identici.
_settings_command = BaseAdaptiveSlowmode.adaptiveslowmode.get_command("settings")
if _settings_command is not None:
    _settings_command.callback = _detailed_status


class AdaptiveSlowmode(BaseAdaptiveSlowmode):
    """AdaptiveSlowmode 1.0.1-it con status dettagliato."""

    __version__ = "1.0.1-it"
