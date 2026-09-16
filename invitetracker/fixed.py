from __future__ import annotations

import asyncio
from typing import Any, Optional

import discord
from redbot.core import commands

from .invitetracker import (
    InviteCache,
    InviteTracker as BaseInviteTracker,
    MemberRecord,
    log,
)


_SHARED_STATE_ATTR = "_dany_invite_api_state"


def _shared_state(bot) -> dict[str, Any]:
    state = getattr(bot, _SHARED_STATE_ATTR, None)
    if not isinstance(state, dict):
        state = {
            "locks": {},
            "cache": {},
            "cache_at": {},
            "blocked_until": {},
            "event_cache": {},
            "last_error": {},
        }
        setattr(bot, _SHARED_STATE_ATTR, state)
    return state


async def _shared_guild_invites(
    bot,
    guild: discord.Guild,
    *,
    event_key: Optional[str] = None,
    fresh_for: float = 2.0,
    stale_for: float = 120.0,
) -> Optional[list[discord.Invite]]:
    state = _shared_state(bot)
    loop = asyncio.get_running_loop()
    now = loop.time()
    gid = guild.id

    for key, (created_at, _value) in list(state["event_cache"].items()):
        if now - created_at > 30.0:
            state["event_cache"].pop(key, None)

    event_cache_key = (gid, event_key) if event_key else None
    if event_cache_key is not None:
        hit = state["event_cache"].get(event_cache_key)
        if hit is not None:
            return list(hit[1])

    cached = state["cache"].get(gid)
    cached_at = state["cache_at"].get(gid, 0.0)
    if event_key is None and cached is not None and now - cached_at <= fresh_for:
        return list(cached)

    blocked_until = state["blocked_until"].get(gid, 0.0)
    if blocked_until > now:
        if cached is not None and now - cached_at <= stale_for:
            return list(cached)
        return None

    lock = state["locks"].setdefault(gid, asyncio.Lock())
    async with lock:
        now = loop.time()

        if event_cache_key is not None:
            hit = state["event_cache"].get(event_cache_key)
            if hit is not None:
                return list(hit[1])

        cached = state["cache"].get(gid)
        cached_at = state["cache_at"].get(gid, 0.0)
        if event_key is None and cached is not None and now - cached_at <= fresh_for:
            return list(cached)

        blocked_until = state["blocked_until"].get(gid, 0.0)
        if blocked_until > now:
            if cached is not None and now - cached_at <= stale_for:
                return list(cached)
            return None

        try:
            invites = await guild.invites()
        except discord.Forbidden:
            state["last_error"][gid] = "forbidden"
            state["blocked_until"][gid] = now + 60.0
            return None
        except discord.HTTPException as exc:
            status = int(getattr(exc, "status", 0) or 0)
            if status == 429:
                delay = 90.0
            elif status >= 500:
                delay = 20.0
            else:
                delay = 8.0
            state["last_error"][gid] = f"http-{status or 'error'}"
            state["blocked_until"][gid] = now + delay
            if cached is not None and now - cached_at <= stale_for:
                return list(cached)
            return None

        result = list(invites)
        state["cache"][gid] = result
        state["cache_at"][gid] = now
        state["blocked_until"].pop(gid, None)
        state["last_error"].pop(gid, None)
        if event_cache_key is not None:
            state["event_cache"][event_cache_key] = (now, result)
        return list(result)


class InviteTracker(BaseInviteTracker):
    """InviteTracker with staggered startup and shared invite API throttling."""

    __version__ = "1.2.1-it.2"

    async def _refresh_enabled_guilds(self) -> None:
        # Avoid the startup burst where several cogs all request invites/audit
        # logs at the same time immediately after the gateway becomes ready.
        await self.bot.wait_until_ready()
        await asyncio.sleep(20)

        all_guilds = await self.config.all_guilds()
        for guild_id, settings in all_guilds.items():
            if not settings.get("enabled"):
                continue
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            try:
                await self._refresh_invite_cache(guild)
            except commands.CommandError as exc:
                log.warning(
                    "Cache inviti non aggiornata al boot per il server %s: %s",
                    guild_id,
                    exc,
                )
            except Exception:
                log.exception(
                    "Errore inatteso aggiornando la cache inviti del server %s",
                    guild_id,
                )
            await asyncio.sleep(2)

    async def _fetch_invite_cache(self, guild: discord.Guild) -> InviteCache:
        invites = await _shared_guild_invites(self.bot, guild)
        if invites is None:
            state = _shared_state(self.bot)
            reason = state["last_error"].get(guild.id)
            if reason == "forbidden":
                raise commands.CommandError(
                    "Non posso leggere gli inviti del server. Dammi **Gestisci server** e poi usa `[p]invitetracker refresh`."
                )
            raise commands.CommandError(
                "Discord non ha restituito la lista degli inviti del server; riprovero automaticamente dopo il backoff."
            )
        return {invite.code: self._invite_to_record(invite) for invite in invites}

    async def _fetch_invite_cache_for_join(
        self,
        guild: discord.Guild,
        member_id: int,
    ) -> InviteCache:
        invites = await _shared_guild_invites(
            self.bot,
            guild,
            event_key=f"join:{member_id}",
        )
        if invites is None:
            raise commands.CommandError(
                "Discord non ha restituito la lista degli inviti del server."
            )
        return {invite.code: self._invite_to_record(invite) for invite in invites}

    async def _record_join(self, member: discord.Member) -> None:
        guild = member.guild
        settings = await self.config.guild(guild).all()
        if not settings.get("enabled"):
            return
        if member.bot and not settings.get("include_bots"):
            return

        invite_code: str | None = None
        invite_record: dict[str, Any] | None = None

        async with self._guild_lock(guild.id):
            before_cache = await self.config.guild(guild).invite_cache()
            try:
                after_cache = await self._fetch_invite_cache_for_join(guild, member.id)
            except commands.CommandError:
                await self._increment_unknown_joins(guild)
                log.warning(
                    "Ricerca invito saltata/backoff per un nuovo membro nel server %s",
                    guild.id,
                )
            else:
                invite_code, invite_record = self._find_used_invite(
                    before_cache,
                    after_cache,
                )
                await self.config.guild(guild).invite_cache.set(after_cache)
                if invite_record is None:
                    await self._increment_unknown_joins(guild)

        fake_age_hours = int(settings.get("fake_age_hours") or 0)
        is_fake = self._is_fake_join(member, fake_age_hours)
        inviter_id = invite_record.get("inviter_id") if invite_record else None

        member_record: MemberRecord = {
            "member_id": member.id,
            "inviter_id": inviter_id,
            "invite_code": invite_code,
            "joined_at": self._now_ts(),
            "left_at": None,
            "fake": is_fake,
        }

        async with self.config.guild(guild).members() as members:
            members[str(member.id)] = member_record

        if inviter_id:
            async with self.config.guild(guild).inviters() as inviters:
                stats = self._ensure_stats(inviters, inviter_id)
                stats["joins"] += 1
                if is_fake:
                    stats["fake"] += 1

        await self._send_log(
            guild,
            self._join_embed(member, invite_code, invite_record, is_fake),
        )
