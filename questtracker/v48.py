import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from discord.ext import tasks

from .v47 import QuestTracker as QuestTrackerV47


class QuestTracker(QuestTrackerV47):
    """QuestTracker 4.8.0: scansione automatica ogni minuto con retry del feed."""

    __version__ = "4.8.0"
    CACHE_MAX_AGE_SECONDS = 900
    SUSPICIOUS_DROP_RATIO = 0.75

    def __init__(self, bot):
        super().__init__(bot)
        self._last_good_entries: List[Dict[str, Any]] = []
        self._last_good_fetch_at: Optional[datetime] = None

    @staticmethod
    def _merge_samples(samples: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for sample in samples:
            for entry in sample:
                if not isinstance(entry, dict):
                    continue
                quest_id = str(entry.get("id") or "").strip()
                if not quest_id:
                    continue
                old = merged.get(quest_id)
                if old is None or (not old.get("_italy_region") and entry.get("_italy_region")):
                    merged[quest_id] = entry
        return list(merged.values())

    async def _fetch_quests(self) -> List[Dict[str, Any]]:
        previous_count = len(self._last_good_entries)
        samples: List[List[Dict[str, Any]]] = []

        for delay in (0, 2, 5):
            if delay:
                await asyncio.sleep(delay)
            current = await super()._fetch_quests()
            if current:
                samples.append(current)
                merged = self._merge_samples(samples)
                threshold = max(5, int(previous_count * self.SUSPICIOUS_DROP_RATIO))
                if previous_count == 0 or len(merged) >= threshold:
                    break

        merged = self._merge_samples(samples)
        now = datetime.now(timezone.utc)
        if merged:
            self._last_good_entries = list(merged)
            self._last_good_fetch_at = now
            return merged

        if self._last_good_entries and self._last_good_fetch_at:
            age = (now - self._last_good_fetch_at).total_seconds()
            if age <= self.CACHE_MAX_AGE_SECONDS:
                return list(self._last_good_entries)

        return []

    @tasks.loop(seconds=60)
    async def quest_scan(self):
        if self._scan_lock.locked():
            return
        async with self._scan_lock:
            for guild in list(self.bot.guilds):
                try:
                    await self._scan_guild(guild)
                except Exception:
                    continue

    @quest_scan.before_loop
    async def before_quest_scan(self):
        await self.bot.wait_until_red_ready()
        await asyncio.sleep(10)
