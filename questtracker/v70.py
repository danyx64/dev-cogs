import asyncio
import base64
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp
import discord
from redbot.core import commands

from .questtracker import FALLBACK_SOURCE, PRIMARY_SOURCE, _parse_iso, _quest_config
from .v42 import REGIONS_SOURCE
from .v62 import QuestTracker as QuestTrackerV62


API_SERVICE = "questtracker"
PROBE_URL = "https://discord.com/api/v10/quests/@me"
DEFAULT_MESSAGE_TEMPLATE = "{role} {link}"
AUTO_LINK_RE = re.compile(r"\]\(\s*(?:quest|link|url)\s*\)", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")
ALLOWED_PLACEHOLDERS = {"role", "link", "url", "quest_url"}

# Fallback client identity only for the read-only Quest probe. It can be
# overridden with `.quest probe superprops` without changing the cog.
DEFAULT_CLIENT_VERSION = "1.0.9256"
DEFAULT_CHROME_VERSION = "148.0.7778.280"
DEFAULT_ELECTRON_VERSION = "42.9.0"
DEFAULT_CLIENT_BUILD_NUMBER = 607562
PROBE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    f"discord/{DEFAULT_CLIENT_VERSION} Chrome/{DEFAULT_CHROME_VERSION} "
    f"Electron/{DEFAULT_ELECTRON_VERSION} Safari/537.36"
)


for _name in (
    "status",
    "messaggio",
    "message",
    "testo",
    "version",
    "versione",
    "placeholders",
    "placeholder",
    "vars",
    "variabili",
    "titolo",
    "title",
    "footer",
):
    QuestTrackerV62.quest.remove_command(_name)


def _default_super_properties(locale: str = "it-IT") -> str:
    payload = {
        "os": "Windows",
        "browser": "Discord Client",
        "release_channel": "stable",
        "client_version": DEFAULT_CLIENT_VERSION,
        "os_version": "10.0.19045",
        "os_arch": "x64",
        "app_arch": "x64",
        "system_locale": locale,
        "has_client_mods": False,
        "browser_user_agent": PROBE_USER_AGENT,
        "browser_version": DEFAULT_ELECTRON_VERSION,
        "os_sdk_version": "19045",
        "client_build_number": DEFAULT_CLIENT_BUILD_NUMBER,
        "native_build_number": 89799,
        "client_event_source": None,
        "client_app_state": "focused",
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


class QuestTracker(QuestTrackerV62):
    """QuestTracker 7: probe Italia primario + due fallback pubblici."""

    __version__ = "7.0.0"
    PROBE_STALE_MAX_SECONDS = 600
    ROLLING_GRACE_SECONDS = 600

    def __init__(self, bot):
        super().__init__(bot)
        self.config.register_global(
            v70_probe_enabled=True,
            v70_probe_locale="it-IT",
            v70_probe_timezone="Europe/Rome",
            v70_probe_interval_seconds=30,
        )
        self.config.register_guild(v70_migrated=False)

        self._probe_cache_quests: Dict[str, Dict[str, Any]] = {}
        self._probe_cache_excluded: Set[str] = set()
        self._probe_cache_at: Optional[datetime] = None
        self._probe_last_ok: Optional[datetime] = None
        self._probe_last_attempt: Optional[datetime] = None
        self._probe_backoff_until: Optional[datetime] = None
        self._probe_status = "non configurato"
        self._probe_error: Optional[str] = None
        self._probe_live_count = 0
        self._probe_excluded_count = 0

    # ------------------------------------------------------------------
    # Primary source: read-only Italian user probe
    # ------------------------------------------------------------------

    def _clear_probe_cache(self) -> None:
        self._probe_cache_quests.clear()
        self._probe_cache_excluded.clear()
        self._probe_cache_at = None
        self._probe_backoff_until = None

    @commands.Cog.listener()
    async def on_red_api_tokens_update(self, service_name: str, api_tokens: Dict[str, str]):
        if str(service_name).lower() == API_SERVICE:
            self._clear_probe_cache()
            self._probe_status = "credenziali aggiornate"
            self._probe_error = None

    @staticmethod
    def _probe_entry_id(entry: Any) -> str:
        if not isinstance(entry, dict):
            return ""
        config = entry.get("config") if isinstance(entry.get("config"), dict) else {}
        return str(
            entry.get("id")
            or entry.get("quest_id")
            or config.get("id")
            or config.get("quest_id")
            or ""
        ).strip()

    @classmethod
    def _probe_excluded_ids(cls, items: Any) -> Set[str]:
        if not isinstance(items, list):
            return set()
        result: Set[str] = set()
        for item in items:
            qid = cls._probe_entry_id(item)
            if qid:
                result.add(qid)
        return result

    def _probe_cached_snapshot(self, status: str) -> Dict[str, Any]:
        return {
            "usable": bool(self._probe_cache_at),
            "live": False,
            "status": status,
            "quests": list(self._probe_cache_quests.values()),
            "available_ids": set(self._probe_cache_quests),
            "excluded_ids": set(self._probe_cache_excluded),
        }

    async def _fetch_probe(self, *, force: bool = False) -> Dict[str, Any]:
        enabled = bool(await self.config.v70_probe_enabled())
        if not enabled:
            self._probe_status = "disattivato"
            self._probe_error = None
            return {
                "usable": False,
                "live": False,
                "status": "disabled",
                "quests": [],
                "available_ids": set(),
                "excluded_ids": set(),
            }

        tokens = await self.bot.get_shared_api_tokens(API_SERVICE)
        token = str(tokens.get("user_token") or "").strip()
        if not token:
            self._probe_status = "token mancante"
            self._probe_error = None
            return {
                "usable": False,
                "live": False,
                "status": "missing-token",
                "quests": [],
                "available_ids": set(),
                "excluded_ids": set(),
            }

        now = datetime.now(timezone.utc)
        interval = int(await self.config.v70_probe_interval_seconds())
        interval = max(15, min(interval, 900))

        if not force and self._probe_cache_at:
            age = (now - self._probe_cache_at).total_seconds()
            if age < interval:
                self._probe_status = f"cache valida ({int(age)}s)"
                return self._probe_cached_snapshot("cache")

        if not force and self._probe_backoff_until and self._probe_backoff_until > now:
            remaining = int((self._probe_backoff_until - now).total_seconds())
            if self._probe_cache_at and (now - self._probe_cache_at).total_seconds() <= self.PROBE_STALE_MAX_SECONDS:
                self._probe_status = f"rate limit, cache ({remaining}s)"
                return self._probe_cached_snapshot("rate-limit-cache")
            self._probe_status = f"rate limit ({remaining}s)"
            return {
                "usable": False,
                "live": False,
                "status": "rate-limit",
                "quests": [],
                "available_ids": set(),
                "excluded_ids": set(),
            }

        if not self.session or self.session.closed:
            self._probe_status = "sessione HTTP non disponibile"
            return {
                "usable": False,
                "live": False,
                "status": "no-session",
                "quests": [],
                "available_ids": set(),
                "excluded_ids": set(),
            }

        locale = str(await self.config.v70_probe_locale() or "it-IT").strip() or "it-IT"
        timezone_name = str(
            await self.config.v70_probe_timezone() or "Europe/Rome"
        ).strip() or "Europe/Rome"
        super_properties = str(tokens.get("super_properties") or "").strip()
        if not super_properties:
            super_properties = _default_super_properties(locale)

        headers = {
            "Authorization": token,
            "Accept": "application/json",
            "Accept-Language": f"{locale},it;q=0.9,en-US;q=0.8,en;q=0.7",
            "User-Agent": PROBE_USER_AGENT,
            "X-Discord-Locale": locale,
            "X-Discord-Timezone": timezone_name,
            "X-Super-Properties": super_properties,
            "Referer": "https://discord.com/quest-home",
            "Origin": "https://discord.com",
        }

        self._probe_last_attempt = now
        try:
            async with self.session.get(PROBE_URL, headers=headers) as response:
                if response.status == 429:
                    retry_after = 60.0
                    try:
                        body = await response.json(content_type=None)
                        retry_after = float(body.get("retry_after", retry_after))
                    except (ValueError, TypeError, aiohttp.ContentTypeError):
                        raw = response.headers.get("Retry-After")
                        try:
                            retry_after = float(raw) if raw else retry_after
                        except (TypeError, ValueError):
                            pass
                    retry_after = max(15.0, min(retry_after, 3600.0))
                    self._probe_backoff_until = now + timedelta(seconds=retry_after)
                    self._probe_error = f"HTTP 429, retry {int(retry_after)}s"
                    if self._probe_cache_at and (
                        now - self._probe_cache_at
                    ).total_seconds() <= self.PROBE_STALE_MAX_SECONDS:
                        self._probe_status = "rate limit, uso cache recente"
                        return self._probe_cached_snapshot("rate-limit-cache")
                    self._probe_status = "rate limit"
                    return {
                        "usable": False,
                        "live": False,
                        "status": "rate-limit",
                        "quests": [],
                        "available_ids": set(),
                        "excluded_ids": set(),
                    }

                if response.status in (401, 403):
                    self._clear_probe_cache()
                    self._probe_status = f"autenticazione fallita ({response.status})"
                    self._probe_error = f"HTTP {response.status}"
                    return {
                        "usable": False,
                        "live": False,
                        "status": "auth-failed",
                        "quests": [],
                        "available_ids": set(),
                        "excluded_ids": set(),
                    }

                if response.status != 200:
                    self._probe_status = f"errore HTTP {response.status}"
                    self._probe_error = f"HTTP {response.status}"
                    if self._probe_cache_at and (
                        now - self._probe_cache_at
                    ).total_seconds() <= self.PROBE_STALE_MAX_SECONDS:
                        return self._probe_cached_snapshot("http-cache")
                    return {
                        "usable": False,
                        "live": False,
                        "status": "http-error",
                        "quests": [],
                        "available_ids": set(),
                        "excluded_ids": set(),
                    }

                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
            self._probe_error = type(exc).__name__
            self._probe_status = f"errore rete: {type(exc).__name__}"
            if self._probe_cache_at and (
                now - self._probe_cache_at
            ).total_seconds() <= self.PROBE_STALE_MAX_SECONDS:
                return self._probe_cached_snapshot("network-cache")
            return {
                "usable": False,
                "live": False,
                "status": "network-error",
                "quests": [],
                "available_ids": set(),
                "excluded_ids": set(),
            }

        if not isinstance(payload, dict) or not isinstance(payload.get("quests"), list):
            self._probe_status = "schema risposta non riconosciuto"
            self._probe_error = "schema"
            return {
                "usable": False,
                "live": False,
                "status": "bad-schema",
                "quests": [],
                "available_ids": set(),
                "excluded_ids": set(),
            }

        quest_map: Dict[str, Dict[str, Any]] = {}
        for item in payload.get("quests") or []:
            if not isinstance(item, dict):
                continue
            qid = self._probe_entry_id(item)
            if qid:
                quest_map[qid] = item

        excluded_ids = self._probe_excluded_ids(payload.get("excluded_quests"))
        self._probe_cache_quests = quest_map
        self._probe_cache_excluded = excluded_ids
        self._probe_cache_at = now
        self._probe_last_ok = now
        self._probe_backoff_until = None
        self._probe_live_count = len(quest_map)
        self._probe_excluded_count = len(excluded_ids)
        self._probe_status = "online"
        self._probe_error = None

        return {
            "usable": True,
            "live": True,
            "status": "online",
            "quests": list(quest_map.values()),
            "available_ids": set(quest_map),
            "excluded_ids": set(excluded_ids),
        }

    # ------------------------------------------------------------------
    # Source merge and Italy verdict
    # ------------------------------------------------------------------

    @classmethod
    def _italy_verdict(cls, region: Optional[Dict[str, Any]]) -> Tuple[str, int, str]:
        if isinstance(region, dict) and region.get("_probe_authoritative"):
            status = str(region.get("_probe_status") or "unknown")
            freshness = str(region.get("_probe_freshness") or "live")
            if status == "available":
                return "allowed", 2000, f"probe Italia {freshness}: disponibile per l'account"
            if status == "blocked":
                return "blocked", 0, f"probe Italia {freshness}: presente in excluded_quests"
            return "unknown", 0, f"probe Italia {freshness}: ID non restituito"
        return QuestTrackerV62._italy_verdict(region)

    @staticmethod
    def _fallback_region_record(
        qid: str,
        region_map: Dict[str, Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        record = region_map.get(qid)
        if not isinstance(record, dict):
            return None
        return {"evidence": {"discordquest-regions": dict(record)}}

    def _resolved_region(
        self,
        qid: str,
        probe: Dict[str, Any],
        region_map: Dict[str, Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if probe.get("usable"):
            freshness = "live" if probe.get("live") else "cache"
            available_ids = probe.get("available_ids") or set()
            excluded_ids = probe.get("excluded_ids") or set()
            if qid in available_ids:
                return {
                    "_probe_authoritative": True,
                    "_probe_status": "available",
                    "_probe_freshness": freshness,
                    "_verified_italy": True,
                }
            if qid in excluded_ids:
                return {
                    "_probe_authoritative": True,
                    "_probe_status": "blocked",
                    "_probe_freshness": freshness,
                }
            return {
                "_probe_authoritative": True,
                "_probe_status": "unknown",
                "_probe_freshness": freshness,
            }

        return self._fallback_region_record(qid, region_map)

    async def _fetch_quests_live(self) -> List[Dict[str, Any]]:
        probe_task = self._fetch_probe()
        dq_task = self._fetch_json_source("discordquest", PRIMARY_SOURCE)
        diff_task = self._fetch_json_source("api-diff", FALLBACK_SOURCE)
        regions_task = self._fetch_json_source("discordquest-regions", REGIONS_SOURCE)

        probe, dq_result, diff_result, regions_result = await asyncio.gather(
            probe_task,
            dq_task,
            diff_task,
            regions_task,
        )

        dq_payload, dq_live = dq_result
        diff_payload, diff_live = diff_result
        regions_payload, regions_live = regions_result

        dq_entries = self._extract_quest_list(dq_payload)
        diff_entries = self._extract_quest_list(diff_payload)
        probe_entries = [
            item for item in probe.get("quests", []) if isinstance(item, dict)
        ]

        self._source_counts["italy-probe"] = len(probe_entries)
        self._source_counts["discordquest"] = len(dq_entries)
        self._source_counts["api-diff"] = len(diff_entries)

        region_map = self._parse_region_payload(
            regions_payload,
            "discordquest-regions",
        )
        if region_map:
            self._regions_cache = dict(region_map)
            if regions_live:
                self._regions_fetched_at = datetime.now(timezone.utc)

        merged: Dict[str, Dict[str, Any]] = {}
        # The probe is discovery source #1; public sources enrich the same IDs
        # and discover IDs that the account did not receive.
        for entry in probe_entries:
            self._merge_source_entry(merged, entry, "italy-probe")
        for entry in dq_entries:
            self._merge_source_entry(merged, entry, "discordquest")
        for entry in diff_entries:
            self._merge_source_entry(merged, entry, "api-diff")

        now = datetime.now(timezone.utc)
        if probe.get("live") or dq_live or diff_live:
            self._last_live_fetch_at = now

        # Refresh rolling cache with a fresh regional decision every cycle.
        for qid, entry in list(merged.items()):
            item = dict(entry)
            item["_italy_region"] = self._resolved_region(qid, probe, region_map)
            self._rolling_entries[qid] = item
            self._rolling_seen_at[qid] = now
            merged[qid] = item

        for qid in list(self._rolling_entries):
            entry = self._rolling_entries[qid]
            seen_at = self._rolling_seen_at.get(qid, now)
            config = _quest_config(entry) or {}
            expires = _parse_iso(config.get("expires_at"))
            age = (now - seen_at).total_seconds()

            if expires and expires <= now:
                self._rolling_entries.pop(qid, None)
                self._rolling_seen_at.pop(qid, None)
                continue
            if age > self.ROLLING_GRACE_SECONDS:
                self._rolling_entries.pop(qid, None)
                self._rolling_seen_at.pop(qid, None)
                continue

            if qid not in merged:
                cached = dict(entry)
                cached["_quest_rolling_cache"] = True
                cached["_italy_region"] = self._resolved_region(qid, probe, region_map)
                merged[qid] = cached

        return list(merged.values())

    async def _scan_guild(self, guild: discord.Guild, *, force: bool = False) -> int:
        settings = await self.config.guild(guild).all()
        if not settings.get("enabled") and not force:
            return 0

        # Upgrade baseline: keep every historical channel/role/message setting,
        # but never replay the currently active catalogue when v7 is first loaded.
        if not settings.get("v70_migrated"):
            entries = await self._fetch_quests()
            current: Dict[str, Tuple[Dict[str, Any], Dict[str, Any], str]] = {}
            for entry, config in self._raw_active_entries(entries):
                qid = self._quest_id(entry)
                if not qid:
                    continue
                current[qid] = (entry, config, self._family_key(entry, config))

            ids = list(current.keys())[-self.STATE_LIMIT:]
            families: List[str] = []
            for _qid, (_entry, _config, family) in current.items():
                if family not in families:
                    families.append(family)

            conf = self.config.guild(guild)
            await conf.v62_known_ids.set(ids)
            await conf.v62_known_families.set(families[-self.STATE_LIMIT:])
            await conf.v62_waiting_ids.set([])
            await conf.v62_pending_ids.set([])
            await conf.v62_migrated.set(True)
            await conf.v70_migrated.set(True)
            return 0

        return await super()._scan_guild(guild, force=force)

    # ------------------------------------------------------------------
    # Editable classic message, with automatic Markdown quest links
    # ------------------------------------------------------------------

    @staticmethod
    def _unknown_placeholders(template: str) -> List[str]:
        return sorted(
            {
                name
                for name in PLACEHOLDER_RE.findall(str(template))
                if name not in ALLOWED_PLACEHOLDERS
            }
        )

    @staticmethod
    def _render_custom_message(
        template: str,
        role_text: str,
        quest_url: str,
    ) -> str:
        legacy_link = f"[.]({quest_url})"
        text = str(template or DEFAULT_MESSAGE_TEMPLATE)
        text = text.replace("{role}", role_text)
        text = text.replace("{url}", quest_url)
        text = text.replace("{quest_url}", quest_url)
        text = text.replace("{link}", legacy_link)
        text = AUTO_LINK_RE.sub(lambda _match: f"]({quest_url})", text)
        text = text.strip()

        # If the admin wrote only plain text, preserve the native Quest card by
        # appending the old masked link automatically.
        if quest_url not in text:
            text = f"{text} {legacy_link}".strip()
        return text

    async def _send_quest(
        self,
        channel: discord.TextChannel,
        guild: discord.Guild,
        entry: Dict[str, Any],
        config: Dict[str, Any],
        *,
        test: bool = False,
    ) -> None:
        url = self._quest_share_url(entry, config)
        if url is None:
            return

        settings = await self.config.guild(guild).all()
        template = str(settings.get("message_template") or DEFAULT_MESSAGE_TEMPLATE)
        role = guild.get_role(settings.get("role_id") or 0)
        show_role = role is not None and settings.get("ping_role", True)
        role_text = role.mention if show_role else ""
        content = self._render_custom_message(template, role_text, url)

        kwargs = {
            "content": content[:2000],
            "allowed_mentions": discord.AllowedMentions(
                roles=bool(show_role and not test),
                users=False,
                everyone=False,
            ),
        }

        if test:
            try:
                await channel.send(**kwargs, silent=True)
                return
            except TypeError:
                pass

        await channel.send(**kwargs)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @QuestTrackerV62.quest.command(name="messaggio", aliases=["message", "testo"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_message(self, ctx: commands.Context, *, testo: Optional[str] = None):
        """Mostra o modifica il messaggio inviato insieme alla card Quest."""
        conf = self.config.guild(ctx.guild)

        if testo is None:
            template = await conf.message_template()
            return await ctx.send(
                "**Messaggio Quest attuale:**\n"
                f"```\n{template or DEFAULT_MESSAGE_TEMPLATE}\n```\n"
                "Puoi scrivere il link come vuoi:\n"
                "`[Apri la Quest](quest)` -> il bot sostituisce `quest` con il link reale.\n"
                "`[CLICCA QUI](link)` e `[🎯](url)` funzionano allo stesso modo.\n\n"
                "Compatibilita vecchia: `{role}` = ruolo, `{link}` = `[.](link reale)`, "
                "`{url}` / `{quest_url}` = URL puro.\n"
                "Se non inserisci nessun link, il bot aggiunge automaticamente il vecchio `[.]`.\n"
                "Reset: `.quest messaggio reset`"
            )

        template = str(testo).strip().replace("\\n", "\n")
        if template.lower() in {"reset", "default", "predefinito"}:
            await conf.message_template.set(DEFAULT_MESSAGE_TEMPLATE)
            return await ctx.send(
                "✅ Messaggio classico ripristinato: `{role} {link}`."
            )

        if not template:
            return await ctx.send("❌ Il messaggio non puo essere vuoto.")
        if len(template) > 1800:
            return await ctx.send("❌ Il messaggio puo avere massimo 1800 caratteri.")

        unknown = self._unknown_placeholders(template)
        if unknown:
            return await ctx.send(
                "❌ Placeholder non validi: "
                + ", ".join(f"`{{{name}}}`" for name in unknown)
                + ". Usa `{role}`, `{link}`, `{url}`, `{quest_url}` oppure "
                "un link Markdown come `[testo](quest)`."
            )

        await conf.message_template.set(template)
        await ctx.send(
            "✅ Messaggio Quest aggiornato. Usa `.quest test` per provarlo senza ping."
        )

    @QuestTrackerV62.quest.command(
        name="placeholders",
        aliases=["placeholder", "vars", "variabili"],
    )
    async def quest_placeholders(self, ctx: commands.Context):
        await ctx.send(
            "**Messaggio Quest - formati supportati**\n"
            "• `{role}` -> ruolo configurato\n"
            "• `{link}` -> vecchio link mascherato `[.]`\n"
            "• `{url}` / `{quest_url}` -> URL Quest puro\n"
            "• `[qualsiasi scritta](quest)` -> link automatico con il testo che scegli tu\n"
            "• `[qualsiasi scritta](link)` / `(url)` -> stesso comportamento\n\n"
            "Esempio: `.quest messaggio 🔔 {role} [APRI LA QUEST](quest)`"
        )

    @QuestTrackerV62.quest.command(name="titolo", aliases=["title"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_title(self, ctx: commands.Context, *, testo: Optional[str] = None):
        await ctx.send(
            "ℹ️ La card Quest e quella nativa di Discord e il suo titolo non viene modificato dal cog. "
            "Puoi personalizzare completamente il testo attorno al link con `.quest messaggio`."
        )

    @QuestTrackerV62.quest.command(name="footer")
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_footer(self, ctx: commands.Context, *, testo: Optional[str] = None):
        await ctx.send(
            "ℹ️ La card e nativa di Discord: non c'e un footer custom. "
            "Usa `.quest messaggio` per aggiungere qualsiasi testo prima/dopo il link."
        )

    @QuestTrackerV62.quest.command(name="version", aliases=["versione"])
    async def quest_version(self, ctx: commands.Context):
        await ctx.send(
            f"QuestTracker **v{self.__version__}** — probe Italia + DiscordQuest + api-diff."
        )

    @QuestTrackerV62.quest.command(name="fonti", aliases=["sources", "sorgenti"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_sources(self, ctx: commands.Context):
        dq_error = self._source_errors.get("discordquest")
        diff_error = self._source_errors.get("api-diff")
        reg_error = self._source_errors.get("discordquest-regions")

        probe_last = (
            f"<t:{int(self._probe_last_ok.timestamp())}:R>"
            if self._probe_last_ok
            else "mai"
        )
        embed = discord.Embed(
            title="QuestTracker - Fonti",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(
            name="1. Probe Italia",
            value=(
                f"Stato: **{self._probe_status}**\n"
                f"Quest: **{self._probe_live_count}** | escluse: **{self._probe_excluded_count}**\n"
                f"Ultimo OK: {probe_last}"
            ),
            inline=False,
        )
        embed.add_field(
            name="2. DiscordQuest",
            value=(
                f"Quest: **{self._source_counts.get('discordquest', 0)}**\n"
                f"Regioni: **{len(self._regions_cache)}**\n"
                f"Errore: `{dq_error or reg_error or 'nessuno'}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="3. discord-api-diff",
            value=(
                f"Quest: **{self._source_counts.get('api-diff', 0)}**\n"
                f"Errore: `{diff_error or 'nessuno'}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Priorita",
            value=(
                "Probe disponibile -> decide l'Italia. "
                "Probe non disponibile -> usa i dati regionali DiscordQuest. "
                "api-diff resta discovery/fallback del catalogo."
            ),
            inline=False,
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @QuestTrackerV62.quest.group(name="probe", invoke_without_command=True)
    @commands.is_owner()
    async def quest_probe(self, ctx: commands.Context):
        """Configura il probe Italia globale del bot."""
        enabled = bool(await self.config.v70_probe_enabled())
        interval = int(await self.config.v70_probe_interval_seconds())
        locale = str(await self.config.v70_probe_locale())
        timezone_name = str(await self.config.v70_probe_timezone())
        tokens = await self.bot.get_shared_api_tokens(API_SERVICE)
        token_set = bool(tokens.get("user_token"))
        superprops_set = bool(tokens.get("super_properties"))

        await ctx.send(
            "**Probe Italia**\n"
            f"Stato: **{'attivo' if enabled else 'disattivato'}**\n"
            f"Token: **{'configurato' if token_set else 'mancante'}**\n"
            f"X-Super-Properties custom: **{'si' if superprops_set else 'no (fallback interno)'}**\n"
            f"Locale: `{locale}` | timezone: `{timezone_name}`\n"
            f"Intervallo: **{interval}s**\n"
            f"Ultimo risultato: **{self._probe_status}**\n\n"
            "Comandi: `.quest probe token`, `.quest probe test`, `.quest probe on/off`, "
            "`.quest probe intervallo <secondi>`, `.quest probe locale <locale>`, "
            "`.quest probe timezone <zona>`, `.quest probe superprops`, `.quest probe reset`."
        )

    @quest_probe.command(name="token")
    @commands.is_owner()
    async def quest_probe_token(self, ctx: commands.Context):
        """Apre il modal sicuro di Red per salvare il token utente."""
        try:
            from redbot.core.utils.views import SetApiView
        except ImportError:
            return await ctx.send(
                "Il tuo Red non supporta il modal API. Usa il comando core `set api` "
                f"con servizio `{API_SERVICE}` e chiave `user_token`."
            )

        view = SetApiView(
            default_service=API_SERVICE,
            default_keys={"user_token": ""},
        )
        await ctx.send(
            "🔐 Premi il pulsante e inserisci `user_token <TOKEN>` nel modal privato. "
            "Il token viene salvato nello storage API condiviso di Red, non nella config del server.",
            view=view,
        )

    @quest_probe.command(name="superprops", aliases=["superproperties"])
    @commands.is_owner()
    async def quest_probe_superprops(self, ctx: commands.Context):
        """Imposta opzionalmente X-Super-Properties senza esporlo nel canale."""
        try:
            from redbot.core.utils.views import SetApiView
        except ImportError:
            return await ctx.send(
                "Usa il comando core `set api` con servizio "
                f"`{API_SERVICE}` e chiave `super_properties`."
            )

        view = SetApiView(
            default_service=API_SERVICE,
            default_keys={"super_properties": ""},
        )
        await ctx.send(
            "🔐 Opzionale: inserisci `super_properties <BASE64>` nel modal. "
            "Se non lo imposti viene usato il fallback interno.",
            view=view,
        )

    @quest_probe.command(name="test", aliases=["check"])
    @commands.is_owner()
    async def quest_probe_test(self, ctx: commands.Context):
        async with ctx.typing():
            result = await self._fetch_probe(force=True)
        if not result.get("usable"):
            return await ctx.send(
                f"❌ Probe non utilizzabile: **{self._probe_status}**"
                + (f" (`{self._probe_error}`)" if self._probe_error else "")
            )
        await ctx.send(
            "✅ Probe Italia operativo. "
            f"Quest disponibili: **{len(result.get('available_ids') or [])}**; "
            f"excluded: **{len(result.get('excluded_ids') or [])}**; "
            f"stato: **{result.get('status')}**."
        )

    @quest_probe.command(name="on", aliases=["enable", "attiva"])
    @commands.is_owner()
    async def quest_probe_on(self, ctx: commands.Context):
        await self.config.v70_probe_enabled.set(True)
        self._clear_probe_cache()
        await ctx.send("✅ Probe Italia attivato.")

    @quest_probe.command(name="off", aliases=["disable", "disattiva"])
    @commands.is_owner()
    async def quest_probe_off(self, ctx: commands.Context):
        await self.config.v70_probe_enabled.set(False)
        self._clear_probe_cache()
        await ctx.send(
            "⏸️ Probe Italia disattivato. Il tracker usera i fallback pubblici."
        )

    @quest_probe.command(name="intervallo", aliases=["interval"])
    @commands.is_owner()
    async def quest_probe_interval(self, ctx: commands.Context, seconds: int):
        if seconds < 15 or seconds > 900:
            return await ctx.send("Usa un intervallo tra **15** e **900** secondi.")
        await self.config.v70_probe_interval_seconds.set(seconds)
        await ctx.send(f"✅ Intervallo probe impostato a **{seconds}s**.")

    @quest_probe.command(name="locale")
    @commands.is_owner()
    async def quest_probe_locale(self, ctx: commands.Context, locale: str):
        locale = locale.strip()
        if not locale or len(locale) > 20:
            return await ctx.send("Locale non valido.")
        await self.config.v70_probe_locale.set(locale)
        self._clear_probe_cache()
        await ctx.send(
            f"✅ Locale probe impostato a `{locale}`. "
            "Nota: la locale non sostituisce la posizione/account usati da Discord per le regioni."
        )

    @quest_probe.command(name="timezone", aliases=["fuso"])
    @commands.is_owner()
    async def quest_probe_timezone(self, ctx: commands.Context, *, timezone_name: str):
        timezone_name = timezone_name.strip()
        if not timezone_name or len(timezone_name) > 64:
            return await ctx.send("Timezone non valida.")
        await self.config.v70_probe_timezone.set(timezone_name)
        self._clear_probe_cache()
        await ctx.send(f"✅ Timezone probe impostata a `{timezone_name}`.")

    @quest_probe.command(name="reset", aliases=["rimuovitoken"])
    @commands.is_owner()
    async def quest_probe_reset(self, ctx: commands.Context):
        await self.bot.remove_shared_api_tokens(
            API_SERVICE,
            "user_token",
            "super_properties",
        )
        self._clear_probe_cache()
        self._probe_status = "token rimosso"
        self._probe_error = None
        await ctx.send("✅ Token e X-Super-Properties del probe rimossi.")

    @QuestTrackerV62.quest.command(name="status")
    async def quest_status(self, ctx: commands.Context):
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(settings.get("channel_id") or 0)
        role = ctx.guild.get_role(settings.get("role_id") or 0)
        template = str(settings.get("message_template") or DEFAULT_MESSAGE_TEMPLATE)

        embed = discord.Embed(title="QuestTracker", colour=discord.Colour.blurple())
        embed.add_field(name="Versione", value=self.__version__, inline=True)
        embed.add_field(
            name="Stato",
            value="✅ Attivo" if settings.get("enabled") else "⏸️ Disattivato",
            inline=True,
        )
        embed.add_field(name="Polling", value="15 secondi", inline=True)
        embed.add_field(
            name="Canale",
            value=channel.mention if channel else "Non configurato",
            inline=True,
        )
        embed.add_field(
            name="Ruolo",
            value=role.mention if role else "Nessuno",
            inline=True,
        )
        embed.add_field(
            name="Ping ruolo",
            value="✅ Attivo" if settings.get("ping_role", True) else "⛔ Disattivato",
            inline=True,
        )
        embed.add_field(
            name="Sorgente primaria",
            value=f"Probe account Italia: **{self._probe_status}**",
            inline=False,
        )
        embed.add_field(
            name="Fallback",
            value="2) DiscordQuest + regioni\n3) discord-api-diff",
            inline=False,
        )
        embed.add_field(
            name="Messaggio",
            value=f"```\n{template}\n```"[:1024],
            inline=False,
        )
        embed.add_field(
            name="Code",
            value=(
                f"Attesa regione: **{len(settings.get('v62_waiting_ids') or [])}** | "
                f"Pronte: **{len(settings.get('v62_pending_ids') or [])}**"
            ),
            inline=False,
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
