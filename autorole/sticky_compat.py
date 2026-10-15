"""Optional Sticky Roles integration; original AutoRole Config is unchanged."""
from redbot.core import commands
from .autorole import AutoRole as BaseAutoRole


class AutoRole(BaseAutoRole):
    __version__ = "1.1.0"
    __sticky_roles_compatible__ = True

    @commands.Cog.listener()
    async def on_member_join(self, member):
        sticky = self.bot.get_cog("StickyRoles")
        if sticky is not None and await sticky.should_skip_autorole(member):
            return
        await super().on_member_join(member)
