from .sticky_compat import Quarantena


async def setup(bot):
    await bot.add_cog(Quarantena(bot))
