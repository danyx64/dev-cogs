from .v2 import QuestTracker, install_v2_commands


install_v2_commands()


async def setup(bot):
    await bot.add_cog(QuestTracker(bot))
