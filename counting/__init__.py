from redbot.core.bot import Red

from .counting import Counting


async def setup(bot: Red):
    await bot.add_cog(Counting(bot))
