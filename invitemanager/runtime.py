from .fixed import InviteManager as FixedInviteManager


class InviteManager(FixedInviteManager):
    """Runtime wrapper preserving the historical five-minute sync interval."""

    __version__ = "1.6.1"

    async def cog_load(self) -> None:
        # The inherited loop already has @tasks.loop(minutes=5). We only avoid
        # the old blocking wait_until_red_ready() from the legacy cog_load.
        if not self.auto_sync.is_running():
            self.auto_sync.start()
