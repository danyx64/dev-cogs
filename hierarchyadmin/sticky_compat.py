"""Order manual role grants/removals after restoration; read-only commands stay read-only."""
from .hierarchyadmin_v3 import HierarchyAdmin as BaseHierarchyAdmin


class HierarchyAdmin(BaseHierarchyAdmin):
    __sticky_roles_compatible__ = True

    async def _member(self, ctx, user_id):
        member = await super()._member(ctx, user_id)
        sticky = self.bot.get_cog("StickyRoles")
        name = getattr(ctx.command, "qualified_name", "")
        if member is not None and sticky is not None and name in {"ha give", "ha remove"}:
            member = await sticky.before_role_change(member)
            if member is None:
                await ctx.send("Ripristino non completato: controlla `.sr utente ID_UTENTE` prima di riprovare.")
        return member
