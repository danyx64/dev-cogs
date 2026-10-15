from .stickyroles import StickyRoles


async def setup(bot):
    await bot.add_cog(StickyRoles(bot))
