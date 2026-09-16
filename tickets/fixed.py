import asyncio

import discord

from .common.models import GuildSettings
from .tickets import Tickets as BaseTickets, log


class Tickets(BaseTickets):
    """Tickets with startup staggering and retries for transient Discord errors."""

    __version__ = "3.5.1-r1"

    async def _startup(self) -> None:
        await self.bot.wait_until_red_ready()
        # Do not hit panel/message endpoints in the same startup burst as all
        # the other cogs restoring their caches and views.
        await asyncio.sleep(12)
        await self.initialize()

    async def _init_guild(self, guild: discord.Guild, conf: GuildSettings) -> None:
        # PanelView.start() and fetch_message() can transiently fail with
        # Discord 5xx/429 during startup. Retry the guild initialization instead
        # of permanently leaving its persistent ticket views unregistered.
        retry_delays = (0, 4, 12)
        last_error = None

        for attempt, delay in enumerate(retry_delays, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                await super()._init_guild(guild, conf)
                return
            except discord.HTTPException as exc:
                last_error = exc
                status = int(getattr(exc, "status", 0) or 0)
                transient = status == 429 or status >= 500
                if not transient or attempt >= len(retry_delays):
                    raise
                log.warning(
                    "Transient Discord HTTP %s while initializing tickets for %s; retry %s/%s",
                    status or "error",
                    guild.name,
                    attempt,
                    len(retry_delays) - 1,
                )

        if last_error is not None:
            raise last_error
