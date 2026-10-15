from .sticky_compat import ReactionRoles


async def setup(bot):
    await bot.add_cog(ReactionRoles(bot))
