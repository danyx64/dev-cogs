import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import discord
from discord.ext import tasks

from .v47 import QuestTracker as QuestTrackerV47


# Lo status ereditato indicava ancora il vecchio intervallo di 5 minuti.
QuestTrackerV47.quest.remove_command("status")


class QuestTracker(QuestTrackerV47):
    """QuestTracker 4.8.0: scansione automatica ogni minuto con retry del feed."""

    __version__ = "4.8.0"
    CACHE_MAX_AGE_SECONDS = 900
    SHARED_SNAPSHOT_SECONDS = 20
    SUSPICIOUS_DROP_RATIO = 0.75

    def __init__(self, bot):
        super().__init__(bot)
        self._last_good_entries: List[Dict[str, Any]] = []
        self._last_good_fetch_at: Optional[datetime] = None
        self._last_fetch_used_stale_cache = False

    @staticmethod
    def _merge_samples(samples: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for sample in samples:
            for entry in sample:
                if not isinstance(entry, dict):
                    continue
                quest_id = str(entry.get("id") or "").strip()
                if not quest_id:
                    continue
                old = merged.get(quest_id)
                if old is None or (not old.get("_italy_region") and entry.get("_italy_region")):
                    merged[quest_id] = entry
        return list(merged.values())

    async def _fetch_quests(self) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc)

        # Una scansione attraversa tutte le guild. Per pochi secondi riusiamo lo
        # stesso snapshot, evitando di martellare i feed una volta per server.
        if self._last_good_entries and self._last_good_fetch_at:
            age = (now - self._last_good_fetch_at).total_seconds()
            if age <= self.SHARED_SNAPSHOT_SECONDS:
                self._last_fetch_used_stale_cache = False
                return list(self._last_good_entries)

        previous_count = len(self._last_good_entries)
        samples: List[List[Dict[str, Any]]] = []

        # Se il feed risponde vuoto o con un calo anomalo, facciamo altre due
        # letture e le uniamo. Una Quest vista in una sola lettura non viene persa.
        for delay in (0, 2, 5):
            if delay:
                await asyncio.sleep(delay)
            current = await super()._fetch_quests()
            if current:
                samples.append(current)
                merged = self._merge_samples(samples)
                threshold = max(5, int(previous_count * self.SUSPICIOUS_DROP_RATIO))
                if len(merged) >= threshold:
                    break

        merged = self._merge_samples(samples)
        now = datetime.now(timezone.utc)
        if merged:
            self._last_good_entries = list(merged)
            self._last_good_fetch_at = now
            self._last_fetch_used_stale_cache = False
            return merged

        # Se tutti i tentativi falliscono, per massimo 15 minuti manteniamo lo
        # snapshot precedente. Il filtro delle date continua comunque a scartare
        # automaticamente le Quest scadute.
        if self._last_good_entries and self._last_good_fetch_at:
            age = (now - self._last_good_fetch_at).total_seconds()
            if age <= self.CACHE_MAX_AGE_SECONDS:
                self._last_fetch_used_stale_cache = True
                return list(self._last_good_entries)

        self._last_fetch_used_stale_cache = False
        return []

    @tasks.loop(seconds=60)
    async def quest_scan(self):
        """Controlla automaticamente le nuove Quest ogni 60 secondi."""
        if self._scan_lock.locked():
            return
        async with self._scan_lock:
            for guild in list(self.bot.guilds):
                try:
                    await self._scan_guild(guild)
                except Exception:
                    # Un errore su una guild non deve fermare il monitoraggio
                    # delle altre guild ne il prossimo ciclo automatico.
                    continue

    @quest_scan.before_loop
    async def before_quest_scan(self):
        await self.bot.wait_until_red_ready()
        await asyncio.sleep(10)

    @QuestTrackerV47.quest.command(name="status")
    async def quest_status(self, ctx):
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(settings.get("channel_id") or 0)
        role = ctx.guild.get_role(settings.get("role_id") or 0)

        if self._last_good_fetch_at:
            last_feed = self._last_good_fetch_at.strftime("%d/%m/%Y %H:%M:%S UTC")
        else:
            last_feed = "Non ancora disponibile"

        embed = discord.Embed(title="🎯 QuestTracker", colour=discord.Colour.blurple())
        embed.add_field(name="Versione", value=self.__version__, inline=True)
        embed.add_field(
            name="Stato",
            value="✅ Attivo" if settings.get("enabled") else "⏸️ Disattivato",
            inline=True,
        )
        embed.add_field(name="Controllo", value="Ogni 60 secondi", inline=True)
        embed.add_field(name="Canale", value=channel.mention if channel else "Non configurato", inline=True)
        embed.add_field(name="Ruolo", value=role.mention if role else "Nessuno", inline=True)
        embed.add_field(
            name="Ping ruolo",
            value="✅ Attivo" if settings.get("ping_role", True) else "⛔ Disattivato",
            inline=True,
        )
        embed.add_field(name="Ultimo feed valido", value=last_feed, inline=False)
        embed.add_field(
            name="Protezione feed",
            value="Retry 3x + unione letture + cache emergenza 15 min",
            inline=False,
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
