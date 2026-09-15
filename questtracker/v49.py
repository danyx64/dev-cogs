import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import commands

from .questtracker import FALLBACK_SOURCE, PRIMARY_SOURCE, _parse_iso, _quest_config
from .v42 import REGIONS_SOURCE
from .v48 import QuestTracker as QuestTrackerV48


CURRENT_MIRROR_SOURCE = "https://raw.githubusercontent.com/xGustavvo/discord-api-tracker/main/quest.json"


# Sostituiamo status e aggiungiamo un comando di ispezione puntuale.
for _command_name in ("status", "cerca", "ispeziona", "inspect"):
    QuestTrackerV48.quest.remove_command(_command_name)


class QuestTracker(QuestTrackerV48):
    """QuestTracker 4.9.0: polling 15s, multi-source, backoff 429 e cache rolling."""

    __version__ = "4.9.0"
    POLL_SECONDS = 15
    REGION_REFRESH_SECONDS = 60
    ROLLING_GRACE_SECONDS = 600
    SOURCE_CACHE_MAX_AGE_SECONDS = 1800

    QUEST_SOURCES: Tuple[Tuple[str, str], ...] = (
        ("discordquest", PRIMARY_SOURCE),
        ("tracker", CURRENT_MIRROR_SOURCE),
        ("github-fallback", FALLBACK_SOURCE),
    )

    def __init__(self, bot):
        super().__init__(bot)
        self._payload_cache: Dict[str, Any] = {}
        self._payload_cached_at: Dict[str, datetime] = {}
        self._etags: Dict[str, str] = {}
        self._last_modified: Dict[str, str] = {}
        self._blocked_until: Dict[str, datetime] = {}
        self._source_errors: Dict[str, str] = {}
        self._source_counts: Dict[str, int] = {}
        self._source_last_ok: Dict[str, datetime] = {}

        self._regions_cache: Dict[str, Dict[str, Any]] = {}
        self._regions_fetched_at: Optional[datetime] = None

        self._rolling_entries: Dict[str, Dict[str, Any]] = {}
        self._rolling_seen_at: Dict[str, datetime] = {}
        self._cycle_entries: Optional[List[Dict[str, Any]]] = None
        self._last_live_fetch_at: Optional[datetime] = None

    @staticmethod
    def _quest_id(entry: Dict[str, Any]) -> str:
        config = entry.get("config") if isinstance(entry.get("config"), dict) else {}
        return str(
            entry.get("id")
            or config.get("id")
            or config.get("quest_id")
            or ""
        ).strip()

    @staticmethod
    def _extract_quest_list(payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [entry for entry in payload if isinstance(entry, dict)]
        if isinstance(payload, dict):
            for key in ("quests", "data", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [entry for entry in value if isinstance(entry, dict)]
        return []

    @staticmethod
    def _entry_quality(entry: Dict[str, Any]) -> int:
        """Preferisce la copia piu completa quando piu fonti hanno lo stesso ID."""
        config = _quest_config(entry) or {}
        score = 0
        if _parse_iso(config.get("starts_at")):
            score += 30
        if _parse_iso(config.get("expires_at")):
            score += 30
        if isinstance(config.get("messages"), dict):
            score += 15
        if isinstance(config.get("application"), dict):
            score += 10
        if isinstance(config.get("task_config_v2") or config.get("task_config"), dict):
            score += 10
        if isinstance(config.get("rewards_config") or config.get("rewards"), (dict, list)):
            score += 10
        if isinstance(config.get("assets"), dict):
            score += 5
        score += min(len(config), 20)
        return score

    @staticmethod
    def _retry_after_seconds(response: aiohttp.ClientResponse, payload: Any = None) -> float:
        raw = response.headers.get("Retry-After")
        if raw:
            try:
                return max(15.0, min(float(raw), 3600.0))
            except (TypeError, ValueError):
                try:
                    retry_at = parsedate_to_datetime(raw)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    seconds = (retry_at.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
                    return max(15.0, min(seconds, 3600.0))
                except (TypeError, ValueError, OverflowError):
                    pass

        if isinstance(payload, dict):
            try:
                return max(15.0, min(float(payload.get("retry_after")), 3600.0))
            except (TypeError, ValueError):
                pass
        return 60.0

    def _cached_payload(self, url: str) -> Any:
        payload = self._payload_cache.get(url)
        cached_at = self._payload_cached_at.get(url)
        if payload is None or cached_at is None:
            return None
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age > self.SOURCE_CACHE_MAX_AGE_SECONDS:
            return None
        return payload

    async def _fetch_json_source(self, label: str, url: str) -> Tuple[Any, bool]:
        """GET condizionale con cache e rispetto esplicito di HTTP 429."""
        if not self.session or self.session.closed:
            self._source_errors[label] = "sessione HTTP non disponibile"
            return self._cached_payload(url), False

        now = datetime.now(timezone.utc)
        blocked_until = self._blocked_until.get(url)
        if blocked_until and blocked_until > now:
            remaining = int((blocked_until - now).total_seconds())
            self._source_errors[label] = f"rate limit, retry tra {remaining}s"
            return self._cached_payload(url), False

        headers = {
            "Accept": "application/json",
            "User-Agent": f"Red-QuestTracker/{self.__version__}",
        }
        if url in self._etags:
            headers["If-None-Match"] = self._etags[url]
        if url in self._last_modified:
            headers["If-Modified-Since"] = self._last_modified[url]

        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status == 304:
                    cached = self._cached_payload(url)
                    if cached is not None:
                        self._source_last_ok[label] = now
                        self._source_errors.pop(label, None)
                        return cached, True
                    self._source_errors[label] = "304 senza cache locale"
                    return None, False

                if response.status == 429:
                    try:
                        payload = await response.json(content_type=None)
                    except (ValueError, TypeError, aiohttp.ContentTypeError):
                        payload = None
                    retry_after = self._retry_after_seconds(response, payload)
                    self._blocked_until[url] = now + timedelta(seconds=retry_after)
                    self._source_errors[label] = f"HTTP 429, pausa {int(retry_after)}s"
                    return self._cached_payload(url), False

                if response.status != 200:
                    self._source_errors[label] = f"HTTP {response.status}"
                    return self._cached_payload(url), False

                payload = await response.json(content_type=None)
                self._payload_cache[url] = payload
                self._payload_cached_at[url] = now
                if response.headers.get("ETag"):
                    self._etags[url] = response.headers["ETag"]
                if response.headers.get("Last-Modified"):
                    self._last_modified[url] = response.headers["Last-Modified"]
                self._source_last_ok[label] = now
                self._source_errors.pop(label, None)
                return payload, True
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
            self._source_errors[label] = f"{type(exc).__name__}"
            return self._cached_payload(url), False

    async def _fetch_regions_resilient(self) -> Dict[str, Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        if self._regions_cache and self._regions_fetched_at:
            age = (now - self._regions_fetched_at).total_seconds()
            if age < self.REGION_REFRESH_SECONDS:
                return dict(self._regions_cache)

        payload, live = await self._fetch_json_source("regions", REGIONS_SOURCE)
        if not isinstance(payload, dict):
            return dict(self._regions_cache)

        items = payload.get("quests")
        if not isinstance(items, list):
            return dict(self._regions_cache)

        result: Dict[str, Dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            quest_id = str(item.get("id") or "").strip()
            if not quest_id:
                continue
            region = item.get("regions") if isinstance(item.get("regions"), dict) else {}
            include_raw = region.get("include") if isinstance(region, dict) else []
            exclude_raw = region.get("exclude") if isinstance(region, dict) else []
            result[quest_id] = {
                "is_global": bool(item.get("is_global")),
                "include": [str(v) for v in include_raw] if isinstance(include_raw, list) else [],
                "exclude": [str(v) for v in exclude_raw] if isinstance(exclude_raw, list) else [],
            }

        if result:
            self._regions_cache = result
            if live:
                self._regions_fetched_at = now
        return dict(self._regions_cache)

    def _merge_source_entry(
        self,
        merged: Dict[str, Dict[str, Any]],
        entry: Dict[str, Any],
        source_label: str,
    ) -> None:
        quest_id = self._quest_id(entry)
        if not quest_id:
            return

        candidate = dict(entry)
        candidate["_quest_sources"] = [source_label]
        previous = merged.get(quest_id)
        if previous is None:
            merged[quest_id] = candidate
            return

        sources = list(dict.fromkeys((previous.get("_quest_sources") or []) + [source_label]))
        if self._entry_quality(candidate) > self._entry_quality(previous):
            candidate["_quest_sources"] = sources
            merged[quest_id] = candidate
        else:
            previous["_quest_sources"] = sources

    async def _fetch_quests_live(self) -> List[Dict[str, Any]]:
        """Legge tutte le fonti, le unisce per ID e mantiene una breve cache rolling."""
        results = await asyncio.gather(
            *(self._fetch_json_source(label, url) for label, url in self.QUEST_SOURCES),
            return_exceptions=False,
        )
        regions = await self._fetch_regions_resilient()

        merged: Dict[str, Dict[str, Any]] = {}
        any_live = False
        for (label, _url), (payload, live) in zip(self.QUEST_SOURCES, results):
            entries = self._extract_quest_list(payload)
            self._source_counts[label] = len(entries)
            any_live = any_live or live
            for entry in entries:
                self._merge_source_entry(merged, entry, label)

        now = datetime.now(timezone.utc)
        if any_live:
            self._last_live_fetch_at = now

        # Aggiorna il rolling set. Se una fonte omette per pochi minuti una Quest
        # che aveva appena pubblicato, non la perdiamo prima che parta/notifichi.
        for quest_id, entry in merged.items():
            item = dict(entry)
            item["_italy_region"] = regions.get(quest_id)
            self._rolling_entries[quest_id] = item
            self._rolling_seen_at[quest_id] = now

        for quest_id in list(self._rolling_entries):
            entry = self._rolling_entries[quest_id]
            seen_at = self._rolling_seen_at.get(quest_id, now)
            config = _quest_config(entry) or {}
            expires = _parse_iso(config.get("expires_at"))
            age = (now - seen_at).total_seconds()

            if expires and expires <= now:
                self._rolling_entries.pop(quest_id, None)
                self._rolling_seen_at.pop(quest_id, None)
                continue
            if age > self.ROLLING_GRACE_SECONDS:
                self._rolling_entries.pop(quest_id, None)
                self._rolling_seen_at.pop(quest_id, None)
                continue

            if quest_id not in merged:
                cached = dict(entry)
                cached["_quest_rolling_cache"] = True
                if quest_id in regions:
                    cached["_italy_region"] = regions[quest_id]
                merged[quest_id] = cached

        return list(merged.values())

    async def _fetch_quests(self) -> List[Dict[str, Any]]:
        # Durante il ciclo automatico tutte le guild usano esattamente lo stesso
        # snapshot: una sola lettura delle fonti ogni 15 secondi, non una per server.
        if self._cycle_entries is not None:
            return list(self._cycle_entries)
        return await self._fetch_quests_live()

    @tasks.loop(seconds=POLL_SECONDS)
    async def quest_scan(self):
        """Controlla e autoinvia nuove Quest ogni 15 secondi."""
        if self._scan_lock.locked():
            return

        async with self._scan_lock:
            entries = await self._fetch_quests_live()
            self._cycle_entries = entries
            try:
                for guild in list(self.bot.guilds):
                    try:
                        await self._scan_guild(guild)
                    except Exception:
                        continue
            finally:
                self._cycle_entries = None

    @quest_scan.before_loop
    async def before_quest_scan(self):
        await self.bot.wait_until_red_ready()
        await asyncio.sleep(5)

    @QuestTrackerV48.quest.command(name="cerca", aliases=["ispeziona", "inspect"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_lookup(self, ctx: commands.Context, quest_id: str):
        """Controlla un ID Quest nelle fonti e spiega perche viene o non viene notificato."""
        quest_id = str(quest_id).strip()
        async with ctx.typing():
            entries = await self._fetch_quests_live()

        entry = next((item for item in entries if self._quest_id(item) == quest_id), None)
        if entry is None:
            counts = ", ".join(f"{name}={self._source_counts.get(name, 0)}" for name, _ in self.QUEST_SOURCES)
            errors = "; ".join(f"{name}: {error}" for name, error in self._source_errors.items()) or "nessuno"
            return await ctx.send(
                f"❌ Quest `{quest_id}` non trovata nello snapshot corrente.\n"
                f"Fonti: `{counts}`\nErrori/rate-limit: `{errors}`"
            )

        config = _quest_config(entry) or {}
        messages = config.get("messages") or {}
        app = config.get("application") or {}
        name = str(messages.get("quest_name") or messages.get("game_title") or app.get("name") or "Discord Quest")
        starts = _parse_iso(config.get("starts_at"))
        expires = _parse_iso(config.get("expires_at"))
        now = datetime.now(timezone.utc)

        if starts and starts > now:
            state = f"⏳ Non ancora iniziata • inizio <t:{int(starts.timestamp())}:F> (<t:{int(starts.timestamp())}:R>)"
        elif expires and expires <= now:
            state = "⌛ Scaduta"
        else:
            state = "✅ Attiva adesso"

        region = entry.get("_italy_region")
        italy = self._region_allows_italy(region)
        sources = ", ".join(entry.get("_quest_sources") or ["cache rolling"])
        rolling = "si" if entry.get("_quest_rolling_cache") else "no"
        active_ids = {self._quest_id(item) for _, item, _ in self._active_quests(entries)}
        notify_state = "si" if quest_id in active_ids else "no"

        embed = discord.Embed(title=f"🔎 Quest {quest_id}", colour=discord.Colour.blurple())
        embed.add_field(name="Nome", value=name[:1024], inline=False)
        embed.add_field(name="Stato", value=state, inline=False)
        embed.add_field(name="Regione", value=self._region_summary(region)[:1024], inline=False)
        embed.add_field(name="Compatibile Italia", value="✅ Si" if italy else "⛔ No", inline=True)
        embed.add_field(name="Notificabile ora", value=notify_state, inline=True)
        embed.add_field(name="Cache rolling", value=rolling, inline=True)
        embed.add_field(name="Fonti", value=sources[:1024], inline=False)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @QuestTrackerV48.quest.command(name="status")
    async def quest_status(self, ctx: commands.Context):
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(settings.get("channel_id") or 0)
        role = ctx.guild.get_role(settings.get("role_id") or 0)
        now = datetime.now(timezone.utc)

        source_lines = []
        for label, url in self.QUEST_SOURCES:
            count = self._source_counts.get(label, 0)
            blocked = self._blocked_until.get(url)
            if blocked and blocked > now:
                suffix = f" • 429 fino a <t:{int(blocked.timestamp())}:R>"
            elif label in self._source_errors:
                suffix = f" • {self._source_errors[label]}"
            else:
                suffix = ""
            source_lines.append(f"• **{label}**: {count}{suffix}")

        if self._last_live_fetch_at:
            last_feed = f"<t:{int(self._last_live_fetch_at.timestamp())}:R>"
        else:
            last_feed = "Non ancora disponibile"

        embed = discord.Embed(title="🎯 QuestTracker", colour=discord.Colour.blurple())
        embed.add_field(name="Versione", value=self.__version__, inline=True)
        embed.add_field(name="Stato", value="✅ Attivo" if settings.get("enabled") else "⏸️ Disattivato", inline=True)
        embed.add_field(name="Controllo", value="Ogni 15 secondi", inline=True)
        embed.add_field(name="Canale", value=channel.mention if channel else "Non configurato", inline=True)
        embed.add_field(name="Ruolo", value=role.mention if role else "Nessuno", inline=True)
        embed.add_field(name="Ping ruolo", value="✅ Attivo" if settings.get("ping_role", True) else "⛔ Disattivato", inline=True)
        embed.add_field(name="Ultimo fetch live", value=last_feed, inline=False)
        embed.add_field(name="Fonti Quest", value="\n".join(source_lines)[:1024], inline=False)
        embed.add_field(
            name="Protezione",
            value="ETag/304 + rispetto HTTP 429 + 3 fonti + cache rolling 10 min + retry invio",
            inline=False,
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
