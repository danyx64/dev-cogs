from .sticky_compat import OwnerControl


async def setup(bot):
    await bot.add_cog(OwnerControl(bot))
