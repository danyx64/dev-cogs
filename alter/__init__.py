from .alter import Alter


async def setup(bot):
    await bot.add_cog(Alter(bot))
