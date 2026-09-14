from typing import Any, Dict

import discord

from .v42 import QuestTracker as QuestTrackerV42


QUEST_HOME_URL = "https://discord.com/quest-home"


class QuestTracker(QuestTrackerV42):
    """QuestTracker 4.3.0: il titolo apre direttamente la Quest specifica su Discord."""

    __version__ = "4.3.0"

    def _build_embed(
        self,
        entry: Dict[str, Any],
        config: Dict[str, Any],
        *,
        test: bool = False,
    ) -> discord.Embed:
        embed = super()._build_embed(entry, config, test=test)

        # Discord riconosce i link /quests/<quest_id> come link di condivisione
        # della Quest e, nel client/web app, apre direttamente la relativa
        # scheda/modale da cui l'utente puo accettarla o avviarla.
        quest_id = str(
            entry.get("id")
            or config.get("id")
            or config.get("quest_id")
            or ""
        ).strip()

        if quest_id.isdigit():
            embed.url = f"https://discord.com/quests/{quest_id}"
        else:
            # Fallback sicuro se il feed dovesse fornire una Quest senza ID.
            embed.url = QUEST_HOME_URL

        return embed
