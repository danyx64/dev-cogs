from __future__ import annotations

import asyncio
from typing import Any, Optional

import discord
from redbot.core import commands

from .invitemanager import InviteManager as BaseInviteManager


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
    """Read guild invites with a bot-wide lock/cache shared by invite cogs.

    `event_key` lets two different cogs handling the same member join reuse the
    exact same Discord response without also reusing it for a different join.
    """

    state = _shared_state(bot)
    loop = asyncio.get_running_loop()
    now = loop.time()
    gid = guild.id

    # Drop old per-event entries so this state cannot grow forever.
    for key, (created_at, _value) in list(state["event_cache"].items()):
        if now - created_at > 30.0:
            state["event_cache"].pop(key, None)

    event_cache_key = (gid, event_key) if event_key else None
    if event_cache_key is not None:
        event_hit = state["event_cache"].get(event_cache_key)
        if event_hit is not None:
            return list(event_hit[1])

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
            event_hit = state["event_cache"].get(event_cache_key)
            if event_hit is not None:
                return list(event_hit[1])

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


class InviteManager(BaseInviteManager):
    """InviteManager with non-blocking startup and shared API throttling."""

    __version__ = "1.6.0"

    async def cog_load(self) -> None:
        # Never wait for red_ready from cog_load: Red is still loading packages
        # here and aborts a package that does not finish within its load timeout.
        self.auto_sync.change_interval(minutes=15)
        if not self.auto_sync.is_running():
            self.auto_sync.start()

    def cog_unload(self) -> None:
        if self.auto_sync.is_running():
            self.auto_sync.cancel()

    async def _sync_guild(self, guild: discord.Guild) -> tuple[int, int, int]:
        """Sync from Discord without duplicating invite API calls across cogs."""
        async with self._sync_lock:
            live_invites = await _shared_guild_invites(self.bot, guild)
            if live_invites is None:
                return 0, 0, 0

            stored = await self.config.guild(guild).invites()
            live_by_code = {invite.code: invite for invite in live_invites}
            imported = 0
            revoked = 0

            for code, invite in live_by_code.items():
                data = stored.get(code)
                if data is None:
                    inviter = invite.inviter
                    data = {
                        "code": code,
                        "url": invite.url,
                        "channel_id": getattr(invite.channel, "id", None),
                        "type": "esterno",
                        "purpose": "Importato automaticamente da Discord",
                        "created_by_id": getattr(inviter, "id", None),
                        "created_by_name": str(inviter) if inviter else "Sconosciuto",
                        "created_at": invite.created_at.isoformat() if invite.created_at else self._now(),
                        "uses": invite.uses or 0,
                        "joined_users": [],
                        "last_used_by_id": None,
                        "last_used_by_name": None,
                        "last_used_at": None,
                        "revoked": False,
                        "permanent": invite.max_age == 0,
                        "managed_by_cog": False,
                    }
                    imported += 1
                else:
                    data.setdefault("joined_users", [])
                    data.pop("assigned_type", None)
                    data.pop("assigned_to", None)
                    data["url"] = invite.url
                    data["channel_id"] = getattr(invite.channel, "id", data.get("channel_id"))
                    data["uses"] = invite.uses or 0
                    data["revoked"] = False
                    data["permanent"] = invite.max_age == 0
                stored[code] = data

            for code, data in stored.items():
                data.setdefault("joined_users", [])
                data.pop("assigned_type", None)
                data.pop("assigned_to", None)
                if code not in live_by_code and not data.get("revoked"):
                    data["revoked"] = True
                    data["revoked_at"] = self._now()
                    data["revoked_by_id"] = None
                    revoked += 1

            await self.config.guild(guild).invites.set(stored)
            self._invite_cache[guild.id] = {
                code: invite.uses or 0 for code, invite in live_by_code.items()
            }
            return len(live_invites), imported, revoked

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild = member.guild
        before = self._invite_cache.get(guild.id, {})
        current_invites = await _shared_guild_invites(
            self.bot,
            guild,
            event_key=f"join:{member.id}",
        )
        if current_invites is None:
            return

        current = {invite.code: invite.uses or 0 for invite in current_invites}
        used = next(
            (
                (invite.code, invite.uses or 0)
                for invite in current_invites
                if (invite.uses or 0) > before.get(invite.code, 0)
            ),
            None,
        )
        self._invite_cache[guild.id] = current
        if not used:
            return

        data = await self._get_record(guild, used[0])
        if not data:
            # Build the missing record from the already fetched response instead
            # of performing another GET /invites.
            invite = next((item for item in current_invites if item.code == used[0]), None)
            if invite is None:
                return
            inviter = invite.inviter
            data = {
                "code": invite.code,
                "url": invite.url,
                "channel_id": getattr(invite.channel, "id", None),
                "type": "esterno",
                "purpose": "Importato automaticamente da Discord",
                "created_by_id": getattr(inviter, "id", None),
                "created_by_name": str(inviter) if inviter else "Sconosciuto",
                "created_at": invite.created_at.isoformat() if invite.created_at else self._now(),
                "uses": invite.uses or 0,
                "joined_users": [],
                "last_used_by_id": None,
                "last_used_by_name": None,
                "last_used_at": None,
                "revoked": False,
                "permanent": invite.max_age == 0,
                "managed_by_cog": False,
            }

        now = self._now()
        data["uses"] = used[1]
        data["last_used_by_id"] = member.id
        data["last_used_by_name"] = str(member)
        data["last_used_at"] = now
        history = data.setdefault("joined_users", [])
        history.append(
            {
                "user_id": member.id,
                "user_name": str(member),
                "joined_at": now,
            }
        )
        await self._save_record(guild, used[0], data)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        guild = invite.guild
        if guild is None:
            return

        data = await self._get_record(guild, invite.code)
        if data is None:
            inviter = invite.inviter
            data = {
                "code": invite.code,
                "url": invite.url,
                "channel_id": getattr(invite.channel, "id", None),
                "type": "esterno",
                "purpose": "Importato automaticamente da Discord",
                "created_by_id": getattr(inviter, "id", None),
                "created_by_name": str(inviter) if inviter else "Sconosciuto",
                "created_at": invite.created_at.isoformat() if invite.created_at else self._now(),
                "uses": invite.uses or 0,
                "joined_users": [],
                "last_used_by_id": None,
                "last_used_by_name": None,
                "last_used_at": None,
                "revoked": False,
                "permanent": invite.max_age == 0,
                "managed_by_cog": False,
            }
        else:
            data["url"] = invite.url
            data["channel_id"] = getattr(invite.channel, "id", data.get("channel_id"))
            data["uses"] = invite.uses or 0
            data["revoked"] = False
            data["permanent"] = invite.max_age == 0

        await self._save_record(guild, invite.code, data)
        self._invite_cache.setdefault(guild.id, {})[invite.code] = invite.uses or 0

        # Update the shared snapshot locally; no API request is necessary.
        state = _shared_state(self.bot)
        cached = state["cache"].get(guild.id)
        if cached is not None:
            by_code = {item.code: item for item in cached}
            by_code[invite.code] = invite
            state["cache"][guild.id] = list(by_code.values())
            state["cache_at"][guild.id] = asyncio.get_running_loop().time()
