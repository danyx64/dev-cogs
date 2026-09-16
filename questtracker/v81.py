from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Set

import discord
from redbot.core import commands

from .v80 import (
    QuestTracker as QuestTrackerV80,
    _family_key,
    _parse_iso,
    _quest_config,
    _quest_id,
    _quest_name,
)


# v8.1 deliberately does NOT redefine existing v8 commands such as
# `.quest attive` / `.quest active` or `.quest reinvia`.  Those inherited
# commands automatically use the overridden `_fetch_all()` below, so they get
# the new cross-source dedupe without registering duplicate command aliases.
# Only the two genuinely new subcommands are attached here.  Removing them
# first makes module reloads idempotent.
for _command_name in ("tutte", "italiane"):
    QuestTrackerV80.quest.remove_command(_command_name)


class QuestTracker(QuestTrackerV80):
    """QuestTracker v8.1.1: catalogo unificato e dedupe tra tutte le sorgenti."""

    __version__ = "8.1.1"

    SOURCE_LABELS = {
        "source1-selfbot": "S1",
        "source2-discordquest": "S2",
        "source3-api-diff": "S3",
    }

    def __init__(self, bot):
        super().__init__(bot)
        self._last_duplicate_groups = 0
        self._last_duplicate_aliases = 0

    @staticmethod
    def _availability_rank(entry: Dict[str, Any]) -> int:
        return {
            "allowed": 3,
            "blocked": 2,
            "unknown": 1,
        }.get(str(entry.get("_availability") or "unknown"), 0)

    @classmethod
    def _entry_preference_score(cls, entry: Dict[str, Any]) -> int:
        """Sceglie il record piu completo/autorevole di un gruppo duplicato."""
        score = cls._availability_rank(entry) * 10000
        sources = set(entry.get("_sources") or [])
        if "source1-selfbot" in sources:
            score += 1000
        if "source2-discordquest" in sources:
            score += 500
        if "source3-api-diff" in sources:
            score += 100

        config = _quest_config(entry)
        for key in (
            "application",
            "messages",
            "starts_at",
            "expires_at",
            "rewards_config",
            "rewards",
        ):
            if config.get(key):
                score += 10
        score += min(len(str(entry)), 5000) // 250
        return score

    @staticmethod
    def _dedupe_identity(entry: Dict[str, Any]) -> str:
        """Identita cross-source sicura per la stessa Quest/campagna.

        La v8 base ha gia unito gli ID identici. Qui uniamo anche ID diversi
        soltanto quando i metadati della campagna sono abbastanza forti.
        """
        qid = _quest_id(entry)
        config = _quest_config(entry)
        messages = (
            config.get("messages")
            if isinstance(config.get("messages"), dict)
            else {}
        )
        app = (
            config.get("application")
            if isinstance(config.get("application"), dict)
            else {}
        )
        app_id = str(app.get("id") or config.get("application_id") or "").strip()
        name = re.sub(
            r"\s+",
            " ",
            str(messages.get("quest_name") or "").strip().casefold(),
        )
        starts = str(config.get("starts_at") or "").strip()
        expires = str(config.get("expires_at") or "").strip()

        strong_signals = sum(bool(value) for value in (app_id, name, starts, expires))
        if name and strong_signals >= 3:
            return "family:" + _family_key(entry)
        return "id:" + qid

    @staticmethod
    def _alias_ids(entry: Dict[str, Any]) -> List[str]:
        aliases: List[str] = []
        for value in entry.get("_alias_ids") or []:
            value = str(value).strip()
            if value and value not in aliases:
                aliases.append(value)
        qid = _quest_id(entry)
        if qid and qid not in aliases:
            aliases.append(qid)
        return aliases

    @classmethod
    def _combine_duplicates(
        cls,
        left: Dict[str, Any],
        right: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Unisce duplicati conservando ID alias e prove delle sorgenti."""
        if cls._entry_preference_score(right) > cls._entry_preference_score(left):
            preferred, fallback = right, left
        else:
            preferred, fallback = left, right

        result: Dict[str, Any] = dict(fallback)
        for key, value in preferred.items():
            if key == "config" and isinstance(value, dict):
                old_config = (
                    result.get("config")
                    if isinstance(result.get("config"), dict)
                    else {}
                )
                merged_config = dict(old_config)
                merged_config.update(value)
                result["config"] = merged_config
            else:
                result[key] = value

        sources: List[str] = []
        for entry in (left, right):
            for source in entry.get("_sources") or []:
                source = str(source)
                if source not in sources:
                    sources.append(source)
        result["_sources"] = sources

        aliases: List[str] = []
        for entry in (left, right):
            for alias in cls._alias_ids(entry):
                if alias not in aliases:
                    aliases.append(alias)
        result["_alias_ids"] = aliases
        result["_dedupe_family"] = _family_key(result)
        return result

    async def _fetch_all(self, *, force_selfbot: bool = False) -> List[Dict[str, Any]]:
        # v8 prima fonde gli ID esatti tra S1/S2/S3 e calcola il verdetto Italia.
        # v8.1 poi comprime eventuali alias con ID diversi della stessa campagna.
        exact_id_catalog = await super()._fetch_all(force_selfbot=force_selfbot)

        deduped: Dict[str, Dict[str, Any]] = {}
        duplicate_groups: Set[str] = set()
        duplicate_aliases = 0

        for entry in exact_id_catalog:
            identity = self._dedupe_identity(entry)
            existing = deduped.get(identity)
            if existing is None:
                copy = dict(entry)
                copy["_alias_ids"] = self._alias_ids(copy)
                copy["_dedupe_family"] = _family_key(copy)
                deduped[identity] = copy
                continue

            duplicate_groups.add(identity)
            before = set(self._alias_ids(existing))
            combined = self._combine_duplicates(existing, entry)
            after = set(self._alias_ids(combined))
            duplicate_aliases += max(0, len(after - before))
            deduped[identity] = combined

        self._last_duplicate_groups = len(duplicate_groups)
        self._last_duplicate_aliases = duplicate_aliases
        self._last_merged_count = len(deduped)
        return list(deduped.values())

    @staticmethod
    def _active_sort_key(entry: Dict[str, Any]) -> datetime:
        starts = _parse_iso(_quest_config(entry).get("starts_at"))
        return starts or datetime.min.replace(tzinfo=timezone.utc)

    async def _all_active_catalog(self) -> List[Dict[str, Any]]:
        entries = await self._fetch_all()
        active = [entry for entry in entries if self._entry_is_active(entry)]
        active.sort(key=self._active_sort_key, reverse=True)
        return active

    @classmethod
    def _source_summary(cls, entry: Dict[str, Any]) -> str:
        tags: List[str] = []
        for source in entry.get("_sources") or []:
            tag = cls.SOURCE_LABELS.get(str(source), str(source))
            if tag not in tags:
                tags.append(tag)
        return "+".join(tags) if tags else "?"

    @staticmethod
    def _italy_mark(entry: Dict[str, Any]) -> str:
        verdict = str(entry.get("_availability") or "unknown")
        if verdict == "allowed":
            return "✅"
        if verdict == "blocked":
            return "❌"
        return "❔"

    @classmethod
    def _catalog_line(cls, entry: Dict[str, Any], *, show_verdict: bool) -> str:
        qid = _quest_id(entry)
        name = _quest_name(entry).replace("\n", " ")[:90]
        aliases = cls._alias_ids(entry)
        alias_note = f" · {len(aliases)} ID uniti" if len(aliases) > 1 else ""
        source_note = cls._source_summary(entry)
        prefix = cls._italy_mark(entry) + " " if show_verdict else "✅ "
        return f"{prefix}**{name}** — `{qid}` · `{source_note}`{alias_note}"

    async def _send_catalog_pages(
        self,
        ctx: commands.Context,
        entries: List[Dict[str, Any]],
        *,
        title: str,
        show_verdict: bool,
    ) -> None:
        if not entries:
            await ctx.send("Nessuna Quest attiva trovata per questo elenco.")
            return

        page_size = 10
        pages = (len(entries) + page_size - 1) // page_size
        for page_index in range(pages):
            chunk = entries[page_index * page_size : (page_index + 1) * page_size]
            description = "\n".join(
                self._catalog_line(entry, show_verdict=show_verdict)
                for entry in chunk
            )
            embed = discord.Embed(
                title=title,
                description=description[:4096],
                colour=discord.Colour.blurple(),
            )
            if show_verdict:
                embed.add_field(
                    name="Legenda Italia",
                    value=(
                        "✅ disponibile · ❌ non disponibile · "
                        "❔ disponibilita non confermata"
                    ),
                    inline=False,
                )
            embed.set_footer(
                text=(
                    f"Pagina {page_index + 1}/{pages} · {len(entries)} Quest · "
                    f"dedupe: {self._last_duplicate_groups} gruppi duplicati"
                )
            )
            await ctx.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @QuestTrackerV80.quest.command(name="tutte")
    async def quest_all(self, ctx: commands.Context) -> None:
        """Lista tutte le Quest attive del catalogo unificato S1+S2+S3."""
        entries = await self._all_active_catalog()
        await self._send_catalog_pages(
            ctx,
            entries,
            title="Tutte le Quest attive",
            show_verdict=True,
        )

    @QuestTrackerV80.quest.command(name="italiane")
    async def quest_italian(self, ctx: commands.Context) -> None:
        """Lista solo le Quest attive confermate disponibili in Italia."""
        entries = [
            entry
            for entry in await self._all_active_catalog()
            if str(entry.get("_availability") or "unknown") == "allowed"
        ]
        await self._send_catalog_pages(
            ctx,
            entries,
            title="Quest attive disponibili in Italia",
            show_verdict=False,
        )
