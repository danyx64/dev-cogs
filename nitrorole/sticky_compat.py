"""Coordinate boost lifecycle roles without treating them as new-member autoroles."""
from .nitrorole import NitroRole as BaseNitroRole


class NitroRole(BaseNitroRole):
    __sticky_roles_compatible__ = True

    async def maybe_assign_role(self, guild_data, member):
        sticky = self.bot.get_cog("StickyRoles")
        if sticky is not None:
            member = await sticky.before_role_change(member)
            if member is None:
                return
        await super().maybe_assign_role(guild_data, member)

    async def maybe_unassign_role(self, guild_data, member):
        sticky = self.bot.get_cog("StickyRoles")
        if sticky is not None:
            member = await sticky.before_role_change(member)
            if member is None:
                return
        await super().maybe_unassign_role(guild_data, member)
