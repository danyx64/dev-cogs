import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

import discord

from .serverlogger import AUDIT_WINDOW_SECONDS
from .serverlogger_v4 import ServerLogger as BaseServerLogger


class ServerLogger(BaseServerLogger):
    """ServerLogger v1.8: shared audit snapshots with rate-limit protection."""

    __version__ = "1.8.0"
    AUDIT_SNAPSHOT_SECONDS = 4.0
    AUDIT_STALE_SECONDS = 60.0
    AUDIT_RATE_LIMIT_BACKOFF_SECONDS = 90.0

    def __init__(self, bot):
        super().__init__(bot)
        self._audit_snapshot_cache: Dict[int, Tuple[datetime, List[discord.AuditLogEntry]]] = {}
        self._audit_snapshot_locks: Dict[int, asyncio.Lock] = {}
        self._audit_blocked_until: Dict[int, datetime] = {}

    async def _get_audit_snapshot(self, guild: discord.Guild) -> List[discord.AuditLogEntry]:
        """Fetch audit logs at most once every few seconds per guild.

        Every listener reuses the same snapshot instead of issuing a separate
        GET /audit-logs for each target/action. On 429/5xx we temporarily stop
        querying and fall back to a recent snapshot when available.
        """
        if guild.me is None or not guild.me.guild_permissions.view_audit_log:
            return []

        now = discord.utils.utcnow()
        cached = self._audit_snapshot_cache.get(guild.id)
        if cached:
            age = (now - cached[0]).total_seconds()
            if age <= self.AUDIT_SNAPSHOT_SECONDS:
                return list(cached[1])

        blocked_until = self._audit_blocked_until.get(guild.id)
        if blocked_until is not None and blocked_until > now:
            if cached and (now - cached[0]).total_seconds() <= self.AUDIT_STALE_SECONDS:
                return list(cached[1])
            return []

        lock = self._audit_snapshot_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            now = discord.utils.utcnow()
            cached = self._audit_snapshot_cache.get(guild.id)
            if cached:
                age = (now - cached[0]).total_seconds()
                if age <= self.AUDIT_SNAPSHOT_SECONDS:
                    return list(cached[1])

            blocked_until = self._audit_blocked_until.get(guild.id)
            if blocked_until is not None and blocked_until > now:
                if cached and (now - cached[0]).total_seconds() <= self.AUDIT_STALE_SECONDS:
                    return list(cached[1])
                return []

            entries: List[discord.AuditLogEntry] = []
            try:
                async for entry in guild.audit_logs(limit=16):
                    entries.append(entry)
            except discord.Forbidden:
                self._audit_blocked_until[guild.id] = now + timedelta(seconds=300)
                return list(cached[1]) if cached else []
            except discord.HTTPException as exc:
                status = int(getattr(exc, "status", 0) or 0)
                if status == 429:
                    delay = self.AUDIT_RATE_LIMIT_BACKOFF_SECONDS
                elif status >= 500:
                    delay = 20.0
                else:
                    delay = 8.0
                self._audit_blocked_until[guild.id] = now + timedelta(seconds=delay)
                if cached and (now - cached[0]).total_seconds() <= self.AUDIT_STALE_SECONDS:
                    return list(cached[1])
                return []

            self._audit_snapshot_cache[guild.id] = (now, entries)
            self._audit_blocked_until.pop(guild.id, None)
            return list(entries)

    async def _find_audit_actor(
        self,
        guild,
        action,
        *,
        target_id=None,
        channel_id=None,
    ):
        if guild.me is None or not guild.me.guild_permissions.view_audit_log:
            return None

        now = discord.utils.utcnow()
        cache_key = (guild.id, target_id or channel_id or 0, str(action))
        cached_actor = self._audit_cache.get(cache_key)
        if cached_actor and (now - cached_actor[0]).total_seconds() < 5:
            return guild.get_member(cached_actor[1]) if cached_actor[1] else None

        entries = await self._get_audit_snapshot(guild)
        for entry in entries:
            if entry.action != action:
                continue
            if abs((now - entry.created_at).total_seconds()) > AUDIT_WINDOW_SECONDS:
                continue

            target_entry_id = self._object_id(entry.target)
            if target_id is not None and target_entry_id not in (None, target_id):
                continue

            if channel_id is not None:
                extra_id = self._object_id(
                    getattr(getattr(entry, "extra", None), "channel", None)
                )
                if channel_id not in (extra_id, target_entry_id):
                    continue

            actor_id = self._object_id(entry.user)
            self._audit_cache[cache_key] = (now, actor_id)
            return entry.user

        # Cache a miss briefly too. This prevents several listeners generated by
        # one Discord action from all forcing another audit lookup immediately.
        self._audit_cache[cache_key] = (now, None)
        return None
