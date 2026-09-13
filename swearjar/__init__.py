from redbot.core import commands

from . import legacy_commands
from .v4 import SwearJar


def _guild_only(command):
    """Applica correttamente il check guild-only anche a Command creati a runtime."""
    commands.guild_only()(command)
    return command


def _admin(command):
    """Applica i requisiti Red admin/manage_guild a Command creati a runtime."""
    commands.admin_or_permissions(manage_guild=True)(command)
    commands.guild_only()(command)
    return command


async def setup(bot):
    cog = SwearJar(bot)
    await bot.add_cog(cog)

    # legacy_commands costruisce i comandi a runtime. I decorator di Red devono
    # essere applicati direttamente al Command; non espongono una `.predicate`.
    legacy_commands._guild_only = _guild_only
    legacy_commands._admin = _admin
    legacy_commands.install_command_tree(bot, cog)


async def teardown(bot):
    legacy_commands.uninstall_command_tree(bot)
