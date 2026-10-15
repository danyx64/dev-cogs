from redbot.core.bot import Red

from .sticky_compat import AutoRole


async def setup(bot: Red):
    await bot.add_cog(AutoRole(bot))
