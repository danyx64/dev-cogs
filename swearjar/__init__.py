from .v3 import SwearJar


async def setup(bot):
    cog = SwearJar(bot)
    await bot.add_cog(cog)

    # La leaderboard pubblica deve essere solo slash: rimuove il vecchio
    # comando prefix `leadswear`, lasciando invariati gli altri comandi del cog.
    legacy_top = bot.get_command("leadswear")
    if legacy_top is not None and legacy_top.cog is cog:
        bot.remove_command("leadswear")
