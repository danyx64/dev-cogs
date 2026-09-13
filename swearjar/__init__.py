from .legacy_commands import install_command_tree, uninstall_command_tree
from .v4 import SwearJar


async def setup(bot):
    cog = SwearJar(bot)
    await bot.add_cog(cog)
    install_command_tree(bot, cog)


async def teardown(bot):
    uninstall_command_tree(bot)
