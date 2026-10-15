"""Only coordinate concurrent role changes. No quarantine ID list or sticky logic."""
from .quarantena import Quarantena as BaseQuarantena


class Quarantena(BaseQuarantena):
    __sticky_roles_compatible__ = True

    async def isolate(self, member, reason):
        sticky = self.bot.get_cog("StickyRoles")
        if sticky is not None:
            member = await sticky.before_role_change(member)
            if member is None:
                return
        await super().isolate(member, reason)
