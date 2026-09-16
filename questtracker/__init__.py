from .v82 import QuestTracker


async def setup(bot):
    await bot.add_cog(QuestTracker(bot))
