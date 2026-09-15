from typing import Any, Dict, List, Tuple

import discord
from redbot.core import commands

from .v61 import QuestTracker as QuestTrackerV61


# Sostituiamo lo status v6.1. Gli altri comandi, incluso reinvia/forza,
# restano quelli v5.2 e continuano a funzionare con il motore nuovo.
QuestTrackerV61.quest.remove_command("status")


class QuestTracker(QuestTrackerV61):
    """QuestTracker 6.2.0: automatico solo con prova regionale positiva.

    Regola fondamentale:
    - auto-invio SOLO se l'ID specifico risulta disponibile in Italia dalle
      fonti regionali (IT/EU/EEA/global oppure blacklist che non esclude IT),
      senza conflitti;
    - unknown resta in attesa e viene rivalutato, non viene mai promosso a IT
      per supposizione;
    - non-IT non viene inviato automaticamente;
    - `.quest reinvia <id>` resta il force manuale e ignora questi filtri.
    """

    __version__ = "6.2.0"
    STATE_LIMIT = 5000
    MAX_AUTO_SEND_PER_SCAN = 1
    AUTO_SEND_COOLDOWN_SECONDS = 60

    def __init__(self, bot):
        super().__init__(bot)
        self.config.register_guild(
            v62_migrated=False,
            v62_known_ids=[],
            v62_known_families=[],
            v62_waiting_ids=[],
            v62_pending_ids=[],
            v62_last_auto_send=None,
        )

    def _exact_auto_verdict(self, entry: Dict[str, Any]) -> Tuple[str, str]:
        """Verdetto dell'ID esatto usato dall'automatico.

        `_italy_verdict` v5.1 e gia fail-closed sui conflitti: ritorna allowed
        solo con evidenza regionale positiva utilizzabile per l'Italia.
        """
        verdict, _score, reason = self._italy_verdict(entry.get("_italy_region"))
        return verdict, reason

    async def _baseline_v62(
        self,
        guild: discord.Guild,
        current: Dict[str, Tuple[Dict[str, Any], Dict[str, Any], str]],
    ) -> None:
        """Crea una baseline senza inviare nulla.

        Serve anche a neutralizzare le code delle versioni precedenti: al primo
        avvio v6.2 tutto cio che e gia presente viene considerato noto. Da quel
        momento l'automatico reagisce soltanto a ID comparsi dopo la baseline.
        """
        conf = self.config.guild(guild)
        ids = list(current.keys())[-self.STATE_LIMIT:]
        families: List[str] = []
        for _qid, (_entry, _config, family) in current.items():
            if family not in families:
                families.append(family)

        await conf.v62_known_ids.set(ids)
        await conf.v62_known_families.set(families[-self.STATE_LIMIT:])
        await conf.v62_waiting_ids.set([])
        await conf.v62_pending_ids.set([])
        await conf.v62_migrated.set(True)

        # Elimina esplicitamente la coda v6.1 che poteva contenere campagne
        # recuperate dalle versioni precedenti.
        await conf.v61_pending_families.set([])

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
        current: Dict[str, Tuple[Dict[str, Any], Dict[str, Any], str]] = {}
        for entry, config in self._raw_active_entries(entries):
            qid = self._quest_id(entry)
            if not qid:
                continue
            current[qid] = (entry, config, self._family_key(entry, config))

        conf = self.config.guild(guild)

        if not settings.get("v62_migrated"):
            await self._baseline_v62(guild, current)
            return 0

        known_ids = [str(v) for v in settings.get("v62_known_ids") or []]
        known_families = [str(v) for v in settings.get("v62_known_families") or []]
        waiting_ids = [str(v) for v in settings.get("v62_waiting_ids") or []]
        pending_ids = [str(v) for v in settings.get("v62_pending_ids") or []]

        known_id_set = set(known_ids)
        known_family_set = set(known_families)
        waiting_set = set(waiting_ids)
        pending_set = set(pending_ids)

        sent_ids = {str(v) for v in settings.get("v51_sent_ids") or []}
        sent_families = {str(v) for v in settings.get("v51_sent_families") or []}

        # 1) Rileva SOLO ID realmente nuovi rispetto alla baseline persistente.
        for qid, (entry, _config, family) in current.items():
            if qid in known_id_set:
                continue

            known_id_set.add(qid)
            known_ids.append(qid)
            if family not in known_family_set:
                known_family_set.add(family)
                known_families.append(family)

            if qid in sent_ids or family in sent_families:
                continue

            verdict, _reason = self._exact_auto_verdict(entry)
            if verdict == "allowed":
                if qid not in pending_set:
                    pending_set.add(qid)
                    pending_ids.append(qid)
            elif verdict == "unknown":
                if qid not in waiting_set:
                    waiting_set.add(qid)
                    waiting_ids.append(qid)
            # blocked: viene registrato come noto ma non entra in nessuna coda.

        # 2) Gli ID nuovi ma inizialmente unknown vengono rivalutati ogni 15s.
        #    Solo una successiva conferma regionale positiva li promuove.
        new_waiting: List[str] = []
        for qid in waiting_ids:
            item = current.get(qid)
            if item is None:
                continue
            entry, _config, family = item
            if qid in sent_ids or family in sent_families:
                continue

            verdict, _reason = self._exact_auto_verdict(entry)
            if verdict == "allowed":
                if qid not in pending_set:
                    pending_set.add(qid)
                    pending_ids.append(qid)
                waiting_set.discard(qid)
            elif verdict == "unknown":
                new_waiting.append(qid)
            # blocked: esce dalla coda definitivamente.
        waiting_ids = new_waiting
        waiting_set = set(waiting_ids)

        # 3) Ripulisce i pending e ricontrolla il verdetto prima di ogni invio.
        clean_pending: List[str] = []
        for qid in pending_ids:
            item = current.get(qid)
            if item is None:
                continue
            entry, _config, family = item
            if qid in sent_ids or family in sent_families:
                continue

            verdict, _reason = self._exact_auto_verdict(entry)
            if verdict == "allowed":
                clean_pending.append(qid)
            elif verdict == "unknown" and qid not in waiting_set:
                waiting_ids.append(qid)
                waiting_set.add(qid)
        pending_ids = clean_pending
        pending_set = set(pending_ids)

        await conf.v62_known_ids.set(known_ids[-self.STATE_LIMIT:])
        await conf.v62_known_families.set(known_families[-self.STATE_LIMIT:])
        await conf.v62_waiting_ids.set(waiting_ids[-self.STATE_LIMIT:])
        await conf.v62_pending_ids.set(pending_ids[-self.STATE_LIMIT:])

        if not pending_ids:
            return 0

        # Circuit breaker: anche con piu Quest italiane vere insieme, massimo
        # una notifica automatica al minuto per server.
        now = discord.utils.utcnow()
        last_send = self._parse_saved_time(settings.get("v62_last_auto_send"))
        if last_send and (now - last_send).total_seconds() < self.AUTO_SEND_COOLDOWN_SECONDS:
            return 0

        sent = 0
        for qid in list(pending_ids):
            if sent >= self.MAX_AUTO_SEND_PER_SCAN:
                break

            item = current.get(qid)
            if item is None:
                pending_set.discard(qid)
                continue
            entry, config, family = item

            if qid in sent_ids or family in sent_families:
                pending_set.discard(qid)
                continue

            verdict, reason = self._exact_auto_verdict(entry)
            if verdict != "allowed":
                pending_set.discard(qid)
                if verdict == "unknown" and qid not in waiting_set:
                    waiting_set.add(qid)
                    waiting_ids.append(qid)
                continue

            if self._quest_share_url(entry, config) is None:
                self._last_scan_errors[guild.id].append(f"Quest `{qid}`: link non valido.")
                pending_set.discard(qid)
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

            await self._record_forced_delivery(guild, family, qid, entry, config)
            sent_ids.add(qid)
            sent_families.add(family)
            pending_set.discard(qid)

            # Dedupe di famiglia: dopo un invio elimina eventuali altre varianti
            # della stessa campagna dalle code automatiche.
            for other_id, (_other_entry, _other_config, other_family) in current.items():
                if other_family == family:
                    pending_set.discard(other_id)
                    waiting_set.discard(other_id)

            await conf.v62_last_auto_send.set(now.isoformat())
            sent += 1

        pending_ids = [qid for qid in pending_ids if qid in pending_set]
        waiting_ids = [qid for qid in waiting_ids if qid in waiting_set]
        await conf.v62_pending_ids.set(pending_ids[-self.STATE_LIMIT:])
        await conf.v62_waiting_ids.set(waiting_ids[-self.STATE_LIMIT:])
        return sent

    @QuestTrackerV61.quest.command(name="status")
    async def quest_status(self, ctx: commands.Context):
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(settings.get("channel_id") or 0)
        role = ctx.guild.get_role(settings.get("role_id") or 0)

        embed = discord.Embed(title="QuestTracker", colour=discord.Colour.blurple())
        embed.add_field(name="Versione", value=self.__version__, inline=True)
        embed.add_field(name="Polling", value="15 secondi", inline=True)
        embed.add_field(name="Stato", value="Attivo" if settings.get("enabled") else "Disattivato", inline=True)
        embed.add_field(name="Canale", value=channel.mention if channel else "Non configurato", inline=True)
        embed.add_field(name="Ruolo", value=role.mention if role else "Nessuno", inline=True)
        embed.add_field(
            name="Automatico",
            value=(
                "Invia solo nuovi ID con prova positiva per Italia: IT/EU/EEA/global oppure "
                "blacklist regionale che non esclude l'Italia. Unknown e conflitti aspettano; non-IT viene scartato."
            ),
            inline=False,
        )
        embed.add_field(
            name="Sicurezza",
            value="Nessun replay all'upgrade, dedupe per famiglia e massimo 1 invio automatico ogni 60 secondi.",
            inline=False,
        )
        embed.add_field(
            name="Manuale",
            value="`.quest reinvia <id>` forza esattamente quell'ID ignorando regione, dedupe e storico.",
            inline=False,
        )
        embed.add_field(name="In attesa regione", value=str(len(settings.get("v62_waiting_ids") or [])), inline=True)
        embed.add_field(name="Pronte", value=str(len(settings.get("v62_pending_ids") or [])), inline=True)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
