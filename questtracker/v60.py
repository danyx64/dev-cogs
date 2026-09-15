from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from .v51 import VERIFIED_ITALY_IDS
from .v52 import QuestTracker as QuestTrackerV52


# Conferma reale: questa Quest e visibile/completabile da un account italiano.
VERIFIED_ITALY_IDS.add("1547895090830250055")

# Sostituiamo solo lo status; gli altri comandi v5.2 (cerca, diagnostica,
# reinvia/forza) useranno automaticamente il nuovo motore decisionale.
QuestTrackerV52.quest.remove_command("status")


class QuestTracker(QuestTrackerV52):
    """QuestTracker 6.0.0: policy regionale riscritta per non perdere le nuove Quest.

    Principio:
    - una conferma IT/EU/global vince e viene notificata;
    - una restrizione estera esplicita blocca quella variante;
    - conflitti veri tra fonti restano sospesi;
    - se TUTTE le varianti della campagna sono ancora senza regione e nessuna
      fonte le dichiara estere, aspetta un intero ciclo (15s) per permettere ai
      dataset regioni di aggiornarsi e poi notifica una sola variante best-effort.

    In questo modo non torniamo al vecchio errore "unknown = Italia" immediato,
    ma non restiamo neppure bloccati per minuti su una Quest globale/italiana
    appena uscita mentre il feed regioni e in ritardo.
    """

    __version__ = "6.0.0"
    UNKNOWN_GRACE_SECONDS = 15

    def __init__(self, bot):
        super().__init__(bot)
        self._v60_family_first_seen: Dict[str, datetime] = {}

    def _family_decision(
        self,
        family: str,
        candidates: List[Tuple[Dict[str, Any], Dict[str, Any]]],
    ) -> Tuple[str, Optional[Dict[str, Any]], Optional[Dict[str, Any]], str]:
        now = datetime.now(timezone.utc)
        first_seen = self._v60_family_first_seen.setdefault(family, now)

        allowed: List[Tuple[int, Dict[str, Any], Dict[str, Any], str]] = []
        blocked: List[Tuple[str, str]] = []
        unknown: List[Tuple[int, Dict[str, Any], Dict[str, Any], str]] = []
        conflicts: List[Tuple[str, str]] = []

        for entry, config in candidates:
            qid = self._quest_id(entry)
            verdict, region_score, reason = self._italy_verdict(entry.get("_italy_region"))

            # v5.1 rappresenta il conflitto come unknown con questa motivazione.
            if verdict == "unknown" and "conflitto fonti" in reason.lower():
                conflicts.append((qid, reason))
                continue

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
                score = self._italian_hint(config) * 500 + self._entry_quality(entry)
                unknown.append((score, entry, config, reason))

        # Prova positiva: scegli la migliore variante e deduplica tutta la famiglia.
        if allowed:
            allowed.sort(key=lambda item: item[0], reverse=True)
            _score, entry, config, reason = allowed[0]
            qid = self._quest_id(entry)
            return "allowed", entry, config, f"Italia confermata via `{qid}`: {reason}"

        # Se le fonti si contraddicono non indoviniamo.
        if conflicts:
            detail = "; ".join(f"{qid}: {reason}" for qid, reason in conflicts[:3])
            return "unknown", None, None, f"conflitto regionale reale: {detail}"

        # Caso pericoloso tipo vecchie campagne US + variante senza metadati:
        # non consideriamo automaticamente italiana la variante unknown.
        if blocked and unknown:
            detail = "; ".join(f"{qid}: {reason}" for qid, reason in blocked[:3])
            return (
                "unknown",
                None,
                None,
                f"esistono varianti esplicitamente estere; attendo prova IT. {detail}",
            )

        # Tutta la campagna e ancora unknown. I dataset regione spesso arrivano
        # qualche secondo dopo quest.json: aspettiamo un ciclo completo, poi
        # inviamo UNA sola variante se nel frattempo nessuna fonte l'ha bloccata.
        if unknown:
            age = (now - first_seen).total_seconds()
            if age < self.UNKNOWN_GRACE_SECONDS:
                return (
                    "unknown",
                    None,
                    None,
                    f"regione non ancora pubblicata; ricontrollo tra {max(1, int(self.UNKNOWN_GRACE_SECONDS - age))}s",
                )

            unknown.sort(key=lambda item: item[0], reverse=True)
            _score, entry, config, reason = unknown[0]
            qid = self._quest_id(entry)
            sources = ", ".join(entry.get("_quest_sources") or ["feed corrente"])
            return (
                "allowed",
                entry,
                config,
                f"best-effort dopo grace: `{qid}` senza blocchi regionali; fonti {sources}; dato regione: {reason}",
            )

        if blocked:
            detail = "; ".join(f"{qid}: {reason}" for qid, reason in blocked[:4])
            return "blocked", None, None, detail

        return "unknown", None, None, "nessuna variante utilizzabile"

    @QuestTrackerV52.quest.command(name="status")
    async def quest_status(self, ctx):
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(settings.get("channel_id") or 0)
        role = ctx.guild.get_role(settings.get("role_id") or 0)

        source_lines = []
        for label, _url in self.QUEST_SOURCES:
            count = self._source_counts.get(label, 0)
            error = self._source_errors.get(label)
            source_lines.append(f"• **{label}**: {count}" + (f" ({error})" if error else ""))

        embed = discord.Embed(title="QuestTracker", colour=discord.Colour.blurple())
        embed.add_field(name="Versione", value=self.__version__, inline=True)
        embed.add_field(name="Polling", value="15 secondi", inline=True)
        embed.add_field(name="Stato", value="Attivo" if settings.get("enabled") else "Disattivato", inline=True)
        embed.add_field(name="Canale", value=channel.mention if channel else "Non configurato", inline=True)
        embed.add_field(name="Ruolo", value=role.mention if role else "Nessuno", inline=True)
        embed.add_field(name="Fonti Quest", value="\n".join(source_lines)[:1024] or "n/a", inline=False)
        embed.add_field(
            name="Policy Italia v6",
            value=(
                "IT/EU/global = invio; estera esplicita = blocco; conflitto = attesa; "
                "campagna tutta senza regione = 15s di grace e poi una sola notifica best-effort. "
                "`.quest reinvia <id>` resta un force reale e manda esattamente quell'ID."
            ),
            inline=False,
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
