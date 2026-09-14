from .v2 import install_v2_commands
from .v23 import QuestTracker

install_v2_commands()

async def setup(bot):
    await bot.add_cog(QuestTracker(bot))
