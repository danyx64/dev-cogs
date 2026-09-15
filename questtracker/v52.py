from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import discord
from redbot.core import commands

from .questtracker import _canonical_key, _parse_iso, _quest_config
from .v51 import QuestTracker as QuestTrackerV51


for _name in (
    "cerca", "ispeziona", "inspect",
    "diagnostica", "debugquest", "regioni",
    "reinvia", "resend", "forza", "force",
):
    QuestTrackerV51.quest.remove_command(_name)


class QuestTracker(QuestTrackerV51):
    """QuestTracker 5.2.0: consenso per famiglia e reinvio forzato reale."""

    __version__ = "5.2.0"

    def _family_groups(
        self,
        entries: Iterable[Dict[str, Any]],
    ) -> Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]]:
        groups: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
        for entry, config in self._raw_active_entries(entries):
            family = self._family_key(entry, config)
            groups.setdefault(family, []).append((entry, config))
        return groups

    def _family_decision(
        self,
        family: str,
        candidates: List[Tuple[Dict[str, Any], Dict[str, Any]]],
    ) -> Tuple[str, Optional[Dict[str, Any]], Optional[Dict[str, Any]], str]:
        """Decide la disponibilita della campagna usando tutte le varianti regionali.

        Una variante estera non rende estera l'intera campagna: se una variante
        della stessa famiglia e confermata IT/EU/global, la campagna e disponibile
        in Italia e viene scelta solo quella variante. Se non esiste alcuna prova
        positiva ma restano varianti senza regione, la famiglia resta in attesa.
        """
        allowed: List[Tuple[int, Dict[str, Any], Dict[str, Any], str]] = []
        blocked: List[Tuple[str, str]] = []
        unknown: List[Tuple[str, str]] = []

        for entry, config in candidates:
            qid = self._quest_id(entry)
            verdict, region_score, reason = self._italy_verdict(entry.get("_italy_region"))
            if verdict == "allowed":
                score = (
                    region_score * 100
                    + self._italian_hint(config) * 500
                    + self._entry_quality(entry)
                )
                allowed.append((score, entry, config, reason))
            elif verdict == "blocked":
                blocked.append((qid, reason))
            else:
                unknown.append((qid, reason))

        if allowed:
            allowed.sort(key=lambda item: item[0], reverse=True)
            _score, entry, config, reason = allowed[0]
            qid = self._quest_id(entry)
            extra = len(allowed) - 1
            suffix = f"; altre {extra} varianti compatibili" if extra else ""
            return "allowed", entry, config, f"famiglia IT via `{qid}`: {reason}{suffix}"

        if unknown:
            sample = "; ".join(f"{qid}: {reason}" for qid, reason in unknown[:3])
            if blocked:
                sample += f"; {len(blocked)} varianti estere note"
            return "unknown", None, None, sample or "famiglia senza prova Italia"

        if blocked:
            sample = "; ".join(f"{qid}: {reason}" for qid, reason in blocked[:3])
            return "blocked", None, None, sample

        return "unknown", None, None, f"famiglia `{family}` senza varianti utilizzabili"

    def _active_quests(
        self,
        entries: Iterable[Dict[str, Any]],
    ) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
        """Seleziona una sola variante italiana per ogni campagna."""
        groups = self._family_groups(entries)
        selected: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []

        for family, candidates in groups.items():
            verdict, entry, config, _reason = self._family_decision(family, candidates)
            if verdict == "allowed" and entry is not None and config is not None:
                selected.append((family, entry, config))

        selected.sort(
            key=lambda item: _parse_iso(item[2].get("starts_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return selected

    async def _record_forced_delivery(
        self,
        guild: discord.Guild,
        family: str,
        qid: str,
        entry: Dict[str, Any],
        config: Dict[str, Any],
    ) -> None:
        conf = self.config.guild(guild)

        sent_ids = [str(v) for v in await conf.v51_sent_ids()]
        sent_families = [str(v) for v in await conf.v51_sent_families()]
        if qid not in sent_ids:
            sent_ids.append(qid)
        if family not in sent_families:
            sent_families.append(family)
        await conf.v51_sent_ids.set(sent_ids[-1000:])
        await conf.v51_sent_families.set(sent_families[-1000:])

        v50_ids = [str(v) for v in await conf.v50_seen_ids()]
        v50_families = [str(v) for v in await conf.v50_seen_families()]
        if qid not in v50_ids:
            v50_ids.append(qid)
        if family not in v50_families:
            v50_families.append(family)
        await conf.v50_seen_ids.set(v50_ids[-500:])
        await conf.v50_seen_families.set(v50_families[-500:])

        legacy = [str(v) for v in await conf.seen_keys()]
        key = _canonical_key(entry, config)
        if key not in legacy:
            legacy.append(key)
        await conf.seen_keys.set(legacy[-500:])

    @QuestTrackerV51.quest.command(name="reinvia", aliases=["resend", "forza", "force"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_resend(self, ctx: commands.Context, quest_id: str):
        """Invia ESATTAMENTE l'ID richiesto, ignorando regione, dedupe e storico."""
        quest_id = str(quest_id).strip()
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(settings.get("channel_id") or 0)
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send("Canale Quest non configurato.")

        async with ctx.typing():
            entries = await self._fetch_quests_live()

        entry = next((item for item in entries if self._quest_id(item) == quest_id), None)
        if entry is None:
            return await ctx.send(
                f"Quest `{quest_id}` non trovata in nessuna fonte corrente: non posso costruire il link/card in modo affidabile."
            )

        config = _quest_config(entry)
        if not config:
            return await ctx.send(f"Quest `{quest_id}` trovata ma payload non valido.")
        if self._quest_share_url(entry, config) is None:
            return await ctx.send(f"Quest `{quest_id}` trovata ma non ha un link Discord Quest valido.")

        exact_verdict, _score, exact_reason = self._italy_verdict(entry.get("_italy_region"))
        family = self._family_key(entry, config)

        try:
            await self._send_quest(channel, ctx.guild, entry, config, test=False)
        except discord.Forbidden:
            return await ctx.send("Invio forzato fallito: permessi insufficienti nel canale Quest.")
        except discord.HTTPException as exc:
            return await ctx.send(
                f"Invio forzato fallito: HTTP {getattr(exc, 'status', '?')} / code {getattr(exc, 'code', '?')}."
            )

        await self._record_forced_delivery(ctx.guild, family, quest_id, entry, config)
        await ctx.send(
            f"Quest `{quest_id}` inviata FORZATAMENTE in {channel.mention}. "
            f"Il filtro regione e stato ignorato (`{exact_verdict}`: {exact_reason})."
        )

    @QuestTrackerV51.quest.command(name="cerca", aliases=["ispeziona", "inspect"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_lookup(self, ctx: commands.Context, quest_id: str):
        """Mostra verdetto dell'ID e, separatamente, verdetto dell'intera campagna."""
        quest_id = str(quest_id).strip()
        async with ctx.typing():
            entries = await self._fetch_quests_live()

        entry = next((item for item in entries if self._quest_id(item) == quest_id), None)
        if entry is None:
            return await ctx.send(f"Quest `{quest_id}` non trovata nelle fonti correnti.")

        config = _quest_config(entry)
        if not config:
            return await ctx.send(f"Quest `{quest_id}` trovata ma payload non valido.")

        family = self._family_key(entry, config)
        candidates = self._family_groups(entries).get(family, [])
        family_verdict, selected, _selected_config, family_reason = self._family_decision(family, candidates)
        exact_verdict, _score, exact_reason = self._italy_verdict(entry.get("_italy_region"))

        messages = config.get("messages") or {}
        name = str(messages.get("quest_name") or messages.get("game_title") or "Discord Quest")
        selected_id = self._quest_id(selected) if selected else "nessuno"
        variant_lines = []
        for candidate, _cfg in candidates[:12]:
            cid = self._quest_id(candidate)
            verdict, _vscore, reason = self._italy_verdict(candidate.get("_italy_region"))
            marker = "->" if cid == selected_id else "-"
            variant_lines.append(f"{marker} `{cid}`: **{verdict}** - {reason}")

        embed = discord.Embed(title=f"Quest {quest_id}", colour=discord.Colour.blurple())
        embed.add_field(name="Nome", value=name[:1024], inline=False)
        embed.add_field(
            name="Questo ID",
            value=f"**{exact_verdict}** - {exact_reason}"[:1024],
            inline=False,
        )
        embed.add_field(
            name="Campagna / famiglia",
            value=f"**{family_verdict}** - {family_reason}"[:1024],
            inline=False,
        )
        embed.add_field(name="ID scelto automaticamente", value=f"`{selected_id}`", inline=False)
        if variant_lines:
            embed.add_field(name="Varianti collegate", value="\n".join(variant_lines)[:1024], inline=False)
        embed.add_field(
            name="Forza invio",
            value=f"`.quest reinvia {quest_id}` ignora regione, dedupe e storico e manda proprio questo ID.",
            inline=False,
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @QuestTrackerV51.quest.command(name="diagnostica", aliases=["debugquest", "regioni"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_diagnostics(self, ctx: commands.Context):
        async with ctx.typing():
            entries = await self._fetch_quests_live()

        groups = self._family_groups(entries)
        allowed = blocked = unknown = 0
        lines: List[str] = []

        for family, candidates in groups.items():
            verdict, selected, selected_config, reason = self._family_decision(family, candidates)
            if verdict == "allowed":
                allowed += 1
            elif verdict == "blocked":
                blocked += 1
            else:
                unknown += 1

            sample_config = selected_config or candidates[0][1]
            messages = sample_config.get("messages") or {}
            name = str(messages.get("quest_name") or messages.get("game_title") or "Discord Quest")
            chosen = self._quest_id(selected) if selected else "-"
            ids = ", ".join(self._quest_id(entry) for entry, _cfg in candidates[:6])
            state = {"allowed": "IT OK", "blocked": "NO IT", "unknown": "ATTESA"}[verdict]
            lines.append(
                f"**{state}** - {name}\n"
                f"scelto: `{chosen}` | varianti: `{ids}`\n"
                f"{reason}"
            )

        header = (
            f"Campagne attive: **{len(groups)}** | IT: **{allowed}** | "
            f"non-IT: **{blocked}** | attesa: **{unknown}**"
        )
        chunks: List[str] = []
        current = ""
        for line in lines:
            candidate = current + ("\n\n" if current else "") + line
            if len(candidate) > 3400:
                chunks.append(current)
                current = line
            else:
                current = candidate
        if current:
            chunks.append(current)

        if not chunks:
            return await ctx.send(header)

        for index, chunk in enumerate(chunks[:5], 1):
            embed = discord.Embed(
                title=f"Diagnostica QuestTracker v5.2 ({index}/{min(len(chunks), 5)})",
                description=(header + "\n\n" + chunk)[:4096],
                colour=discord.Colour.blurple(),
            )
            await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
