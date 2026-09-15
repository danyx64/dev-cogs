from .v62 import QuestTracker


async def setup(bot):
    await bot.add_cog(QuestTracker(bot))
