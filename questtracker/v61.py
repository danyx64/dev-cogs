from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord
from redbot.core import commands

from .questtracker import _parse_iso
from .v51 import VERIFIED_ITALY_IDS
from .v52 import QuestTracker as QuestTrackerV52


# Confermata dall'utente come disponibile da un account italiano.
VERIFIED_ITALY_IDS.add("1547895090830250055")

# Sostituiamo lo status della v5.x con quello del motore anti-spam.
QuestTrackerV52.quest.remove_command("status")


class QuestTracker(QuestTrackerV52):
    """QuestTracker 6.1.0: rileva solo novita reali e non rigioca il catalogo attivo."""

    __version__ = "6.1.0"
    MAX_AUTO_SEND_PER_SCAN = 1
    AUTO_SEND_COOLDOWN_SECONDS = 60
    MIGRATION_RECOVERY_MINUTES = 20
    STATE_LIMIT = 4000

    def __init__(self, bot):
        super().__init__(bot)
        self.config.register_guild(
            v61_migrated=False,
            v61_known_ids=[],
            v61_known_families=[],
            v61_pending_families=[],
            v61_last_auto_send=None,
        )

    @staticmethod
    def _family_start(candidates: List[Tuple[Dict[str, Any], Dict[str, Any]]]) -> Optional[datetime]:
        starts = [_parse_iso(config.get("starts_at")) for _entry, config in candidates]
        starts = [value for value in starts if value is not None]
        return max(starts) if starts else None

    @staticmethod
    def _parse_saved_time(value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    async def _migrate_v61_state(
        self,
        guild: discord.Guild,
        entries: List[Dict[str, Any]],
        groups: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]],
        settings: Dict[str, Any],
    ) -> None:
        """Baseline sicura: tutto cio che esiste al momento dell'upgrade e gia noto.

        Non viene mai rigiocato l'intero catalogo. Recuperiamo automaticamente solo
        campagne iniziate negli ultimi 20 minuti, compatibili Italia e non gia inviate.
        """
        conf = self.config.guild(guild)
        known_ids = []
        for entry, _config in self._raw_active_entries(entries):
            qid = self._quest_id(entry)
            if qid and qid not in known_ids:
                known_ids.append(qid)

        known_families = list(groups.keys())
        sent_families = {str(value) for value in settings.get("v51_sent_families") or []}
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.MIGRATION_RECOVERY_MINUTES)
        pending: List[str] = []

        for family, candidates in groups.items():
            if family in sent_families:
                continue
            start = self._family_start(candidates)
            if start is None or start < cutoff:
                continue
            verdict, selected, selected_config, _reason = self._family_decision(family, candidates)
            if verdict == "allowed" and selected is not None and selected_config is not None:
                pending.append(family)

        await conf.v61_known_ids.set(known_ids[-self.STATE_LIMIT:])
        await conf.v61_known_families.set(known_families[-self.STATE_LIMIT:])
        await conf.v61_pending_families.set(pending[-self.STATE_LIMIT:])
        await conf.v61_migrated.set(True)

    async def _scan_guild(self, guild: discord.Guild, *, force: bool = False) -> int:
        """Scansiona ogni 15s ma notifica solo campagne/varianti comparse dopo la baseline.

        Le campagne pending restano in coda finche una variante diventa IT/EU/global.
        Unknown e non-IT NON vengono mai promosse automaticamente a Italia.
        """
        self._last_scan_errors[guild.id] = []
        settings = await self.config.guild(guild).all()
        if not settings.get("enabled") and not force:
            return 0

        channel = guild.get_channel(settings.get("channel_id") or 0)
        if not isinstance(channel, discord.TextChannel):
            self._last_scan_errors[guild.id].append("Canale Quest non configurato.")
            return 0

        entries = await self._fetch_quests()
        groups = self._family_groups(entries)
        conf = self.config.guild(guild)

        if not settings.get("v61_migrated"):
            await self._migrate_v61_state(guild, entries, groups, settings)
            return 0

        known_ids = [str(value) for value in settings.get("v61_known_ids") or []]
        known_families = [str(value) for value in settings.get("v61_known_families") or []]
        pending = [str(value) for value in settings.get("v61_pending_families") or []]
        known_id_set = set(known_ids)
        known_family_set = set(known_families)
        pending_set = set(pending)

        # Rileva novita vere. Una nuova variante di una famiglia gia nota rimette
        # la famiglia in pending, utile quando Discord pubblica l'ID italiano dopo.
        for entry, config in self._raw_active_entries(entries):
            qid = self._quest_id(entry)
            family = self._family_key(entry, config)
            is_new_id = bool(qid and qid not in known_id_set)
            is_new_family = family not in known_family_set

            if is_new_id:
                known_id_set.add(qid)
                known_ids.append(qid)
            if is_new_family:
                known_family_set.add(family)
                known_families.append(family)
            if is_new_id or is_new_family:
                if family not in pending_set:
                    pending_set.add(family)
                    pending.append(family)

        # Togli solo famiglie non piu attive. Unknown/non-IT restano pending:
        # se compare successivamente una variante italiana verranno rivalutate.
        active_family_set = set(groups)
        pending = [family for family in pending if family in active_family_set]
        pending_set = set(pending)

        await conf.v61_known_ids.set(known_ids[-self.STATE_LIMIT:])
        await conf.v61_known_families.set(known_families[-self.STATE_LIMIT:])
        await conf.v61_pending_families.set(pending[-self.STATE_LIMIT:])

        if not pending:
            return 0

        # Circuit breaker anti-mention storm: anche se una fonte impazzisce non
        # puo partire piu di una notifica automatica al minuto per server.
        now = datetime.now(timezone.utc)
        last_send = self._parse_saved_time(settings.get("v61_last_auto_send"))
        if last_send and (now - last_send).total_seconds() < self.AUTO_SEND_COOLDOWN_SECONDS:
            return 0

        sent_families = {str(value) for value in settings.get("v51_sent_families") or []}
        sent = 0

        for family in list(pending):
            if sent >= self.MAX_AUTO_SEND_PER_SCAN:
                break
            if family in sent_families:
                pending_set.discard(family)
                continue

            candidates = groups.get(family) or []
            verdict, entry, config, _reason = self._family_decision(family, candidates)
            if verdict != "allowed" or entry is None or config is None:
                continue

            qid = self._quest_id(entry)
            if not qid or self._quest_share_url(entry, config) is None:
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
            pending_set.discard(family)
            await conf.v61_last_auto_send.set(now.isoformat())
            sent_families.add(family)
            sent += 1

        pending = [family for family in pending if family in pending_set]
        await conf.v61_pending_families.set(pending[-self.STATE_LIMIT:])
        return sent

    @QuestTrackerV52.quest.command(name="status")
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
            name="Anti-spam",
            value="Baseline persistente + solo nuovi ID/famiglie + massimo 1 invio automatico ogni 60s.",
            inline=False,
        )
        embed.add_field(
            name="Filtro Italia",
            value="Solo prova positiva IT/EU/EEA/global o ID verificato. Unknown/non-IT restano in coda e non vengono inviati.",
            inline=False,
        )
        embed.add_field(
            name="Coda",
            value=str(len(settings.get("v61_pending_families") or [])),
            inline=True,
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
