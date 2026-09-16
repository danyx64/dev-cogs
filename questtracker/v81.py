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


# Replace the v8 commands whose behaviour changes in v8.1.
for _command_name in (
    "attive",
    "active",
    "reinvia",
    "resend",
    "forza",
    "force",
):
    QuestTrackerV80.quest.remove_command(_command_name)


class QuestTracker(QuestTrackerV80):
    """QuestTracker v8.1: catalogo unificato e dedupe tra tutte le sorgenti."""

    __version__ = "8.1.0"

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
        """Choose the richest/most authoritative representative of a duplicate group."""
        score = cls._availability_rank(entry) * 10000
        sources = set(entry.get("_sources") or [])
        if "source1-selfbot" in sources:
            score += 1000
        if "source2-discordquest" in sources:
            score += 500
        if "source3-api-diff" in sources:
            score += 100

        config = _quest_config(entry)
        for key in ("application", "messages", "starts_at", "expires_at", "rewards_config", "rewards"):
            if config.get(key):
                score += 10
        score += min(len(str(entry)), 5000) // 250
        return score

    @staticmethod
    def _dedupe_identity(entry: Dict[str, Any]) -> str:
        """Return a safe cross-source identity.

        Exact IDs are already merged by v8. Here we additionally collapse the
        same campaign when two sources expose different IDs but the campaign
        metadata is strong enough to identify the same Quest family.
        """
        qid = _quest_id(entry)
        config = _quest_config(entry)
        messages = config.get("messages") if isinstance(config.get("messages"), dict) else {}
        app = config.get("application") if isinstance(config.get("application"), dict) else {}
        app_id = str(app.get("id") or config.get("application_id") or "").strip()
        name = re.sub(
            r"\s+",
            " ",
            str(messages.get("quest_name") or "").strip().casefold(),
        )
        starts = str(config.get("starts_at") or "").strip()
        expires = str(config.get("expires_at") or "").strip()

        # Do not family-dedupe sparse records: unrelated incomplete entries must
        # never collapse into one another merely because fields are missing.
        strong_signals = sum(bool(value) for value in (app_id, name, starts, expires))
        if name and strong_signals >= 3:
            return "family:" + _family_key(entry)
        return "id:" + qid

    @classmethod
    def _combine_duplicates(
        cls,
        left: Dict[str, Any],
        right: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Combine duplicate Quest records while preserving IDs and source evidence."""
        if cls._entry_preference_score(right) > cls._entry_preference_score(left):
            preferred, fallback = right, left
        else:
            preferred, fallback = left, right

        result: Dict[str, Any] = dict(fallback)
        for key, value in preferred.items():
            if key == "config" and isinstance(value, dict):
                old_config = result.get("config") if isinstance(result.get("config"), dict) else {}
                merged_config = dict(old_config)
                merged_config.update(value)
                result["config"] = merged_config
            else:
                result[key] = value

        sources: List[str] = []
        for entry in (left, right):
            for source in entry.get("_sources") or []:
                if source not in sources:
                    sources.append(str(source))
        result["_sources"] = sources

        aliases: List[str] = []
        for entry in (left, right):
            candidate_aliases = [str(v) for v in entry.get("_alias_ids") or [] if v]
            qid = _quest_id(entry)
            if qid:
                candidate_aliases.append(qid)
            for alias in candidate_aliases:
                if alias not in aliases:
                    aliases.append(alias)
        result["_alias_ids"] = aliases
        result["_dedupe_family"] = _family_key(result)
        return result

    async def _fetch_all(self, *, force_selfbot: bool = False) -> List[Dict[str, Any]]:
        # v8 first merges exact Quest IDs across S1/S2/S3 and calculates the
        # Italy verdict. v8.1 then collapses same-family aliases across IDs.
        exact_id_catalog = await super()._fetch_all(force_selfbot=force_selfbot)

        deduped: Dict[str, Dict[str, Any]] = {}
        duplicate_groups: Set[str] = set()
        duplicate_aliases = 0

        for entry in exact_id_catalog:
            identity = self._dedupe_identity(entry)
            existing = deduped.get(identity)
            if existing is None:
                copy = dict(entry)
                qid = _quest_id(copy)
                copy["_alias_ids"] = [qid] if qid else []
                copy["_dedupe_family"] = _family_key(copy)
                deduped[identity] = copy
                continue

            duplicate_groups.add(identity)
            before = set(existing.get("_alias_ids") or [])
            combined = self._combine_duplicates(existing, entry)
            after = set(combined.get("_alias_ids") or [])
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
        aliases = [str(v) for v in entry.get("_alias_ids") or [] if v]
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
                    value="✅ disponibile · ❌ non disponibile · ❔ disponibilita non confermata",
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

    @QuestTrackerV80.quest.command(
        name="tutte",
        aliases=["all", "catalogo", "catalog"],
    )
    async def quest_all(self, ctx: commands.Context) -> None:
        """Lista tutte le Quest attive del catalogo unificato S1+S2+S3."""
        entries = await self._all_active_catalog()
        await self._send_catalog_pages(
            ctx,
            entries,
            title="Tutte le Quest attive",
            show_verdict=True,
        )

    @QuestTrackerV80.quest.command(
        name="italiane",
        aliases=["italy", "it", "attive", "active"],
    )
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

    @QuestTrackerV80.quest.command(
        name="reinvia",
        aliases=["resend", "forza", "force"],
    )
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_resend(self, ctx: commands.Context, quest_id: str) -> None:
        """Invia forzatamente una Quest, anche se gia vista o non marcata Italia."""
        wanted = str(quest_id).strip()
        entries = await self._fetch_all(force_selfbot=True)
        entry = next(
            (
                item
                for item in entries
                if wanted == _quest_id(item)
                or wanted in {str(v) for v in item.get("_alias_ids") or []}
            ),
            None,
        )
        if entry is None:
            return await ctx.send("❌ Quest non trovata nelle tre sorgenti.")

        channel_id = await self.config.guild(ctx.guild).channel_id()
        channel = ctx.guild.get_channel(channel_id or 0)
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send("❌ Canale Quest non configurato.")

        await self._send_quest(channel, ctx.guild, entry, test=False)
        canonical_id = _quest_id(entry)
        aliases = [str(v) for v in entry.get("_alias_ids") or [] if v]
        alias_text = (
            f" (gruppo deduplicato: {len(aliases)} ID)"
            if len(aliases) > 1
            else ""
        )
        await ctx.send(
            f"✅ Quest `{canonical_id}` inviata forzatamente in {channel.mention}{alias_text}."
        )
