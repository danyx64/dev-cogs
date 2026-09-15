from typing import Any, Dict, Optional

import discord
from redbot.core import commands

from .questtracker import _canonical_key
from .v41 import QuestTracker as QuestTrackerV41
from .v42 import ITALY_REGION_CODES, _norm_region
from .v45 import QuestTracker as QuestTrackerV45


# Evita doppie registrazioni dei sottocomandi in caso di reload del cog.
for _command_name in ("diagnostica", "debugquest", "regioni", "reinvia", "resend"):
    QuestTrackerV45.quest.remove_command(_command_name)


class QuestTracker(QuestTrackerV45):
    """QuestTracker 4.6.1: filtro Italia piu affidabile e diagnostica delle Quest."""

    __version__ = "4.6.1"

    @staticmethod
    def _region_allows_italy(region: Optional[Dict[str, Any]]) -> bool:
        """Filtro Italia bilanciato.

        Il feed regioni e' best-effort e puo' arrivare in ritardo rispetto al feed
        Quest. Per questo una Quest senza metadati regionali non viene piu persa.
        Restano invece bloccate le Quest con una allow-list esplicitamente estera
        o con un'esclusione esplicita di Italia/Europa.
        """
        if not region:
            return True

        include = {_norm_region(value) for value in region.get("include", []) if value}
        exclude = {_norm_region(value) for value in region.get("exclude", []) if value}

        # Un'esclusione esplicita di Italia/Europa vince sempre.
        if exclude & ITALY_REGION_CODES:
            return False

        # Se il mirror dichiara la Quest globale, e' valida anche in Italia.
        if region.get("is_global"):
            return True

        # Con una allow-list esplicita accettiamo soltanto Italia o una regione
        # che comprende l'Italia (EU/EEA/Europe).
        if include:
            return bool(include & ITALY_REGION_CODES)

        # Nessuna allow-list e nessuna esclusione Italia: il mirror non fornisce
        # abbastanza dati per giustificare uno scarto. Accettiamo best-effort.
        return True

    @staticmethod
    def _family_key(entry: Dict[str, Any], config: Dict[str, Any]) -> str:
        """Evita il vecchio dedupe troppo aggressivo per gioco/nome Quest.

        v4.2 raggruppava tutte le Quest con stesso application id + nome, cosa che
        poteva nascondere campagne distinte contemporanee. Il canonical key base
        include finestra temporale, task e ricompensa ed e' quindi molto piu sicuro.
        """
        return _canonical_key(entry, config)

    @staticmethod
    def _region_summary(region: Optional[Dict[str, Any]]) -> str:
        if not region:
            return "metadati regione assenti -> accettata best-effort"

        include = [str(v) for v in region.get("include", []) if v]
        exclude = [str(v) for v in region.get("exclude", []) if v]
        if region.get("is_global"):
            base = "globale"
        elif include:
            base = "include: " + ", ".join(include)
        elif exclude:
            base = "exclude: " + ", ".join(exclude)
        else:
            base = "nessun vincolo regionale"

        if exclude and not base.startswith("exclude:"):
            base += " | exclude: " + ", ".join(exclude)
        return base

    @QuestTrackerV45.quest.command(name="diagnostica", aliases=["debugquest", "regioni"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_diagnostics(self, ctx: commands.Context):
        """Mostra cosa arriva dai feed, cosa passa il filtro Italia e cosa e' gia visto."""
        async with ctx.typing():
            entries = await self._fetch_quests()
            # Bypass del filtro regionale, ma mantiene controllo date/test/dedupe base.
            raw_active = QuestTrackerV41._active_quests(self, entries)
            accepted = self._active_quests(entries)

        accepted_canonicals = {canonical for canonical, _, _ in accepted}
        seen = set(await self.config.guild(ctx.guild).seen_keys())

        lines = []
        for canonical, entry, config in raw_active:
            messages = config.get("messages") or {}
            app = config.get("application") or {}
            name = str(
                messages.get("quest_name")
                or messages.get("game_title")
                or app.get("name")
                or "Discord Quest"
            )
            quest_id = str(entry.get("id") or config.get("id") or "?")
            region = entry.get("_italy_region")

            if canonical not in accepted_canonicals:
                state = "⛔ scartata"
            elif canonical in seen:
                state = "☑️ Italia, gia vista"
            else:
                state = "✅ Italia, nuova"

            lines.append(
                f"**{state}** • `{quest_id}` • {name}\n"
                f"↳ {self._region_summary(region)}"
            )

        header = (
            f"Feed attive: **{len(raw_active)}** • "
            f"accettate Italia: **{len(accepted)}** • "
            f"scartate: **{max(0, len(raw_active) - len(accepted))}**"
        )

        if not lines:
            return await ctx.send(header + "\nNessuna Quest attiva trovata nei feed.")

        # Discord limita la descrizione embed a 4096 caratteri.
        chunks = []
        current = ""
        for line in lines:
            candidate = current + ("\n\n" if current else "") + line
            if len(candidate) > 3800:
                chunks.append(current)
                current = line
            else:
                current = candidate
        if current:
            chunks.append(current)

        for index, chunk in enumerate(chunks[:3]):
            title = "🔎 Diagnostica QuestTracker"
            if len(chunks) > 1:
                title += f" ({index + 1}/{min(len(chunks), 3)})"
            embed = discord.Embed(
                title=title,
                description=(header + "\n\n" + chunk)[:4096],
                colour=discord.Colour.blurple(),
            )
            await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @QuestTrackerV45.quest.command(name="reinvia", aliases=["resend"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_resend(self, ctx: commands.Context, quest_id: str):
        """Reinvia una Quest attiva per ID anche se era gia stata marcata come vista."""
        quest_id = str(quest_id).strip()
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(settings.get("channel_id") or 0)
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send("❌ Canale Quest non configurato. Usa `.quest setup #canale`.")

        async with ctx.typing():
            entries = await self._fetch_quests()
            active = self._active_quests(entries)

        for canonical, entry, config in active:
            current_id = str(entry.get("id") or config.get("id") or config.get("quest_id") or "").strip()
            if current_id != quest_id:
                continue

            try:
                await self._send_quest(channel, ctx.guild, entry, config, test=False)
            except discord.Forbidden:
                return await ctx.send("❌ Non posso inviare messaggi nel canale Quest configurato.")
            except discord.HTTPException as exc:
                return await ctx.send(f"❌ Discord ha rifiutato l'invio della Quest: `{exc}`")

            seen = set(settings.get("seen_keys") or [])
            seen.add(canonical)
            await self.config.guild(ctx.guild).seen_keys.set(list(seen)[-500:])
            return await ctx.send(f"✅ Quest `{quest_id}` reinviata in {channel.mention}.")

        # Se esiste nel feed ma non passa il filtro Italia, spiega il motivo.
        raw_active = QuestTrackerV41._active_quests(self, entries)
        for _, entry, config in raw_active:
            current_id = str(entry.get("id") or config.get("id") or config.get("quest_id") or "").strip()
            if current_id == quest_id:
                return await ctx.send(
                    "⛔ La Quest e attiva nel feed ma non passa il filtro Italia: "
                    + self._region_summary(entry.get("_italy_region"))
                )

        await ctx.send("❌ Non trovo una Quest attiva con quell'ID nei feed correnti.")
