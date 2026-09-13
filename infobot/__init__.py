from .infobot import InfoBot


async def setup(bot):
    await bot.add_cog(InfoBot(bot))
