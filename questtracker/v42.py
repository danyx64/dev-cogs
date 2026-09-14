import asyncio
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

import aiohttp

from .v41 import QuestTracker as QuestTrackerV41


REGIONS_SOURCE = "https://api.discordquest.com/api/regions"
ITALY_CODES = {"IT", "ITA", "ITALY", "ITALIA"}
ITALY_REGION_CODES = ITALY_CODES | {"EU", "EEA", "EUROPE", "EUROPEANUNION"}


def _norm_region(value: Any) -> str:
    return re.sub(r"[^A-Z]", "", str(value or "").upper())


def _norm_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


class QuestTracker(QuestTrackerV41):
    """QuestTracker 4.2.0: filtra le Quest disponibili in Italia e rimuove i duplicati regionali."""

    __version__ = "4.2.0"

    async def _fetch_regions(self) -> Dict[str, Dict[str, Any]]:
        """Carica la mappa regioni pubblica di discordquest.com.

        Formato atteso:
        {"quests": [{"id": "...", "is_global": bool,
                     "regions": {"include": [...], "exclude": [...]}}]}
        """
        if not self.session or self.session.closed:
            return {}

        try:
            async with self.session.get(REGIONS_SOURCE) as response:
                if response.status != 200:
                    return {}
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError):
            return {}

        if not isinstance(payload, dict):
            return {}

        items = payload.get("quests")
        if not isinstance(items, list):
            return {}

        result: Dict[str, Dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            quest_id = str(item.get("id") or "").strip()
            if not quest_id:
                continue

            regions = item.get("regions") if isinstance(item.get("regions"), dict) else {}
            include_raw = regions.get("include") if isinstance(regions, dict) else []
            exclude_raw = regions.get("exclude") if isinstance(regions, dict) else []
            include = [str(value) for value in include_raw] if isinstance(include_raw, list) else []
            exclude = [str(value) for value in exclude_raw] if isinstance(exclude_raw, list) else []

            result[quest_id] = {
                "is_global": bool(item.get("is_global")),
                "include": include,
                "exclude": exclude,
            }
        return result

    async def _fetch_quests(self) -> List[Dict[str, Any]]:
        entries, regions = await asyncio.gather(
            super()._fetch_quests(),
            self._fetch_regions(),
        )

        annotated: List[Dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item = dict(entry)
            quest_id = str(item.get("id") or "")
            item["_italy_region"] = regions.get(quest_id)
            annotated.append(item)
        return annotated

    @staticmethod
    def _region_allows_italy(region: Optional[Dict[str, Any]]) -> bool:
        # Se l'endpoint regioni non conosce la Quest, la trattiamo come globale:
        # meglio non perdere Quest valide in Italia in caso di dati incompleti.
        if not region:
            return True
        if region.get("is_global"):
            return True

        include = {_norm_region(value) for value in region.get("include", []) if value}
        exclude = {_norm_region(value) for value in region.get("exclude", []) if value}

        # Un'esclusione dell'Italia/Europa ha precedenza.
        if exclude & ITALY_REGION_CODES:
            return False

        # Se esiste una allow-list, deve contenere Italia o una regione che la include.
        if include:
            return bool(include & ITALY_REGION_CODES)

        # Nessuna allow-list e Italia non esclusa: disponibile anche in Italia.
        return True

    @staticmethod
    def _italian_text_hint(config: Dict[str, Any]) -> int:
        """Piccolo tie-breaker per feed incompleti: preferisce il testo localizzato italiano."""
        messages = config.get("messages") or {}
        app = config.get("application") or {}
        cta = config.get("cta_config") or {}
        haystack = " ".join(
            str(value)
            for value in (
                *(messages.values() if isinstance(messages, dict) else []),
                app.get("link") if isinstance(app, dict) else "",
                cta.get("link") if isinstance(cta, dict) else "",
            )
            if value is not None
        ).lower()

        hints = (
            "italia",
            "italiano",
            "italiana",
            "dal ",
            " al cinema",
            "nelle sale",
            "biglietti",
            "settembre",
            "ottobre",
            "novembre",
            "dicembre",
            "gennaio",
            "febbraio",
            "marzo",
            "aprile",
            "maggio",
            "giugno",
            "luglio",
            "agosto",
            "/it/",
            "it-it",
            "it_it",
        )
        return sum(1 for hint in hints if hint in haystack)

    def _italy_score(self, entry: Dict[str, Any], config: Dict[str, Any]) -> int:
        region = entry.get("_italy_region")
        score = 0

        if region:
            include = {_norm_region(value) for value in region.get("include", []) if value}
            if include & ITALY_CODES:
                score += 100
            elif include & (ITALY_REGION_CODES - ITALY_CODES):
                score += 80
            elif region.get("is_global"):
                score += 60
            elif not include:
                score += 50
        else:
            score += 40

        score += min(self._italian_text_hint(config), 9)
        return score

    @staticmethod
    def _family_key(entry: Dict[str, Any], config: Dict[str, Any]) -> str:
        """Raggruppa le varianti regionali della stessa Quest in un'unica famiglia."""
        messages = config.get("messages") or {}
        app = config.get("application") or {}
        quest_name = (
            messages.get("quest_name")
            or messages.get("game_title")
            or app.get("name")
            or ""
        )
        app_id = str(app.get("id") or config.get("application_id") or "")
        key = f"{app_id}|{_norm_text(quest_name)}"
        return key if key.strip("|") else str(entry.get("id") or "")

    def _active_quests(self, entries: Iterable[Dict[str, Any]]) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
        # 1) elimina subito le Quest chiaramente non disponibili in Italia;
        # 2) mette davanti le varianti esplicitamente IT, cosi anche il dedupe
        #    ereditato sceglie quella corretta quando i dati sono identici;
        # 3) raggruppa le varianti regionali rimaste e ne conserva una sola.
        italian_entries = [
            entry
            for entry in entries
            if isinstance(entry, dict) and self._region_allows_italy(entry.get("_italy_region"))
        ]

        def preliminary_score(entry: Dict[str, Any]) -> int:
            config = entry.get("config") if isinstance(entry.get("config"), dict) else entry
            return self._italy_score(entry, config)

        italian_entries.sort(key=preliminary_score, reverse=True)
        active = super()._active_quests(italian_entries)

        chosen: Dict[str, Tuple[int, Tuple[str, Dict[str, Any], Dict[str, Any]]]] = {}
        for item in active:
            canonical, entry, config = item
            family = self._family_key(entry, config)
            score = self._italy_score(entry, config)
            previous = chosen.get(family)
            if previous is None or score > previous[0]:
                chosen[family] = (score, item)

        return [item for _, item in chosen.values()]
