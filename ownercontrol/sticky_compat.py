"""Coordinate only ownerctl's role mutations, preserving its owner-only checks."""
from redbot.core import commands
from .ownercontrol import OwnerControl as BaseOwnerControl


class OwnerControl(BaseOwnerControl):
    __sticky_roles_compatible__ = True

    async def cog_before_invoke(self, ctx):
        parent_hook = getattr(super(), "cog_before_invoke", None)
        if parent_hook is not None:
            await parent_hook(ctx)
        if ctx.command.qualified_name not in {"ownerctl addrole", "ownerctl removerole"}:
            return
        sticky = self.bot.get_cog("StickyRoles")
        if sticky is None:
            return
        # Red has already run checks and converted positional arguments:
        # [cog, ctx, guild_id, member_id, role_id]. Both base methods use
        # atomic add/remove, so they do not depend on gateway cache convergence.
        guild_id, member_id = ctx.args[2:4]
        guild = self.get_guild(guild_id)
        member = guild.get_member(member_id) if guild is not None else None
        if member is not None and await sticky.before_role_change(member) is None:
            raise commands.CheckFailure("Sticky Roles: ripristino non completato; controlla permessi e `.sr utente`.")
