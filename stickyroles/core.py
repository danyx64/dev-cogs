"""Persistent role snapshots; no global discord.py or bot event monkey patches."""
from __future__ import annotations

import asyncio
import logging
import time
import weakref
from typing import Any

import discord

log = logging.getLogger("red.danyx64.stickyroles")
MEMBER_DEFAULTS = dict(
    known=False, roles=[], joined="", present=False, returning=False,
    pending=False, retry_needed=False, updated=0.0, last_result={},
)


def role_ids(member) -> list[int]:
    # Keep managed roles in the snapshot for diagnostics, but never assign them.
    return sorted(role.id for role in member.roles if not role.is_default())


def session(member) -> str:
    return member.joined_at.isoformat() if member.joined_at is not None else ""


class RoleMemory:
    """Serialize per member, persist before HTTP, and never save partial restores."""

    def __init__(self, bot, config, enabled, *, delays=(0, 2, 5)):
        self.bot, self.config, self.enabled = bot, config, enabled
        self.delays = delays  # Three bounded passes over seven seconds.
        self._locks = weakref.WeakValueDictionary()
        self._jobs: dict[tuple[int, int], asyncio.Task] = {}
        self._limits: dict[int, asyncio.Semaphore] = {}
        self._closed = False

    def lock(self, guild_id, user_id):
        key = (guild_id, user_id)
        value = self._locks.get(key)
        if value is None:
            value = asyncio.Lock()
            self._locks[key] = value
        return value

    def group(self, guild_id, user_id):
        return self.config.member_from_ids(guild_id, user_id)

    async def read(self, guild_id, user_id):
        return await self.group(guild_id, user_id).all()

    async def write(self, guild_id, user_id, data):
        await self.group(guild_id, user_id).set(data)

    async def active(self, guild):
        return not self._closed and await self.enabled(guild)

    def cancel(self, key):
        task = self._jobs.pop(key, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def close(self):
        self._closed = True
        for key in tuple(self._jobs):
            self.cancel(key)
        # Pending records intentionally remain persistent for the next load.

    async def _prepare_locked(self, member):
        """Called even by AutoRole, so listener scheduling order is irrelevant."""
        data = await self.read(member.guild.id, member.id)
        joined = session(member)
        if not joined:
            return data  # An incomplete member must not overwrite a real snapshot.
        if not data["known"]:
            data.update(known=True, roles=role_ids(member), joined=joined,
                        present=True, returning=False, pending=False, updated=time.time())
        elif data["joined"] != joined or not data["present"]:
            data.update(joined=joined, present=True, returning=True, pending=True, retry_needed=False)
            # Never take roles from the just-joined member here (often @everyone).
        else:
            return data
        await self.write(member.guild.id, member.id, data)
        return data

    async def observe(self, member):
        """Seed a member, start a rejoin, or record a normal live role change."""
        if not await self.active(member.guild):
            return
        async with self.lock(member.guild.id, member.id):
            # Ignore queued events for an older membership or a departed member.
            cached = member.guild.get_member(member.id)
            if cached is None or session(cached) != session(member):
                return
            member = cached
            data = await self._prepare_locked(member)
            if data["pending"]:
                if not data["retry_needed"]:
                    self.schedule(member)
                return
            if data["known"] and session(member):
                data.update(roles=role_ids(member), updated=time.time())
                await self.write(member.guild.id, member.id, data)

    async def joined(self, member):
        if not await self.active(member.guild):
            return
        async with self.lock(member.guild.id, member.id):
            cached = member.guild.get_member(member.id)
            if cached is None or session(cached) != session(member):
                return
            data = await self._prepare_locked(member)
            if data["pending"]:
                self.schedule(member)

    async def should_skip_autorole(self, member) -> bool:
        if not await self.active(member.guild):
            return False
        async with self.lock(member.guild.id, member.id):
            cached = member.guild.get_member(member.id)
            if cached is None or session(cached) != session(member):
                return True  # Stale join listener: never assign to a later membership.
            data = await self._prepare_locked(member)
            if data["pending"] and not data["retry_needed"]:
                self.schedule(member)
            # An empty saved list is still a known returning member.
            return bool(data["known"] and data["returning"])

    async def left(self, guild_id, user_id, member=None):
        guild = self.bot.get_guild(guild_id)
        if guild is None or not await self.active(guild):
            return
        async with self.lock(guild_id, user_id):
            data = await self.read(guild_id, user_id)
            current = guild.get_member(user_id)
            if current is not None:
                # A rapid rejoin may have happened while this event was queued.
                if member is None or session(current) != session(member):
                    return
            if member is not None and data["known"] and data["joined"] != session(member):
                return
            self.cancel((guild_id, user_id))
            # Never overwrite an updated role snapshot with a stale leave event.
            if member is not None and not data["known"]:
                data.update(known=True, roles=role_ids(member), joined=session(member), updated=time.time())
            if data["known"]:
                data["present"] = False
                # pending preserves the ORIGINAL roles if they leave mid-restore.
                await self.write(guild_id, user_id, data)

    def schedule(self, member):
        key = (member.guild.id, member.id)
        if self._closed or key in self._jobs:
            return
        task = asyncio.create_task(self._worker(member.guild, member.id, session(member)))
        self._jobs[key] = task

        def done(finished):
            if self._jobs.get(key) is finished:
                self._jobs.pop(key, None)
            if not finished.cancelled() and finished.exception() is not None:
                exc = finished.exception()
                log.error("StickyRoles job %s failed", key,
                          exc_info=(type(exc), exc, exc.__traceback__))
        task.add_done_callback(done)

    async def _apply_locked(self, guild, user_id, joined, data):
        """Use fresh REST roles. Remove unexpected assignable roles before adding."""
        result: dict[str, Any] = {"added": [], "removed": [], "skipped": [], "errors": []}
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            data["present"] = False
            result["errors"].append("Membro uscito durante il ripristino.")
            return None, result
        except discord.HTTPException as exc:
            result["errors"].append(f"Lettura membro: HTTP {exc.status}")
            return None, result
        if session(member) != joined:
            result["errors"].append("Evento di una precedente permanenza: ignorato.")
            return None, result
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            result["errors"].append("Manca Gestire ruoli al bot.")
            return member, result
        if user_id == me.id or user_id == guild.owner_id:
            result["skipped"].append("Proprietario/bot stesso: sola memorizzazione.")
            return member, result
        wanted = set(data["roles"])
        current = set(role_ids(member))
        for role_id in sorted(current | wanted):
            role = guild.get_role(role_id)
            if role is None:
                result["skipped"].append(f"{role_id}: ruolo eliminato")
            elif role.managed:
                result["skipped"].append(f"{role_id}: gestito da Discord/integrazione")
            elif role >= me.top_role:
                result["skipped"].append(f"{role_id}: sopra o pari al ruolo del bot")
        # Never replace member.roles wholesale: managed roles must be preserved.
        for adding, ids in ((False, current - wanted), (True, wanted - current)):
            for role_id in sorted(ids):
                if not await self.active(guild):
                    result["errors"].append("Sticky Roles disattivato.")
                    return member, result
                role = guild.get_role(role_id)
                if role is None or role.is_default() or role.managed or role >= me.top_role:
                    continue
                try:
                    fn = member.add_roles if adding else member.remove_roles
                    await fn(role, reason="Sticky Roles: ripristino al rientro", atomic=True)
                    result["added" if adding else "removed"].append(role_id)
                except discord.NotFound:
                    result["errors"].append(f"{role_id}: membro o ruolo non piu presente")
                except discord.HTTPException as exc:
                    result["errors"].append(f"{role_id}: HTTP {exc.status}")
        return member, result

    async def _worker(self, guild, user_id, joined):
        limit = self._limits.setdefault(guild.id, asyncio.Semaphore(3))
        for index, delay in enumerate(self.delays):
            await asyncio.sleep(delay)
            if not await self.active(guild):
                return
            async with limit, self.lock(guild.id, user_id):
                data = await self.read(guild.id, user_id)
                if not data["pending"] or not data["present"] or data["joined"] != joined:
                    return
                _, result = await self._apply_locked(guild, user_id, joined, data)
                data["last_result"] = result
                if index == len(self.delays) - 1:
                    data["pending"] = bool(result["errors"])
                    data["retry_needed"] = bool(result["errors"])
                await self.write(guild.id, user_id, data)
                if result["errors"]:
                    log.warning("Ripristino %s/%s: %s", guild.id, user_id, result)
                elif index == len(self.delays) - 1:
                    log.info("Ripristino %s/%s: %s", guild.id, user_id, result)
        # On failures leave pending=True for explicit retry/restart, not an infinite loop.

    async def before_role_change(self, member):
        """Finish the rejoin before an intentional role mutation by a cooperating cog.

        Release the short rejoin guard. The caller receives FRESH roles; normal
        member-update/leave listeners record its subsequent changes. Never keep
        a lock while a moderation cog scans/deletes a member's whole history.
        """
        guild, user_id = member.guild, member.id
        if not await self.active(guild):
            return member
        async with self.lock(guild.id, user_id):
            # Prefer the current REST membership over a stale cached member.
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException as exc:
                log.warning("Role handoff member fetch failed %s/%s: %s", guild.id, user_id, exc)
                return None
            data = await self._prepare_locked(member)
            if data["pending"]:
                self.cancel((guild.id, user_id))
                fresh, result = await self._apply_locked(guild, user_id, session(member), data)
                data["last_result"] = result
                if fresh is None or result["errors"]:
                    # Do not replace a valid historical snapshot with a partial
                    # restore just because another cog requested a role change.
                    data["retry_needed"] = True
                    await self.write(guild.id, user_id, data)
                    log.warning("Azione concorrente rinviata per %s/%s: %s", guild.id, user_id, result)
                    return None
            try:
                fresh = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return None
            if session(fresh) != session(member):
                return None
            data.update(pending=False, retry_needed=False, roles=role_ids(fresh), updated=time.time())
            await self.write(guild.id, user_id, data)
            return fresh

    async def snapshot(self, member, *, force=False):
        if not await self.active(member.guild):
            return False
        async with self.lock(member.guild.id, member.id):
            current = member.guild.get_member(member.id)
            if current is None or session(current) != session(member):
                return False
            if not force:
                member = current
            data = await self._prepare_locked(member)
            if data["pending"] and not force:
                self.schedule(member)
                return False
            if force:
                self.cancel((member.guild.id, member.id))
            data.update(known=True, roles=role_ids(member), joined=session(member),
                        present=True, pending=False, retry_needed=False, updated=time.time())
            await self.write(member.guild.id, member.id, data)
            return True

    async def retry(self, member):
        async with self.lock(member.guild.id, member.id):
            data = await self.read(member.guild.id, member.id)
            if not data["known"]:
                return False
            self.cancel((member.guild.id, member.id))
            data.update(pending=True, retry_needed=False, present=True, joined=session(member), returning=True)
            await self.write(member.guild.id, member.id, data)
            self.schedule(member)
            return True

    async def forget(self, guild_id, user_id):
        async with self.lock(guild_id, user_id):
            self.cancel((guild_id, user_id))
            await self.group(guild_id, user_id).clear()
