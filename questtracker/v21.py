from typing import Any, Dict

import discord

from .questtracker import SafeFormatDict
from .v2 import QuestTracker as QuestTrackerV2


class QuestTracker(QuestTrackerV2):
    __version__ = "2.0.1"

    def _extended_payload(
        self,
        guild: discord.Guild,
        entry: Dict[str, Any],
        config: Dict[str, Any],
        settings: Dict[str, Any],
        *,
        test: bool = False,
    ) -> SafeFormatDict:
        values = super()._extended_payload(guild, entry, config, settings, test=test)
        values["video_line"] = values.get("video_link", "")
        return values
