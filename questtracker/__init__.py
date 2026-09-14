from .v41 import QuestTracker


async def setup(bot):
    await bot.add_cog(QuestTracker(bot))
