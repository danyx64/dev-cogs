from __future__ import annotations

from typing import Any, Dict, List

from .v80 import _parse_iso, _quest_config, _quest_id, _quest_name, _quest_url
from .v81 import QuestTracker as QuestTrackerV81


class QuestTracker(QuestTrackerV81):
    """QuestTracker v8.1.3: liste cronologiche con orario di pubblicazione."""

    __version__ = "8.1.3"

    async def _all_active_catalog(self) -> List[Dict[str, Any]]:
        """Lista attiva ordinata dal meno recente al piu recente.

        In questo modo le Quest piu nuove finiscono in fondo alla lista.
        """
        entries = await self._fetch_all()
        active = [entry for entry in entries if self._entry_is_active(entry)]
        active.sort(key=self._active_sort_key)
        return active

    @staticmethod
    def _published_text(entry: Dict[str, Any]) -> str:
        """Renderizza l'orario di avvio/pubblicazione esposto dai feed."""
        starts = _parse_iso(_quest_config(entry).get("starts_at"))
        if starts is None:
            return "orario sconosciuto"
        return f"<t:{int(starts.timestamp())}:f>"

    @classmethod
    def _catalog_line(cls, entry: Dict[str, Any], *, show_verdict: bool) -> str:
        qid = _quest_id(entry)
        name = _quest_name(entry).replace("\n", " ")[:90]
        link_name = cls._markdown_link_text(name)
        quest_link = _quest_url(qid)
        aliases = cls._alias_ids(entry)
        alias_note = f" · {len(aliases)} ID uniti" if len(aliases) > 1 else ""
        source_note = cls._source_summary(entry)
        prefix = cls._italy_mark(entry) + " " if show_verdict else "✅ "
        published = cls._published_text(entry)
        return (
            f"{prefix}[{link_name}]({quest_link}) — pubblicata {published} "
            f"— `{qid}` · `{source_note}`{alias_note}"
        )
