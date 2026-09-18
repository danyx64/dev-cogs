from .appautomod import AppAutoMod


async def setup(bot):
    cog = AppAutoMod(bot)
    await bot.add_cog(cog)
