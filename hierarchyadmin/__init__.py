from .sticky_compat import HierarchyAdmin


async def setup(bot):
    await bot.add_cog(HierarchyAdmin(bot))
