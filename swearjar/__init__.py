from .command_cog import SwearJarCommands
from .v4 import SwearJar


async def setup(bot):
    detector = SwearJar(bot)
    await bot.add_cog(detector)

    # v4 contiene ancora i vecchi entrypoint prefix. Li rimuoviamo dal Bot e
    # registriamo un vero Cog statico per `.swear` / `.swearjar`.
    # `/top` rimane invece l'app command del Cog principale.
    for name in ("swear", "swearjar"):
        command = bot.get_command(name)
        if command is not None and command.cog is detector:
            bot.remove_command(name)

    await bot.add_cog(SwearJarCommands(bot, detector))
