from typing import Any, Dict, Optional

import discord

from .v42 import ITALY_CODES, ITALY_REGION_CODES, _norm_region
from .v43 import QuestTracker as QuestTrackerV43


class QuestTracker(QuestTrackerV43):
    """QuestTracker 4.4.0: filtro Italia fail-closed e card Quest native di Discord."""

    __version__ = "4.4.0"

    @staticmethod
    def _region_allows_italy(region: Optional[Dict[str, Any]]) -> bool:
        """Accetta solo Quest con disponibilita' Italia esplicitamente confermata.

        A differenza delle versioni precedenti, se i dati regionali mancano o
        sono ambigui la Quest viene scartata. Questo evita falsi positivi quando
        il feed Quest conosce una campagna ma il feed regioni non ne conferma
        la disponibilita' in Italia.
        """
        if not region:
            return False

        include = {_norm_region(value) for value in region.get("include", []) if value}
        exclude = {_norm_region(value) for value in region.get("exclude", []) if value}

        # Un'esclusione esplicita di Italia/Europa ha sempre precedenza.
        if exclude & ITALY_REGION_CODES:
            return False

        # Le campagne dichiarate globali sono disponibili anche in Italia,
        # salvo l'esclusione gestita sopra.
        if region.get("is_global"):
            return True

        # In modalita' stretta accettiamo soltanto allow-list che includano
        # esplicitamente Italia oppure un'area che comprende l'Italia.
        return bool(include & ITALY_REGION_CODES)

    @staticmethod
    def _quest_share_url(entry: Dict[str, Any], config: Dict[str, Any]) -> Optional[str]:
        quest_id = str(
            entry.get("id")
            or config.get("id")
            or config.get("quest_id")
            or ""
        ).strip()
        if not quest_id.isdigit():
            return None
        return f"https://discord.com/quests/{quest_id}"

    async def _send_quest(
        self,
        channel: discord.TextChannel,
        guild: discord.Guild,
        entry: Dict[str, Any],
        config: Dict[str, Any],
        *,
        test: bool = False,
    ) -> None:
        """Invia il link Quest puro, lasciando a Discord la card interattiva nativa."""
        url = self._quest_share_url(entry, config)
        if url is None:
            return

        settings = await self.config.guild(guild).all()
        role = guild.get_role(settings.get("role_id") or 0)

        lines = []
        allow_role = False
        if role is not None and settings.get("ping_role", True):
            lines.append(role.mention)
            allow_role = not test
        lines.append(url)

        kwargs = {
            "content": "\n".join(lines),
            "allowed_mentions": discord.AllowedMentions(
                roles=allow_role,
                users=False,
                everyone=False,
            ),
        }

        # Il test mostra la stessa card e la stessa menzione, ma prova a non
        # generare una notifica push del ruolo.
        if test:
            try:
                await channel.send(**kwargs, silent=True)
                return
            except TypeError:
                pass

        await channel.send(**kwargs)
