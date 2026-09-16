from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red


CONFIG_ID = 84418305220260914
API_SERVICE = "questtracker"

SOURCE1_URL = "https://discord.com/api/v10/quests/@me"
SOURCE2_QUESTS_URL = "https://api.discordquest.com/api/quests"
SOURCE2_REGIONS_URL = "https://api.discordquest.com/api/regions"
SOURCE3_URL = "https://raw.githubusercontent.com/aamiaa/discord-api-diff/refs/heads/main/quests.json"

DEFAULT_MESSAGE_TEMPLATE = "{role} [Apri la Quest](quest)"
AUTO_LINK_RE = re.compile(r"\]\(\s*(?:quest|link|url)\s*\)", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")
ALLOWED_PLACEHOLDERS = {
    "role",
    "link",
    "url",
    "quest_url",
    "name",
    "game",
    "reward",
    "id",
}

ITALY_CODES = {"it", "it-it", "ita", "italy", "italia"}
EUROPE_CODES = {"eu", "europe", "eea", "eur"}
ITALY_REGION_CODES = ITALY_CODES | EUROPE_CODES

KNOWN_TEST_QUEST_IDS = {
    "1193992107035983872",
    "1417206015245418566",
    "1223393873447878656",
    "1276640451235156082",
    "1483951358322147380",
    "1519474065293967471",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _norm_region(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def _quest_config(entry: Dict[str, Any]) -> Dict[str, Any]:
    config = entry.get("config")
    if isinstance(config, dict):
        return config
    if any(key in entry for key in ("starts_at", "expires_at", "messages", "application")):
        return entry
    return {}


def _quest_id(entry: Any) -> str:
    if not isinstance(entry, dict):
        return ""
    config = _quest_config(entry)
    return str(
        entry.get("id")
        or entry.get("quest_id")
        or config.get("id")
        or config.get("quest_id")
        or ""
    ).strip()


def _quest_name(entry: Dict[str, Any]) -> str:
    config = _quest_config(entry)
    messages = config.get("messages") if isinstance(config.get("messages"), dict) else {}
    app = config.get("application") if isinstance(config.get("application"), dict) else {}
    return str(
        messages.get("quest_name")
        or messages.get("game_title")
        or app.get("name")
        or config.get("application_name")
        or "Discord Quest"
    )


def _game_name(entry: Dict[str, Any]) -> str:
    config = _quest_config(entry)
    messages = config.get("messages") if isinstance(config.get("messages"), dict) else {}
    app = config.get("application") if isinstance(config.get("application"), dict) else {}
    return str(
        app.get("name")
        or messages.get("game_title")
        or config.get("application_name")
        or "Discord"
    )


def _rewards(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    reward_config = config.get("rewards_config") if isinstance(config.get("rewards_config"), dict) else {}
    rewards = reward_config.get("rewards") or config.get("rewards") or []
    return [item for item in rewards if isinstance(item, dict)]


def _reward_name(entry: Dict[str, Any]) -> str:
    config = _quest_config(entry)
    rewards = _rewards(config)
    if not rewards:
        messages = config.get("messages") if isinstance(config.get("messages"), dict) else {}
        return str(messages.get("reward_name") or "Ricompensa Quest")
    reward = rewards[0]
    orb = reward.get("orb_quantity")
    if orb is not None:
        return f"{orb} Discord Orbs"
    messages = reward.get("messages") if isinstance(reward.get("messages"), dict) else {}
    return str(messages.get("name") or reward.get("name") or "Ricompensa Quest")


def _quest_url(qid: str) -> str:
    return f"https://discord.com/quests/{qid}"


def _family_key(entry: Dict[str, Any]) -> str:
    config = _quest_config(entry)
    messages = config.get("messages") if isinstance(config.get("messages"), dict) else {}
    app = config.get("application") if isinstance(config.get("application"), dict) else {}
    app_id = str(app.get("id") or config.get("application_id") or "")
    name = re.sub(r"\s+", " ", str(messages.get("quest_name") or "").strip().lower())
    starts = str(config.get("starts_at") or "")
    expires = str(config.get("expires_at") or "")
    reward_sig = ",".join(
        sorted(
            f"{r.get('sku_id', '')}:{r.get('orb_quantity', '')}:{(r.get('messages') or {}).get('name', r.get('name', ''))}"
            for r in _rewards(config)
        )
    )
    raw = "|".join((app_id, name, starts, expires, reward_sig))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _merge_entry(old: Optional[Dict[str, Any]], new: Dict[str, Any], source: str) -> Dict[str, Any]:
    result: Dict[str, Any] = dict(old or {})
    for key, value in new.items():
        if key == "config" and isinstance(value, dict):
            existing = result.get("config") if isinstance(result.get("config"), dict) else {}
            merged_config = dict(existing)
            merged_config.update(value)
            result["config"] = merged_config
        elif value is not None or key not in result:
            result[key] = value
    sources = list(result.get("_sources") or [])
    if source not in sources:
        sources.append(source)
    result["_sources"] = sources
    return result


def _region_map(payload: Any) -> Dict[str, Dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get("quests")
        if not isinstance(items, list):
            items = payload.get("regions")
    else:
        items = payload
    if not isinstance(items, list):
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or item.get("quest_id") or "").strip()
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
        }
    return out


def _italy_verdict(record: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    if not isinstance(record, dict):
        return "unknown", "nessun dato regionale"
    include = {_norm_region(v) for v in record.get("include", []) if v}
    exclude = {_norm_region(v) for v in record.get("exclude", []) if v}
    if exclude & ITALY_REGION_CODES:
        return "blocked", "Italia/Europa esclusa"
    if include:
        if include & ITALY_REGION_CODES:
            return "allowed", "Italia/Europa inclusa"
        return "blocked", "allow-list senza Italia/Europa"
    if record.get("is_global") is True:
        return "allowed", "quest globale"
    if exclude:
        return "allowed", "blacklist senza Italia/Europa"
    return "unknown", "record regionale incompleto"


def _extract_list(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("quests", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


class QuestTracker(commands.Cog):
    """QuestTracker standalone: 3 sorgenti, filtro Italia e messaggio nativo personalizzabile."""

    __author__ = "danyx64"
    __version__ = "8.0.0"

    POLL_SECONDS = 15
    PUBLIC_CACHE_SECONDS = 30
    STALE_CACHE_SECONDS = 600
    SELF_CACHE_SECONDS_DEFAULT = 30
    AUTO_SEND_COOLDOWN_SECONDS = 60

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_ID, force_registration=True)
        self.config.register_guild(
            enabled=False,
            channel_id=None,
            role_id=None,
            ping_role=True,
            seen_keys=[],
            initialized=False,
            message_template=DEFAULT_MESSAGE_TEMPLATE,
            strict_region=True,
            v80_baselined=False,
            v80_seen_ids=[],
            v80_seen_families=[],
            v80_last_auto_send=None,
        )
        self.config.register_global(
            source1_enabled=True,
            source2_enabled=True,
            source3_enabled=True,
            source1_locale="it-IT",
            source1_timezone="Europe/Rome",
            source1_interval_seconds=self.SELF_CACHE_SECONDS_DEFAULT,
        )

        self.session: Optional[aiohttp.ClientSession] = None
        self._scan_lock = asyncio.Lock()
        self._source_cache: Dict[str, Any] = {}
        self._source_cache_at: Dict[str, datetime] = {}
        self._source_backoff_until: Dict[str, datetime] = {}
        self._source_errors: Dict[str, Optional[str]] = {}
        self._source_counts: Dict[str, int] = {}
        self._self_available: Set[str] = set()
        self._self_excluded: Set[str] = set()
        self._self_cache_at: Optional[datetime] = None
        self._self_backoff_until: Optional[datetime] = None
        self._self_status = "non configurato"
        self._self_error: Optional[str] = None
        self._self_live = False
        self._last_region_count = 0
        self._last_merged_count = 0

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        if not self.quest_scan.is_running():
            self.quest_scan.start()

    def cog_unload(self) -> None:
        if self.quest_scan.is_running():
            self.quest_scan.cancel()
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
        return

    async def _fetch_public_json(self, label: str, url: str) -> Tuple[Any, bool]:
        now = _utcnow()
        cached = self._source_cache.get(label)
        cached_at = self._source_cache_at.get(label)
        if cached_at and (now - cached_at).total_seconds() < self.PUBLIC_CACHE_SECONDS:
            return cached, False
        blocked_until = self._source_backoff_until.get(label)
        if blocked_until and blocked_until > now:
            if cached_at and (now - cached_at).total_seconds() <= self.STALE_CACHE_SECONDS:
                return cached, False
            return None, False
        if not self.session or self.session.closed:
            self._source_errors[label] = "sessione HTTP non disponibile"
            return cached, False
        try:
            async with self.session.get(url, headers={"Accept": "application/json"}) as response:
                if response.status == 429:
                    retry_after = 60.0
                    try:
                        body = await response.json(content_type=None)
                        retry_after = float(body.get("retry_after", retry_after)) if isinstance(body, dict) else retry_after
                    except (ValueError, TypeError, aiohttp.ContentTypeError):
                        raw = response.headers.get("Retry-After")
                        try:
                            retry_after = float(raw) if raw else retry_after
                        except (TypeError, ValueError):
                            pass
                    retry_after = max(15.0, min(retry_after, 3600.0))
                    self._source_backoff_until[label] = now + timedelta(seconds=retry_after)
                    self._source_errors[label] = f"HTTP 429 ({int(retry_after)}s)"
                    return cached, False
                if response.status != 200:
                    self._source_errors[label] = f"HTTP {response.status}"
                    if response.status >= 500:
                        self._source_backoff_until[label] = now + timedelta(seconds=20)
                    return cached, False
                payload = await response.json(content_type=None)
                self._source_cache[label] = payload
                self._source_cache_at[label] = now
                self._source_backoff_until.pop(label, None)
                self._source_errors[label] = None
                return payload, True
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            self._source_errors[label] = f"{type(exc).__name__}: {exc}"
            return cached, False

    def _self_snapshot(self, status: str) -> Dict[str, Any]:
        quests = _extract_list(self._source_cache.get("source1-selfbot"))
        return {
            "usable": bool(self._self_cache_at),
            "live": False,
            "status": status,
            "quests": quests,
            "available_ids": set(self._self_available),
            "excluded_ids": set(self._self_excluded),
        }

    async def _fetch_selfbot(self, *, force: bool = False) -> Dict[str, Any]:
        if not await self.config.source1_enabled():
            self._self_status = "disattivata"
            self._self_error = None
            self._self_live = False
            return {"usable": False, "live": False, "status": "disabled", "quests": [], "available_ids": set(), "excluded_ids": set()}
        tokens = await self.bot.get_shared_api_tokens(API_SERVICE)
        token = str(tokens.get("user_token") or "").strip()
        if not token:
            self._self_status = "token mancante"
            self._self_error = None
            self._self_live = False
            return {"usable": False, "live": False, "status": "missing-token", "quests": [], "available_ids": set(), "excluded_ids": set()}
        now = _utcnow()
        interval = max(15, min(int(await self.config.source1_interval_seconds()), 900))
        if not force and self._self_cache_at and (now - self._self_cache_at).total_seconds() < interval:
            self._self_status = "cache valida"
            self._self_live = False
            return self._self_snapshot("cache")
        if not force and self._self_backoff_until and self._self_backoff_until > now:
            remaining = int((self._self_backoff_until - now).total_seconds())
            if self._self_cache_at and (now - self._self_cache_at).total_seconds() <= self.STALE_CACHE_SECONDS:
                self._self_status = f"backoff, cache ({remaining}s)"
                return self._self_snapshot("backoff-cache")
            self._self_status = f"backoff ({remaining}s)"
            return {"usable": False, "live": False, "status": "backoff", "quests": [], "available_ids": set(), "excluded_ids": set()}
        if not self.session or self.session.closed:
            self._self_status = "sessione HTTP non disponibile"
            return {"usable": False, "live": False, "status": "no-session", "quests": [], "available_ids": set(), "excluded_ids": set()}
        locale = str(await self.config.source1_locale() or "it-IT").strip() or "it-IT"
        timezone_name = str(await self.config.source1_timezone() or "Europe/Rome").strip() or "Europe/Rome"
        headers = {
            "Authorization": token,
            "Accept": "application/json",
            "Accept-Language": f"{locale},it;q=0.9,en;q=0.7",
            "X-Discord-Locale": locale,
            "X-Discord-Timezone": timezone_name,
            "User-Agent": "QuestTracker/8.0 (Red-DiscordBot)",
        }
        try:
            async with self.session.get(SOURCE1_URL, headers=headers) as response:
                if response.status == 429:
                    retry_after = 60.0
                    try:
                        data = await response.json(content_type=None)
                        retry_after = float(data.get("retry_after", retry_after)) if isinstance(data, dict) else retry_after
                    except (ValueError, TypeError, aiohttp.ContentTypeError):
                        pass
                    retry_after = max(15.0, min(retry_after, 3600.0))
                    self._self_backoff_until = now + timedelta(seconds=retry_after)
                    self._self_error = f"HTTP 429 ({int(retry_after)}s)"
                    self._self_status = "rate limit"
                    self._self_live = False
                    if self._self_cache_at and (now - self._self_cache_at).total_seconds() <= self.STALE_CACHE_SECONDS:
                        return self._self_snapshot("rate-limit-cache")
                    return {"usable": False, "live": False, "status": "rate-limit", "quests": [], "available_ids": set(), "excluded_ids": set()}
                if response.status in (401, 403):
                    self._self_status = f"autenticazione rifiutata ({response.status})"
                    self._self_error = f"HTTP {response.status}"
                    self._self_live = False
                    return {"usable": False, "live": False, "status": "auth-failed", "quests": [], "available_ids": set(), "excluded_ids": set()}
                if response.status != 200:
                    self._self_status = f"errore HTTP {response.status}"
                    self._self_error = f"HTTP {response.status}"
                    self._self_live = False
                    if response.status >= 500:
                        self._self_backoff_until = now + timedelta(seconds=20)
                    if self._self_cache_at and (now - self._self_cache_at).total_seconds() <= self.STALE_CACHE_SECONDS:
                        return self._self_snapshot("http-cache")
                    return {"usable": False, "live": False, "status": "http-error", "quests": [], "available_ids": set(), "excluded_ids": set()}
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            self._self_status = "errore rete"
            self._self_error = f"{type(exc).__name__}: {exc}"
            self._self_live = False
            if self._self_cache_at and (now - self._self_cache_at).total_seconds() <= self.STALE_CACHE_SECONDS:
                return self._self_snapshot("network-cache")
            return {"usable": False, "live": False, "status": "network-error", "quests": [], "available_ids": set(), "excluded_ids": set()}
        quests = _extract_list(payload)
        excluded_raw = payload.get("excluded_quests", []) if isinstance(payload, dict) else []
        excluded = {_quest_id(item) for item in excluded_raw if isinstance(item, dict)}
        excluded.discard("")
        available = {_quest_id(item) for item in quests}
        available.discard("")
        self._source_cache["source1-selfbot"] = payload
        self._source_cache_at["source1-selfbot"] = now
        self._self_available = available
        self._self_excluded = excluded
        self._self_cache_at = now
        self._self_backoff_until = None
        self._self_status = "operativa"
        self._self_error = None
        self._self_live = True
        self._source_counts["source1-selfbot"] = len(quests)
        return {"usable": True, "live": True, "status": "live", "quests": quests, "available_ids": available, "excluded_ids": excluded}

    async def _fetch_all(self, *, force_selfbot: bool = False) -> List[Dict[str, Any]]:
        use2 = bool(await self.config.source2_enabled())
        use3 = bool(await self.config.source3_enabled())
        source1_task = asyncio.create_task(self._fetch_selfbot(force=force_selfbot))
        source2_quests_task = asyncio.create_task(self._fetch_public_json("source2-quests", SOURCE2_QUESTS_URL)) if use2 else None
        source2_regions_task = asyncio.create_task(self._fetch_public_json("source2-regions", SOURCE2_REGIONS_URL)) if use2 else None
        source3_task = asyncio.create_task(self._fetch_public_json("source3-api-diff", SOURCE3_URL)) if use3 else None
        source1 = await source1_task
        source2_payload = (await source2_quests_task)[0] if source2_quests_task else None
        regions_payload = (await source2_regions_task)[0] if source2_regions_task else None
        source3_payload = (await source3_task)[0] if source3_task else None
        source2_entries = _extract_list(source2_payload)
        source3_entries = _extract_list(source3_payload)
        source1_entries = list(source1.get("quests") or [])
        regions = _region_map(regions_payload)
        self._source_counts["source2-quests"] = len(source2_entries)
        self._source_counts["source3-api-diff"] = len(source3_entries)
        self._last_region_count = len(regions)
        merged: Dict[str, Dict[str, Any]] = {}
        for label, entries in (
            ("source3-api-diff", source3_entries),
            ("source2-discordquest", source2_entries),
            ("source1-selfbot", source1_entries),
        ):
            for entry in entries:
                qid = _quest_id(entry)
                if qid:
                    merged[qid] = _merge_entry(merged.get(qid), entry, label)
        probe_usable = bool(source1.get("usable"))
        available_ids = set(source1.get("available_ids") or set())
        excluded_ids = set(source1.get("excluded_ids") or set())
        for qid, entry in merged.items():
            if probe_usable:
                if qid in available_ids:
                    verdict, reason, via = "allowed", "presente nell'account osservato", "source1-selfbot"
                elif qid in excluded_ids:
                    verdict, reason, via = "blocked", "esclusa per l'account osservato", "source1-selfbot"
                else:
                    verdict, reason, via = "unknown", "non presente nella risposta account", "source1-selfbot"
            else:
                verdict, reason = _italy_verdict(regions.get(qid))
                via = "source2-regions"
            entry["_availability"] = verdict
            entry["_availability_reason"] = reason
            entry["_availability_source"] = via
            entry["_italy_region"] = regions.get(qid)
        self._last_merged_count = len(merged)
        return list(merged.values())

    @staticmethod
    def _entry_is_active(entry: Dict[str, Any]) -> bool:
        qid = _quest_id(entry)
        if not qid or qid in KNOWN_TEST_QUEST_IDS:
            return False
        name = _quest_name(entry)
        if name.upper().startswith("[TEST]"):
            return False
        config = _quest_config(entry)
        starts = _parse_iso(config.get("starts_at"))
        expires = _parse_iso(config.get("expires_at"))
        now = _utcnow()
        if starts and expires:
            return starts <= now < expires
        return entry.get("_availability_source") == "source1-selfbot" and entry.get("_availability") == "allowed"

    async def _active_for_guild(self, guild: discord.Guild) -> List[Dict[str, Any]]:
        strict = bool(await self.config.guild(guild).strict_region())
        entries = await self._fetch_all()
        active: List[Dict[str, Any]] = []
        family_seen: Set[str] = set()
        def sort_key(entry: Dict[str, Any]) -> datetime:
            starts = _parse_iso(_quest_config(entry).get("starts_at"))
            return starts or datetime.min.replace(tzinfo=timezone.utc)
        for entry in sorted(entries, key=sort_key, reverse=True):
            if not self._entry_is_active(entry):
                continue
            verdict = str(entry.get("_availability") or "unknown")
            if verdict == "blocked" or (strict and verdict != "allowed"):
                continue
            family = _family_key(entry)
            if family in family_seen:
                continue
            family_seen.add(family)
            active.append(entry)
        return active

    @staticmethod
    def _unknown_placeholders(template: str) -> List[str]:
        return sorted({m.group(1) for m in PLACEHOLDER_RE.finditer(template) if m.group(1) not in ALLOWED_PLACEHOLDERS})

    async def _render_message(self, guild: discord.Guild, entry: Dict[str, Any], *, test: bool = False) -> str:
        settings = await self.config.guild(guild).all()
        template = str(settings.get("message_template") or DEFAULT_MESSAGE_TEMPLATE)
        qid = _quest_id(entry)
        url = _quest_url(qid)
        role = guild.get_role(settings.get("role_id") or 0)
        role_text = role.mention if role else ""
        values = {
            "role": role_text,
            "link": f"[.]({url})",
            "url": url,
            "quest_url": url,
            "name": _quest_name(entry),
            "game": _game_name(entry),
            "reward": _reward_name(entry),
            "id": qid,
        }
        rendered = template
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", str(value))
        rendered = AUTO_LINK_RE.sub(f"]({url})", rendered)
        rendered = re.sub(r"[ \t]+\n", "\n", rendered)
        rendered = re.sub(r" {2,}", " ", rendered).strip()
        if url not in rendered:
            rendered = (rendered + f" [.]({url})").strip()
        if test:
            rendered = "[TEST] " + rendered
        return rendered[:2000]

    async def _send_quest(self, channel: discord.TextChannel, guild: discord.Guild, entry: Dict[str, Any], *, test: bool = False) -> None:
        settings = await self.config.guild(guild).all()
        role = guild.get_role(settings.get("role_id") or 0)
        ping = bool(settings.get("ping_role", True)) and not test and role is not None
        allowed = discord.AllowedMentions(roles=ping, users=False, everyone=False, replied_user=False)
        await channel.send(await self._render_message(guild, entry, test=test), allowed_mentions=allowed)

    async def _baseline_v8(self, guild: discord.Guild, active: Iterable[Dict[str, Any]]) -> int:
        ids: List[str] = []
        families: List[str] = []
        for entry in active:
            qid = _quest_id(entry)
            family = _family_key(entry)
            if qid and qid not in ids:
                ids.append(qid)
            if family and family not in families:
                families.append(family)
        conf = self.config.guild(guild)
        await conf.v80_seen_ids.set(ids[-1000:])
        await conf.v80_seen_families.set(families[-1000:])
        await conf.v80_baselined.set(True)
        await conf.initialized.set(True)
        return len(ids)

    async def _scan_guild(self, guild: discord.Guild, *, force: bool = False) -> int:
        settings = await self.config.guild(guild).all()
        if not settings.get("enabled") and not force:
            return 0
        channel = guild.get_channel(settings.get("channel_id") or 0)
        if not isinstance(channel, discord.TextChannel):
            return 0
        active = await self._active_for_guild(guild)
        if not settings.get("v80_baselined"):
            await self._baseline_v8(guild, active)
            return 0
        seen_ids_list = [str(v) for v in settings.get("v80_seen_ids") or []]
        seen_families_list = [str(v) for v in settings.get("v80_seen_families") or []]
        seen_ids = set(seen_ids_list)
        seen_families = set(seen_families_list)
        now = _utcnow()
        last_auto = _parse_iso(settings.get("v80_last_auto_send"))
        if not force and last_auto and (now - last_auto).total_seconds() < self.AUTO_SEND_COOLDOWN_SECONDS:
            return 0
        candidates = [entry for entry in active if _quest_id(entry) not in seen_ids and _family_key(entry) not in seen_families]
        if not candidates:
            return 0
        entry = candidates[-1]
        try:
            await self._send_quest(channel, guild, entry, test=False)
        except (discord.Forbidden, discord.HTTPException):
            return 0
        qid = _quest_id(entry)
        family = _family_key(entry)
        seen_ids_list.append(qid)
        seen_families_list.append(family)
        conf = self.config.guild(guild)
        await conf.v80_seen_ids.set(seen_ids_list[-1000:])
        await conf.v80_seen_families.set(seen_families_list[-1000:])
        await conf.v80_last_auto_send.set(now.isoformat())
        return 1

    @tasks.loop(seconds=POLL_SECONDS)
    async def quest_scan(self) -> None:
        if self._scan_lock.locked():
            return
        async with self._scan_lock:
            for guild in list(self.bot.guilds):
                try:
                    await self._scan_guild(guild)
                except Exception:
                    continue

    @quest_scan.before_loop
    async def before_quest_scan(self) -> None:
        await self.bot.wait_until_red_ready()
        await asyncio.sleep(10)

    @commands.group(name="quest", aliases=["quests"], invoke_without_command=True)
    @commands.guild_only()
    async def quest(self, ctx: commands.Context) -> None:
        """Configura e controlla QuestTracker."""
        await ctx.send_help(ctx.command)

    @quest.command(name="setup", aliases=["configura"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_setup(self, ctx: commands.Context, channel: discord.TextChannel, role: Optional[discord.Role] = None) -> None:
        conf = self.config.guild(ctx.guild)
        await conf.channel_id.set(channel.id)
        if role is not None:
            await conf.role_id.set(role.id)
        await conf.enabled.set(True)
        active = await self._active_for_guild(ctx.guild)
        baseline = await self._baseline_v8(ctx.guild, active)
        await ctx.send(
            f"✅ QuestTracker v{self.__version__} attivato in {channel.mention}. "
            f"Ruolo: {role.mention if role else 'mantengo quello gia salvato / nessuno'}. "
            f"Baseline corrente: **{baseline}** Quest."
        )

    @quest.command(name="canale", aliases=["channel"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_channel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"✅ Canale Quest impostato su {channel.mention}.")

    @quest.command(name="ruolo", aliases=["role"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_role(self, ctx: commands.Context, role: discord.Role) -> None:
        await self.config.guild(ctx.guild).role_id.set(role.id)
        await ctx.send(f"✅ Ruolo Quest impostato su {role.mention}.")

    @quest.command(name="noruolo", aliases=["norole", "ruolooff"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_no_role(self, ctx: commands.Context) -> None:
        await self.config.guild(ctx.guild).role_id.set(None)
        await ctx.send("✅ Ruolo Quest rimosso.")

    @quest.command(name="ping")
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_ping(self, ctx: commands.Context, state: Optional[str] = None) -> None:
        conf = self.config.guild(ctx.guild)
        if state is None:
            return await ctx.send(f"Ping ruolo: **{'attivo' if await conf.ping_role() else 'disattivato'}**.")
        value = state.lower().strip()
        if value in {"on", "si", "yes", "true", "1", "attiva", "attivo"}:
            await conf.ping_role.set(True)
            return await ctx.send("✅ Ping ruolo attivato.")
        if value in {"off", "no", "false", "0", "disattiva", "disattivo"}:
            await conf.ping_role.set(False)
            return await ctx.send("✅ Ping ruolo disattivato.")
        await ctx.send("Usa `.quest ping on` oppure `.quest ping off`.")

    @quest.command(name="messaggio", aliases=["message", "testo"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_message(self, ctx: commands.Context, *, testo: Optional[str] = None) -> None:
        conf = self.config.guild(ctx.guild)
        if testo is None:
            current = str(await conf.message_template() or DEFAULT_MESSAGE_TEMPLATE)
            return await ctx.send(
                "**Messaggio attuale**\n"
                f"```\n{current}\n```\n"
                "Esempio: `.quest messaggio 🔔 {role} [APRI LA QUEST](quest)`\n"
                "`(quest)`, `(link)` e `(url)` vengono sostituiti con il link reale. Reset: `.quest messaggio reset`."
            )
        template = str(testo).replace("\\n", "\n").strip()
        if template.lower() in {"reset", "default", "predefinito"}:
            await conf.message_template.set(DEFAULT_MESSAGE_TEMPLATE)
            return await ctx.send(f"✅ Messaggio ripristinato: `{DEFAULT_MESSAGE_TEMPLATE}`")
        if not template:
            return await ctx.send("❌ Il messaggio non puo essere vuoto.")
        if len(template) > 1800:
            return await ctx.send("❌ Il messaggio puo avere massimo 1800 caratteri.")
        unknown = self._unknown_placeholders(template)
        if unknown:
            return await ctx.send("❌ Placeholder sconosciuti: " + ", ".join(f"`{{{x}}}`" for x in unknown))
        await conf.message_template.set(template)
        await ctx.send("✅ Messaggio Quest aggiornato. Usa `.quest test` per vedere l'anteprima.")

    @quest.command(name="placeholders", aliases=["vars", "variabili"])
    async def quest_placeholders(self, ctx: commands.Context) -> None:
        await ctx.send(
            "**Placeholder disponibili**\n"
            "`{role}` ruolo configurato\n"
            "`{link}` link classico mascherato `[.]`\n"
            "`{url}` / `{quest_url}` URL puro\n"
            "`{name}` nome Quest | `{game}` gioco/app | `{reward}` ricompensa | `{id}` ID Quest\n"
            "Markdown automatico: `[qualsiasi testo](quest)` oppure `(link)` / `(url)`."
        )

    @quest.command(name="strict", aliases=["italia", "regionstrict"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_strict(self, ctx: commands.Context, state: Optional[str] = None) -> None:
        conf = self.config.guild(ctx.guild)
        if state is None:
            return await ctx.send(f"Filtro Italia strict: **{'attivo' if await conf.strict_region() else 'disattivato'}**.")
        value = state.lower().strip()
        if value in {"on", "si", "yes", "true", "1"}:
            await conf.strict_region.set(True)
            return await ctx.send("✅ Strict Italia attivo: UNKNOWN non viene notificato.")
        if value in {"off", "no", "false", "0"}:
            await conf.strict_region.set(False)
            return await ctx.send("⚠️ Strict Italia disattivato: anche le Quest con regione UNKNOWN possono essere notificate.")
        await ctx.send("Usa `.quest strict on` oppure `.quest strict off`.")

    @quest.command(name="on", aliases=["enable", "attiva"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_on(self, ctx: commands.Context) -> None:
        if not await self.config.guild(ctx.guild).channel_id():
            return await ctx.send("❌ Prima usa `.quest setup #canale`.")
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("✅ QuestTracker attivato.")

    @quest.command(name="off", aliases=["disable", "disattiva"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_off(self, ctx: commands.Context) -> None:
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("⏸️ QuestTracker disattivato.")

    @quest.command(name="test", aliases=["preview", "anteprima"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_test(self, ctx: commands.Context, quest_id: Optional[str] = None) -> None:
        active = await self._active_for_guild(ctx.guild)
        entry = next((q for q in active if _quest_id(q) == str(quest_id)), None) if quest_id else (active[0] if active else None)
        if entry is None:
            return await ctx.send("❌ Non trovo una Quest attiva compatibile con questo filtro.")
        if not isinstance(ctx.channel, discord.TextChannel):
            return await ctx.send("❌ Usa il comando in un canale testuale.")
        await self._send_quest(ctx.channel, ctx.guild, entry, test=True)

    @quest.command(name="attive", aliases=["active"])
    async def quest_active(self, ctx: commands.Context) -> None:
        active = await self._active_for_guild(ctx.guild)
        if not active:
            return await ctx.send("Al momento non trovo Quest attive compatibili con il filtro configurato.")
        lines = []
        for entry in active[:20]:
            qid = _quest_id(entry)
            verdict = str(entry.get("_availability") or "unknown").upper()
            via = str(entry.get("_availability_source") or "-")
            lines.append(f"• **{_quest_name(entry)}** — `{qid}` — **{verdict}** via `{via}`")
        if len(active) > 20:
            lines.append(f"…e altre {len(active) - 20}.")
        await ctx.send(embed=discord.Embed(title=f"Quest attive: {len(active)}", description="\n".join(lines), colour=discord.Colour.blurple()))

    @quest.command(name="controlla", aliases=["check"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_check(self, ctx: commands.Context) -> None:
        sent = await self._scan_guild(ctx.guild, force=True)
        await ctx.send(f"✅ Controllo completato. Notifiche inviate: **{sent}**.")

    @quest.command(name="reinvia", aliases=["resend", "forza", "force"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_resend(self, ctx: commands.Context, quest_id: str) -> None:
        entries = await self._fetch_all(force_selfbot=True)
        entry = next((item for item in entries if _quest_id(item) == str(quest_id)), None)
        if entry is None:
            return await ctx.send("❌ Quest non trovata nelle tre sorgenti.")
        channel_id = await self.config.guild(ctx.guild).channel_id()
        channel = ctx.guild.get_channel(channel_id or 0)
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send("❌ Canale Quest non configurato.")
        await self._send_quest(channel, ctx.guild, entry, test=False)
        await ctx.send(f"✅ Quest `{quest_id}` reinviata in {channel.mention}.")

    @quest.command(name="fonti", aliases=["sources", "sorgenti"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_sources(self, ctx: commands.Context) -> None:
        tokens = await self.bot.get_shared_api_tokens(API_SERVICE)
        s1 = bool(await self.config.source1_enabled())
        s2 = bool(await self.config.source2_enabled())
        s3 = bool(await self.config.source3_enabled())
        embed = discord.Embed(title="QuestTracker - 3 sorgenti", colour=discord.Colour.blurple())
        embed.add_field(name="1. Account probe / selfbot", value=f"Abilitata: **{'si' if s1 else 'no'}**\nToken: **{'configurato' if tokens.get('user_token') else 'mancante'}**\nStato: **{self._self_status}**\nQuest cache: **{self._source_counts.get('source1-selfbot', 0)}**\nErrore: `{self._self_error or 'nessuno'}`", inline=False)
        embed.add_field(name="2. DiscordQuest + regioni", value=f"Abilitata: **{'si' if s2 else 'no'}**\nQuest: **{self._source_counts.get('source2-quests', 0)}** | regioni: **{self._last_region_count}**\nErrore quest: `{self._source_errors.get('source2-quests') or 'nessuno'}`\nErrore regioni: `{self._source_errors.get('source2-regions') or 'nessuno'}`", inline=False)
        embed.add_field(name="3. discord-api-diff", value=f"Abilitata: **{'si' if s3 else 'no'}**\nQuest: **{self._source_counts.get('source3-api-diff', 0)}**\nErrore: `{self._source_errors.get('source3-api-diff') or 'nessuno'}`", inline=False)
        embed.add_field(name="Merge", value=f"Ultimo catalogo unificato: **{self._last_merged_count}** Quest.", inline=False)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @quest.command(name="status")
    async def quest_status(self, ctx: commands.Context) -> None:
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(settings.get("channel_id") or 0)
        role = ctx.guild.get_role(settings.get("role_id") or 0)
        embed = discord.Embed(title="QuestTracker", colour=discord.Colour.blurple())
        embed.add_field(name="Versione", value=self.__version__, inline=True)
        embed.add_field(name="Stato", value="✅ Attivo" if settings.get("enabled") else "⏸️ Disattivato", inline=True)
        embed.add_field(name="Polling", value=f"{self.POLL_SECONDS}s", inline=True)
        embed.add_field(name="Canale", value=channel.mention if channel else "Non configurato", inline=True)
        embed.add_field(name="Ruolo", value=role.mention if role else "Nessuno", inline=True)
        embed.add_field(name="Ping", value="✅" if settings.get("ping_role", True) else "⛔", inline=True)
        embed.add_field(name="Strict Italia", value="✅" if settings.get("strict_region", True) else "⚠️ OFF", inline=True)
        embed.add_field(name="Source 1", value=self._self_status, inline=True)
        embed.add_field(name="Baseline v8", value="✅" if settings.get("v80_baselined") else "da creare", inline=True)
        template = str(settings.get("message_template") or DEFAULT_MESSAGE_TEMPLATE)
        embed.add_field(name="Messaggio", value=f"```\n{template[:950]}\n```", inline=False)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @quest.command(name="version", aliases=["versione"])
    async def quest_version(self, ctx: commands.Context) -> None:
        await ctx.send(f"QuestTracker **v{self.__version__}** standalone — Source 1 + Source 2 + Source 3.")

    @quest.group(name="selfbot", aliases=["probe", "source1"], invoke_without_command=True)
    @commands.is_owner()
    async def quest_selfbot(self, ctx: commands.Context) -> None:
        tokens = await self.bot.get_shared_api_tokens(API_SERVICE)
        await ctx.send(
            "**Source 1 - account probe/selfbot**\n"
            f"Abilitata: **{'si' if await self.config.source1_enabled() else 'no'}**\n"
            f"Token: **{'configurato' if tokens.get('user_token') else 'mancante'}**\n"
            f"Locale: `{await self.config.source1_locale()}` | timezone: `{await self.config.source1_timezone()}`\n"
            f"Intervallo: **{await self.config.source1_interval_seconds()}s**\n"
            f"Stato: **{self._self_status}**\n\n"
            "Comandi: `.quest selfbot token`, `.quest selfbot test`, `.quest selfbot on/off`, `.quest selfbot intervallo 30`, `.quest selfbot locale it-IT`, `.quest selfbot timezone Europe/Rome`, `.quest selfbot reset`."
        )

    @quest_selfbot.command(name="token")
    @commands.is_owner()
    async def quest_selfbot_token(self, ctx: commands.Context) -> None:
        try:
            from redbot.core.utils.views import SetApiView
        except ImportError:
            return await ctx.send("Il modal API non e disponibile su questa build di Red. Usa il comando core `set api` con servizio `questtracker` e chiave `user_token`.")
        view = SetApiView(default_service=API_SERVICE, default_keys={"user_token": ""})
        await ctx.send("🔐 Salva il token gia disponibile tramite il modal privato (`user_token <TOKEN>`). Non inviarlo in un canale pubblico.", view=view)

    @quest_selfbot.command(name="test", aliases=["check"])
    @commands.is_owner()
    async def quest_selfbot_test(self, ctx: commands.Context) -> None:
        result = await self._fetch_selfbot(force=True)
        if not result.get("usable"):
            return await ctx.send(f"❌ Source 1 non utilizzabile: **{self._self_status}**" + (f" (`{self._self_error}`)" if self._self_error else ""))
        await ctx.send(f"✅ Source 1 operativa: **{len(result.get('available_ids') or [])}** Quest visibili, **{len(result.get('excluded_ids') or [])}** escluse.")

    @quest_selfbot.command(name="on", aliases=["enable", "attiva"])
    @commands.is_owner()
    async def quest_selfbot_on(self, ctx: commands.Context) -> None:
        await self.config.source1_enabled.set(True)
        await ctx.send("✅ Source 1 attivata.")

    @quest_selfbot.command(name="off", aliases=["disable", "disattiva"])
    @commands.is_owner()
    async def quest_selfbot_off(self, ctx: commands.Context) -> None:
        await self.config.source1_enabled.set(False)
        await ctx.send("⏸️ Source 1 disattivata; restano Source 2 e Source 3.")

    @quest_selfbot.command(name="intervallo", aliases=["interval"])
    @commands.is_owner()
    async def quest_selfbot_interval(self, ctx: commands.Context, seconds: int) -> None:
        if seconds < 15 or seconds > 900:
            return await ctx.send("Usa un intervallo tra **15** e **900** secondi.")
        await self.config.source1_interval_seconds.set(seconds)
        await ctx.send(f"✅ Intervallo Source 1 impostato a **{seconds}s**.")

    @quest_selfbot.command(name="locale")
    @commands.is_owner()
    async def quest_selfbot_locale(self, ctx: commands.Context, locale: str) -> None:
        locale = locale.strip()
        if not locale or len(locale) > 20:
            return await ctx.send("Locale non valida.")
        await self.config.source1_locale.set(locale)
        await ctx.send(f"✅ Locale impostata a `{locale}`.")

    @quest_selfbot.command(name="timezone", aliases=["fuso"])
    @commands.is_owner()
    async def quest_selfbot_timezone(self, ctx: commands.Context, *, timezone_name: str) -> None:
        timezone_name = timezone_name.strip()
        if not timezone_name or len(timezone_name) > 64:
            return await ctx.send("Timezone non valida.")
        await self.config.source1_timezone.set(timezone_name)
        await ctx.send(f"✅ Timezone impostata a `{timezone_name}`.")

    @quest_selfbot.command(name="reset", aliases=["rimuovitoken"])
    @commands.is_owner()
    async def quest_selfbot_reset(self, ctx: commands.Context) -> None:
        await self.bot.remove_shared_api_tokens(API_SERVICE, "user_token")
        self._self_available.clear()
        self._self_excluded.clear()
        self._self_cache_at = None
        self._self_status = "token rimosso"
        self._self_error = None
        await ctx.send("✅ Token Source 1 rimosso dallo storage API di Red.")

    @quest.group(name="source2", invoke_without_command=True)
    @commands.is_owner()
    async def quest_source2(self, ctx: commands.Context) -> None:
        await ctx.send(f"Source 2 (DiscordQuest + regioni): **{'attiva' if await self.config.source2_enabled() else 'disattivata'}**. Usa `.quest source2 on` / `.quest source2 off`.")

    @quest_source2.command(name="on")
    @commands.is_owner()
    async def quest_source2_on(self, ctx: commands.Context) -> None:
        await self.config.source2_enabled.set(True)
        await ctx.send("✅ Source 2 attivata.")

    @quest_source2.command(name="off")
    @commands.is_owner()
    async def quest_source2_off(self, ctx: commands.Context) -> None:
        await self.config.source2_enabled.set(False)
        await ctx.send("⏸️ Source 2 disattivata.")

    @quest.group(name="source3", invoke_without_command=True)
    @commands.is_owner()
    async def quest_source3(self, ctx: commands.Context) -> None:
        await ctx.send(f"Source 3 (discord-api-diff): **{'attiva' if await self.config.source3_enabled() else 'disattivata'}**. Usa `.quest source3 on` / `.quest source3 off`.")

    @quest_source3.command(name="on")
    @commands.is_owner()
    async def quest_source3_on(self, ctx: commands.Context) -> None:
        await self.config.source3_enabled.set(True)
        await ctx.send("✅ Source 3 attivata.")

    @quest_source3.command(name="off")
    @commands.is_owner()
    async def quest_source3_off(self, ctx: commands.Context) -> None:
        await self.config.source3_enabled.set(False)
        await ctx.send("⏸️ Source 3 disattivata.")
