from .adaptiveslowmode import AdaptiveSlowmode


async def setup(bot):
    await bot.add_cog(AdaptiveSlowmode(bot))
