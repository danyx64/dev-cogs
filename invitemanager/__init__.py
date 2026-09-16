from .runtime import InviteManager


async def setup(bot):
    await bot.add_cog(InviteManager(bot))
