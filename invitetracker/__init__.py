from .fixed import InviteTracker


async def setup(bot):
    await bot.add_cog(InviteTracker(bot))
