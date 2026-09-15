import re
from typing import Any, Dict, List, Optional, Set, Tuple

import discord

from .questtracker import FALLBACK_SOURCE, PRIMARY_SOURCE, _canonical_key
from .v42 import ITALY_CODES, ITALY_REGION_CODES, _norm_region
from .v49 import CURRENT_MIRROR_SOURCE
from .v50 import QuestTracker as QuestTrackerV50


TRACKER_MAIN_SOURCE = (
    "https://raw.githubusercontent.com/xGustavvo/discord-api-tracker/"
    "refs/heads/main/data/quests-01.json"
)
QUEST_LINK_RE = re.compile(r"https?://(?:www\.)?discord\.com/quests/(\d+)", re.I)
VERIFIED_ITALY_IDS = {"1544553334097190922"}


class QuestTracker(QuestTrackerV50):
    """QuestTracker 5.1.0: consensus regioni e registro consegne reale."""

    __version__ = "5.1.0"
    DELIVERY_HISTORY_LIMIT = 2000
    QUEST_SOURCES: Tuple[Tuple[str, str], ...] = (
        ("discordquest", PRIMARY_SOURCE),
        ("tracker-main", TRACKER_MAIN_SOURCE),
        ("tracker-fallback", CURRENT_MIRROR_SOURCE),
        ("github-fallback", FALLBACK_SOURCE),
    )

    def __init__(self, bot):
        super().__init__(bot)
        self.config.register_guild(
            v51_sent_families=[],
            v51_sent_ids=[],
            v51_delivery_migrated=False,
        )

    @staticmethod
    def _one_region_verdict(record: Dict[str, Any]) -> Tuple[str, int, str]:
        source = str(record.get("source") or "region-source")
        include = {_norm_region(v) for v in record.get("include", []) if v}
        exclude = {_norm_region(v) for v in record.get("exclude", []) if v}

        if exclude & ITALY_REGION_CODES:
            return "blocked", 0, f"{source}: Italia/Europa esclusa"
        if include:
            if include & ITALY_CODES:
                return "allowed", 500, f"{source}: include Italia"
            if include & (ITALY_REGION_CODES - ITALY_CODES):
                return "allowed", 450, f"{source}: include Europa/EEA"
            return "blocked", 0, f"{source}: allow-list estera ({', '.join(sorted(include))})"
        if record.get("is_global") is True:
            return "allowed", 400, f"{source}: globale"
        if exclude:
            return "allowed", 350, f"{source}: blacklist senza Italia ({', '.join(sorted(exclude))})"
        return "unknown", 0, f"{source}: record incompleto"

    @classmethod
    def _italy_verdict(cls, region: Optional[Dict[str, Any]]) -> Tuple[str, int, str]:
        if not isinstance(region, dict):
            return "unknown", 0, "nessun dato regionale affidabile"

        if region.get("_verified_italy"):
            return "allowed", 1000, "verifica reale: disponibile in Italia"

        evidence = region.get("evidence")
        records: List[Dict[str, Any]] = []
        if isinstance(evidence, dict):
            records = [r for r in evidence.values() if isinstance(r, dict)]
        elif any(k in region for k in ("is_global", "include", "exclude")):
            records = [region]

        if not records:
            return "unknown", 0, "nessun dato regionale affidabile"

        allowed: List[Tuple[int, str]] = []
        blocked: List[str] = []
        unknown: List[str] = []
        for record in records:
            verdict, score, reason = cls._one_region_verdict(record)
            if verdict == "allowed":
                allowed.append((score, reason))
            elif verdict == "blocked":
                blocked.append(reason)
            else:
                unknown.append(reason)

        if allowed and blocked:
            return "unknown", 0, "conflitto fonti: " + "; ".join([r for _, r in allowed] + blocked)
        if allowed:
            score, reason = max(allowed, key=lambda item: item[0])
            if len(allowed) > 1:
                reason += f" (+{len(allowed) - 1} conferme)"
            return "allowed", score, reason
        if blocked:
            return "blocked", 0, "; ".join(blocked)
        return "unknown", 0, "; ".join(unknown) or "dati regionali incompleti"

    async def _fetch_quests_live(self) -> List[Dict[str, Any]]:
        entries = await super()._fetch_quests_live()
        out: List[Dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item = dict(entry)
            qid = self._quest_id(item)
            region = dict(item.get("_italy_region") or {})
            if isinstance(region.get("evidence"), dict):
                region["evidence"] = dict(region["evidence"])
            if qid in VERIFIED_ITALY_IDS:
                region["_verified_italy"] = True
            item["_italy_region"] = region
            out.append(item)
        return out

    async def _mark_initial_state(self, guild: discord.Guild) -> int:
        """Su un setup nuovo registra lo stato corrente senza inondare il canale."""
        entries = await self._fetch_quests()
        active = self._active_quests(entries)
        conf = self.config.guild(guild)

        families: List[str] = []
        ids: List[str] = []
        legacy: List[str] = []
        for family, entry, config in active:
            qid = self._quest_id(entry)
            if family not in families:
                families.append(family)
            if qid and qid not in ids:
                ids.append(qid)
            key = _canonical_key(entry, config)
            if key not in legacy:
                legacy.append(key)

        await conf.v51_sent_families.set(families[-1000:])
        await conf.v51_sent_ids.set(ids[-1000:])
        await conf.v51_delivery_migrated.set(True)
        await conf.v50_seen_families.set(families[-500:])
        await conf.v50_seen_ids.set(ids[-500:])
        await conf.v50_migrated.set(True)
        await conf.seen_keys.set(legacy[-500:])
        await conf.initialized.set(True)
        return len(active)

    async def _history_delivery_ledger(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        entries: List[Dict[str, Any]],
    ) -> Optional[Tuple[List[str], List[str]]]:
        me = guild.me
        if me is None or not channel.permissions_for(me).read_message_history:
            self._last_scan_errors[guild.id].append(
                "Serve il permesso Leggere cronologia messaggi per verificare le consegne reali."
            )
            return None

        id_to_family: Dict[str, str] = {}
        for entry, config in self._raw_active_entries(entries):
            qid = self._quest_id(entry)
            if qid:
                id_to_family[qid] = self._family_key(entry, config)

        ids: Set[str] = set()
        families: Set[str] = set()
        try:
            async for message in channel.history(limit=self.DELIVERY_HISTORY_LIMIT):
                if not self.bot.user or message.author.id != self.bot.user.id:
                    continue
                if getattr(message.flags, "suppress_notifications", False):
                    continue
                for qid in QUEST_LINK_RE.findall(message.content or ""):
                    ids.add(qid)
                    family = id_to_family.get(qid)
                    if family:
                        families.add(family)
        except (discord.Forbidden, discord.HTTPException):
            self._last_scan_errors[guild.id].append("Impossibile verificare la cronologia del canale Quest.")
            return None
        return sorted(ids), sorted(families)

    async def _scan_guild(self, guild: discord.Guild, *, force: bool = False) -> int:
        self._last_scan_errors[guild.id] = []
        settings = await self.config.guild(guild).all()
        if not settings.get("enabled") and not force:
            return 0

        channel = guild.get_channel(settings.get("channel_id") or 0)
        if not isinstance(channel, discord.TextChannel):
            self._last_scan_errors[guild.id].append("Canale Quest non configurato.")
            return 0

        entries = await self._fetch_quests()
        active = self._active_quests(entries)
        conf = self.config.guild(guild)

        if not settings.get("v51_delivery_migrated"):
            rebuilt = await self._history_delivery_ledger(guild, channel, entries)
            if rebuilt is None:
                return 0
            sent_ids, sent_families = rebuilt
            await conf.v51_sent_ids.set(sent_ids[-1000:])
            await conf.v51_sent_families.set(sent_families[-1000:])
            await conf.v51_delivery_migrated.set(True)
        else:
            sent_ids = [str(v) for v in settings.get("v51_sent_ids") or []]
            sent_families = [str(v) for v in settings.get("v51_sent_families") or []]

        id_seen = set(sent_ids)
        family_seen = set(sent_families)
        legacy_order = [str(v) for v in settings.get("seen_keys") or []]
        legacy_seen = set(legacy_order)
        v50_family_order = [str(v) for v in settings.get("v50_seen_families") or []]
        v50_id_order = [str(v) for v in settings.get("v50_seen_ids") or []]

        sent = 0
        for family, entry, config in reversed(active):
            qid = self._quest_id(entry)
            if not qid or qid in id_seen or family in family_seen:
                continue
            if self._quest_share_url(entry, config) is None:
                self._last_scan_errors[guild.id].append(f"Quest `{qid}`: link non valido.")
                continue
            try:
                await self._send_quest(channel, guild, entry, config, test=False)
            except discord.Forbidden:
                self._last_scan_errors[guild.id].append(f"Quest `{qid}`: permessi insufficienti.")
                continue
            except discord.HTTPException as exc:
                self._last_scan_errors[guild.id].append(
                    f"Quest `{qid}`: HTTP {getattr(exc, 'status', '?')} / code {getattr(exc, 'code', '?')}."
                )
                continue

            id_seen.add(qid)
            sent_ids.append(qid)
            family_seen.add(family)
            sent_families.append(family)

            old_key = _canonical_key(entry, config)
            if old_key not in legacy_seen:
                legacy_seen.add(old_key)
                legacy_order.append(old_key)
            if family not in v50_family_order:
                v50_family_order.append(family)
            if qid not in v50_id_order:
                v50_id_order.append(qid)
            sent += 1

        await conf.v51_sent_ids.set(sent_ids[-1000:])
        await conf.v51_sent_families.set(sent_families[-1000:])
        await conf.seen_keys.set(legacy_order[-500:])
        await conf.v50_seen_families.set(v50_family_order[-500:])
        await conf.v50_seen_ids.set(v50_id_order[-500:])
        return sent
