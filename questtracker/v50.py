import asyncio
import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlparse

import discord
from redbot.core import commands

from .questtracker import (
    DISCORD_SPONSORED_APP_ID,
    _canonical_key,
    _parse_iso,
    _quest_config,
    _rewards,
    _task_items,
)
from .v42 import ITALY_CODES, ITALY_REGION_CODES, REGIONS_SOURCE, _norm_region
from .v49 import QuestTracker as QuestTrackerV49


TRACKER_RESTRICTIONS_SOURCE = (
    "https://gist.githubusercontent.com/xGustavvo/"
    "3d08b7369eb34b50834815fd43176cae/raw"
)

# Quest demo/test note pubblicamente nei tracker: non vanno notificate.
KNOWN_TEST_QUEST_IDS = {
    "1193992107035983872",
    "1417206015245418566",
    "1223393873447878656",
    "1276640451235156082",
    "1483951358322147380",
    "1519474065293967471",
}

REGION_SOURCE_PRIORITY = ("tracker-regions", "discordquest-regions")
LOCALE_TOKENS = {
    "it", "it-it", "ita", "italy", "italia", "us", "en-us", "gb", "uk",
    "en-gb", "au", "ca", "br", "mx", "de", "de-de", "fr", "fr-fr",
    "es", "es-es", "pt", "pt-br", "jp", "ja", "ja-jp", "kr", "ko",
    "ko-kr", "cn", "zh", "hk", "in", "sa", "ae", "ph", "nl", "pl",
}
CAMPAIGN_QUERY_KEYS = {
    "o2_id", "o2_app_id", "campaign", "campaign_id", "campaignid",
    "offer", "offer_id", "offerid", "placement", "placement_id", "mpid",
    "creative_id", "creativeid", "flight_id", "flightid",
}

for _name in (
    "status", "cerca", "ispeziona", "inspect", "diagnostica", "debugquest",
    "regioni", "reinvia", "resend", "controlla", "check",
):
    QuestTrackerV49.quest.remove_command(_name)


class QuestTracker(QuestTrackerV49):
    """QuestTracker 5.0.0: filtro Italia strict, dedupe campagna e polling 15s."""

    __version__ = "5.0.0"
    POLL_SECONDS = 15
    REGION_REFRESH_SECONDS = 15
    REGION_SOURCES: Tuple[Tuple[str, str], ...] = (
        ("tracker-regions", TRACKER_RESTRICTIONS_SOURCE),
        ("discordquest-regions", REGIONS_SOURCE),
    )

    def __init__(self, bot):
        super().__init__(bot)
        self.config.register_guild(
            v50_seen_families=[],
            v50_seen_ids=[],
            v50_migrated=False,
        )
        self._region_maps_by_source: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._last_region_refresh: Optional[datetime] = None

    @staticmethod
    def _parse_region_payload(payload: Any, source: str) -> Dict[str, Dict[str, Any]]:
        items = payload.get("quests") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            qid = str(item.get("id") or "").strip()
            if not qid:
                continue
            regions = item.get("regions")
            include: List[str] = []
            exclude: List[str] = []
            if isinstance(regions, list):
                include = [str(v) for v in regions if v]
            elif isinstance(regions, dict):
                if isinstance(regions.get("include"), list):
                    include = [str(v) for v in regions["include"] if v]
                if isinstance(regions.get("exclude"), list):
                    exclude = [str(v) for v in regions["exclude"] if v]
            if not include and isinstance(item.get("include"), list):
                include = [str(v) for v in item["include"] if v]
            if not exclude and isinstance(item.get("exclude"), list):
                exclude = [str(v) for v in item["exclude"] if v]
            out[qid] = {
                "is_global": item.get("is_global") is True,
                "include": include,
                "exclude": exclude,
                "source": source,
            }
        return out

    async def _fetch_regions_resilient(self) -> Dict[str, Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        if self._last_region_refresh and self._regions_cache:
            age = (now - self._last_region_refresh).total_seconds()
            if age < self.REGION_REFRESH_SECONDS:
                return dict(self._regions_cache)

        results = await asyncio.gather(
            *(self._fetch_json_source(label, url) for label, url in self.REGION_SOURCES)
        )
        usable = False
        for (label, _url), (payload, _live) in zip(self.REGION_SOURCES, results):
            parsed = self._parse_region_payload(payload, label)
            if parsed:
                self._region_maps_by_source[label] = parsed
                usable = True

        combined: Dict[str, Dict[str, Any]] = {}
        ids = set()
        for mapping in self._region_maps_by_source.values():
            ids.update(mapping)
        for qid in ids:
            evidence = {}
            for label in REGION_SOURCE_PRIORITY:
                record = self._region_maps_by_source.get(label, {}).get(qid)
                if record:
                    evidence[label] = dict(record)
            if evidence:
                combined[qid] = {"evidence": evidence}

        if combined:
            self._regions_cache = combined
        if usable:
            self._regions_fetched_at = now
        self._last_region_refresh = now
        return dict(self._regions_cache)

    @staticmethod
    def _chosen_region_record(region: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(region, dict):
            return None
        evidence = region.get("evidence")
        if isinstance(evidence, dict):
            for source in REGION_SOURCE_PRIORITY:
                record = evidence.get(source)
                if isinstance(record, dict):
                    return record
        if any(k in region for k in ("is_global", "include", "exclude")):
            return region
        return None

    @classmethod
    def _italy_verdict(cls, region: Optional[Dict[str, Any]]) -> Tuple[str, int, str]:
        record = cls._chosen_region_record(region)
        if not record:
            return "unknown", 0, "nessun dato regionale affidabile"
        source = str(record.get("source") or "region-cache")
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
        return "unknown", 0, f"{source}: record regionale incompleto"

    @classmethod
    def _region_allows_italy(cls, region: Optional[Dict[str, Any]]) -> bool:
        return cls._italy_verdict(region)[0] == "allowed"

    @classmethod
    def _region_summary(cls, region: Optional[Dict[str, Any]]) -> str:
        verdict, _score, reason = cls._italy_verdict(region)
        tag = {"allowed": "IT OK", "blocked": "NO IT", "unknown": "ATTESA"}[verdict]
        return f"{tag} - {reason}"

    @staticmethod
    def _norm_text(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    @staticmethod
    def _url_anchor(url: Any) -> str:
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return ""
        try:
            parsed = urlparse(url)
        except ValueError:
            return ""
        host = (parsed.hostname or "").lower().strip(".")
        if host.startswith("www."):
            host = host[4:]
        parts = host.split(".")
        if len(parts) > 2 and parts[0] in LOCALE_TOKENS:
            host = ".".join(parts[1:])
        path = [p.lower() for p in parsed.path.split("/") if p]
        if path and path[0] in LOCALE_TOKENS:
            path = path[1:]
        selected = sorted(
            (k.lower(), v.lower()) for k, v in parse_qsl(parsed.query)
            if k.lower() in CAMPAIGN_QUERY_KEYS and v
        )
        query = "&".join(f"{k}={v}" for k, v in selected)
        return "|".join(x for x in (host, "/".join(path[:3]), query) if x)

    @staticmethod
    def _task_sig(config: Dict[str, Any]) -> str:
        return ",".join(
            f"{name}:{'' if target is None else target}"
            for name, target in sorted(_task_items(config))
        )

    @staticmethod
    def _reward_sig(config: Dict[str, Any]) -> str:
        parts = []
        for reward in _rewards(config):
            parts.append(":".join((
                str(reward.get("type") or ""),
                str(reward.get("sku_id") or ""),
                str(reward.get("orb_quantity") or ""),
                str(reward.get("premium_orb_quantity") or ""),
            )))
        return ",".join(sorted(parts))

    def _family_key(self, entry: Dict[str, Any], config: Dict[str, Any]) -> str:
        app = config.get("application") if isinstance(config.get("application"), dict) else {}
        msg = config.get("messages") if isinstance(config.get("messages"), dict) else {}
        cta = config.get("cta_config") if isinstance(config.get("cta_config"), dict) else {}
        app_id = str(app.get("id") or config.get("application_id") or "")
        if app_id == DISCORD_SPONSORED_APP_ID:
            publisher = self._norm_text(msg.get("game_publisher"))
            link = cta.get("link") or app.get("link") or app.get("store_link")
            campaign = f"sponsored:{publisher}:{self._url_anchor(link)}"
        else:
            campaign = f"app:{app_id}"
        raw = "|".join((
            campaign,
            str(config.get("starts_at") or ""),
            str(config.get("expires_at") or ""),
            self._task_sig(config),
            self._reward_sig(config),
        ))
        return "v50:" + hashlib.sha256(raw.encode()).hexdigest()[:32]

    @staticmethod
    def _italian_hint(config: Dict[str, Any]) -> int:
        app = config.get("application") or {}
        cta = config.get("cta_config") or {}
        msg = config.get("messages") or {}
        text = " ".join(str(v or "") for v in (
            app.get("link"), cta.get("link"), msg.get("quest_name"), msg.get("game_title")
        )).lower()
        return sum(h in text for h in ("/it/", "it-it", "it_it", "locale=it", "lang=it", ".it/"))

    def _raw_active_entries(self, entries: Iterable[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        now = datetime.now(timezone.utc)
        out = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            qid = self._quest_id(entry)
            if not qid.isdigit() or qid in KNOWN_TEST_QUEST_IDS:
                continue
            config = _quest_config(entry)
            if not config:
                continue
            starts = _parse_iso(config.get("starts_at"))
            expires = _parse_iso(config.get("expires_at"))
            if not starts or not expires or starts > now or expires <= now:
                continue
            name = str((config.get("messages") or {}).get("quest_name") or "")
            if name.upper().startswith("[TEST]"):
                continue
            out.append((entry, config))
        return out

    def _active_quests(self, entries: Iterable[Dict[str, Any]]) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
        groups: Dict[str, List[Tuple[int, Dict[str, Any], Dict[str, Any]]]] = {}
        for entry, config in self._raw_active_entries(entries):
            verdict, region_score, _reason = self._italy_verdict(entry.get("_italy_region"))
            if verdict != "allowed":
                continue
            family = self._family_key(entry, config)
            score = region_score + self._italian_hint(config) * 25 + self._entry_quality(entry)
            groups.setdefault(family, []).append((score, entry, config))
        selected = []
        for family, candidates in groups.items():
            candidates.sort(key=lambda x: x[0], reverse=True)
            _score, entry, config = candidates[0]
            selected.append((family, entry, config))
        selected.sort(
            key=lambda x: _parse_iso(x[2].get("starts_at")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return selected

    async def _scan_guild(self, guild: discord.Guild, *, force: bool = False) -> int:
        self._last_scan_errors[guild.id] = []
        settings = await self.config.guild(guild).all()
        if not settings.get("enabled") and not force:
            return 0
        channel = guild.get_channel(settings.get("channel_id") or 0)
        if not isinstance(channel, discord.TextChannel):
            self._last_scan_errors[guild.id].append("Canale Quest non configurato.")
            return 0

        active = self._active_quests(await self._fetch_quests())
        conf = self.config.guild(guild)
        old_order = [str(v) for v in settings.get("seen_keys") or []]
        old_seen = set(old_order)
        fam_order = [str(v) for v in settings.get("v50_seen_families") or []]
        id_order = [str(v) for v in settings.get("v50_seen_ids") or []]
        fam_seen, id_seen = set(fam_order), set(id_order)

        if not settings.get("initialized"):
            for family, entry, config in active:
                qid = self._quest_id(entry)
                fam_seen.add(family); fam_order.append(family)
                id_seen.add(qid); id_order.append(qid)
                old = _canonical_key(entry, config)
                if old not in old_seen:
                    old_seen.add(old); old_order.append(old)
            await conf.seen_keys.set(old_order[-500:])
            await conf.v50_seen_families.set(fam_order[-500:])
            await conf.v50_seen_ids.set(id_order[-500:])
            await conf.v50_migrated.set(True)
            await conf.initialized.set(True)
            return 0

        if not settings.get("v50_migrated"):
            for family, entry, config in active:
                if _canonical_key(entry, config) in old_seen:
                    qid = self._quest_id(entry)
                    if family not in fam_seen:
                        fam_seen.add(family); fam_order.append(family)
                    if qid not in id_seen:
                        id_seen.add(qid); id_order.append(qid)
            await conf.v50_migrated.set(True)

        sent = 0
        for family, entry, config in reversed(active):
            qid = self._quest_id(entry)
            old = _canonical_key(entry, config)
            if family in fam_seen or qid in id_seen:
                continue
            if old in old_seen:
                fam_seen.add(family); fam_order.append(family)
                id_seen.add(qid); id_order.append(qid)
                continue
            try:
                await self._send_quest(channel, guild, entry, config, test=False)
            except discord.Forbidden:
                self._last_scan_errors[guild.id].append(f"Quest `{qid}`: Forbidden.")
                continue
            except discord.HTTPException as exc:
                self._last_scan_errors[guild.id].append(
                    f"Quest `{qid}`: HTTP {getattr(exc, 'status', '?')} / {getattr(exc, 'code', '?')}."
                )
                continue
            fam_seen.add(family); fam_order.append(family)
            id_seen.add(qid); id_order.append(qid)
            old_seen.add(old); old_order.append(old)
            sent += 1

        await conf.seen_keys.set(old_order[-500:])
        await conf.v50_seen_families.set(fam_order[-500:])
        await conf.v50_seen_ids.set(id_order[-500:])
        return sent

    @QuestTrackerV49.quest.command(name="controlla", aliases=["check"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_check(self, ctx: commands.Context):
        async with ctx.typing():
            sent = await self._scan_guild(ctx.guild, force=True)
        errors = self._last_scan_errors.get(ctx.guild.id, [])
        text = f"Controllo completato. Nuove Quest inviate: **{sent}**."
        if errors:
            text += "\n" + "\n".join(f"- {e}" for e in errors[:8])
        await ctx.send(text[:2000], allowed_mentions=discord.AllowedMentions.none())

    @QuestTrackerV49.quest.command(name="cerca", aliases=["ispeziona", "inspect"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_lookup(self, ctx: commands.Context, quest_id: str):
        quest_id = str(quest_id).strip()
        async with ctx.typing():
            entries = await self._fetch_quests_live()
        entry = next((e for e in entries if self._quest_id(e) == quest_id), None)
        if not entry:
            return await ctx.send(f"Quest `{quest_id}` non trovata nelle fonti correnti.")
        config = _quest_config(entry)
        if not config:
            return await ctx.send("Quest trovata ma payload non valido.")
        verdict, _score, reason = self._italy_verdict(entry.get("_italy_region"))
        family = self._family_key(entry, config)
        selected = next((e for fam, e, _ in self._active_quests(entries) if fam == family), None)
        name = str((config.get("messages") or {}).get("quest_name") or "Discord Quest")
        embed = discord.Embed(title=f"Quest {quest_id}", colour=discord.Colour.blurple())
        embed.add_field(name="Nome", value=name[:1024], inline=False)
        embed.add_field(name="Verdetto Italia", value=f"{verdict} - {reason}"[:1024], inline=False)
        embed.add_field(name="ID scelto nella famiglia", value=self._quest_id(selected) if selected else "nessuno", inline=False)
        embed.add_field(name="Fonti Quest", value=", ".join(entry.get("_quest_sources") or ["cache"])[:1024], inline=False)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @QuestTrackerV49.quest.command(name="diagnostica", aliases=["debugquest", "regioni"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_diagnostics(self, ctx: commands.Context):
        async with ctx.typing():
            entries = await self._fetch_quests_live()
            raw = self._raw_active_entries(entries)
            selected = self._active_quests(entries)
        selected_by_family = {fam: self._quest_id(e) for fam, e, _ in selected}
        allowed = blocked = unknown = duplicates = 0
        lines = []
        for entry, config in raw:
            qid = self._quest_id(entry)
            verdict, _score, reason = self._italy_verdict(entry.get("_italy_region"))
            family = self._family_key(entry, config)
            chosen = selected_by_family.get(family)
            name = str((config.get("messages") or {}).get("quest_name") or "Discord Quest")
            if verdict == "blocked":
                blocked += 1; state = "NO IT"
            elif verdict == "unknown":
                unknown += 1; state = "ATTESA REGIONE"
            elif chosen != qid:
                allowed += 1; duplicates += 1; state = f"DUPLICATA -> {chosen}"
            else:
                allowed += 1; state = "IT OK / SCELTA"
            lines.append(f"**{state}** `{qid}` - {name}\n{reason}")
        header = (
            f"Varianti attive: **{len(raw)}** | IT ammesse: **{allowed}** | non-IT: **{blocked}** | "
            f"in attesa regione: **{unknown}** | campagne notificabili: **{len(selected)}** | dedupe: **{duplicates}**"
        )
        chunks, current = [], ""
        for line in lines:
            candidate = current + ("\n\n" if current else "") + line
            if len(candidate) > 3500:
                chunks.append(current); current = line
            else:
                current = candidate
        if current:
            chunks.append(current)
        if not chunks:
            return await ctx.send(header)
        for i, chunk in enumerate(chunks[:4], 1):
            await ctx.send(embed=discord.Embed(
                title=f"Diagnostica QuestTracker v5 ({i}/{min(len(chunks), 4)})",
                description=(header + "\n\n" + chunk)[:4096],
                colour=discord.Colour.blurple(),
            ))

    @QuestTrackerV49.quest.command(name="reinvia", aliases=["resend"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_resend(self, ctx: commands.Context, quest_id: str):
        quest_id = str(quest_id).strip()
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(settings.get("channel_id") or 0)
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send("Canale Quest non configurato.")
        entries = await self._fetch_quests_live()
        target = next(((e, c) for e, c in self._raw_active_entries(entries) if self._quest_id(e) == quest_id), None)
        if not target:
            return await ctx.send("Quest non attiva o non trovata.")
        entry, config = target
        verdict, _score, reason = self._italy_verdict(entry.get("_italy_region"))
        if verdict != "allowed":
            return await ctx.send(f"Non la reinvio: {verdict} - {reason}")
        await self._send_quest(channel, ctx.guild, entry, config, test=False)
        conf = self.config.guild(ctx.guild)
        family = self._family_key(entry, config)
        await conf.v50_seen_families.set(list(dict.fromkeys((await conf.v50_seen_families()) + [family]))[-500:])
        await conf.v50_seen_ids.set(list(dict.fromkeys((await conf.v50_seen_ids()) + [quest_id]))[-500:])
        await conf.seen_keys.set(list(dict.fromkeys((await conf.seen_keys()) + [_canonical_key(entry, config)]))[-500:])
        await ctx.send(f"Quest `{quest_id}` reinviata in {channel.mention}.")

    @QuestTrackerV49.quest.command(name="status")
    async def quest_status(self, ctx: commands.Context):
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(settings.get("channel_id") or 0)
        role = ctx.guild.get_role(settings.get("role_id") or 0)
        qs = "\n".join(f"{label}: {self._source_counts.get(label, 0)}" for label, _ in self.QUEST_SOURCES)
        rs = []
        for label, _ in self.REGION_SOURCES:
            count = len(self._region_maps_by_source.get(label, {}))
            error = self._source_errors.get(label)
            rs.append(f"{label}: {count}" + (f" ({error})" if error else ""))
        embed = discord.Embed(title="QuestTracker", colour=discord.Colour.blurple())
        embed.add_field(name="Versione", value=self.__version__, inline=True)
        embed.add_field(name="Stato", value="Attivo" if settings.get("enabled") else "Disattivato", inline=True)
        embed.add_field(name="Polling", value="15 secondi", inline=True)
        embed.add_field(name="Filtro Italia", value="STRICT / fail-closed", inline=True)
        embed.add_field(name="Canale", value=channel.mention if channel else "Non configurato", inline=True)
        embed.add_field(name="Ruolo", value=role.mention if role else "Nessuno", inline=True)
        embed.add_field(name="Fonti Quest", value=qs[:1024] or "n/a", inline=False)
        embed.add_field(name="Fonti regioni", value="\n".join(rs)[:1024] or "n/a", inline=False)
        embed.add_field(
            name="Sicurezza",
            value="Senza una regione affidabile la Quest resta in attesa e NON viene inviata. Ricontrollo ogni 15s, con backoff automatico sui 429.",
            inline=False,
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
