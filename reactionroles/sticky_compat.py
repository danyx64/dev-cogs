"""Keep intentional reaction changes after the initial sticky restoration."""
from .reactionroles import ReactionRoles as BaseReactionRoles


class ReactionRoles(BaseReactionRoles):
    __sticky_roles_compatible__ = True

    async def _member_from_payload(self, payload):
        member = await super()._member_from_payload(payload)
        sticky = self.bot.get_cog("StickyRoles")
        if member is not None and not member.bot and sticky is not None:
            member = await sticky.before_role_change(member)
        return member
