from redbot.core.bot import Red

from .autorole import AutoRole


async def setup(bot: Red):
    await bot.add_cog(AutoRole(bot))
