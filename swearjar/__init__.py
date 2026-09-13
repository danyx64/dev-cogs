from redbot.core import commands

from . import legacy_commands
from .leaderboard_commands import install_leaderboard_commands, uninstall_leaderboard_commands
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


def _promote_swear_primary(bot):
    """Rende `swear` il nome reale del gruppo e `swearjar` soltanto un alias.

    Il vecchio facade veniva registrato come `swearjar` con alias `swear`.
    Poiche l'interfaccia principale usata nel server e `.swear`, tenerlo come
    alias dinamico rendeva il command tree piu fragile durante reload/restart.
    """
    root = bot.get_command("swearjar")
    if root is None or not isinstance(root, commands.Group):
        raise RuntimeError("SwearJar: gruppo comandi `swearjar` non registrato.")

    if root.name == "swear":
        return root

    bot.remove_command(root.name)
    root.name = "swear"
    root.aliases = ["swearjar"]
    bot.add_command(root)
    return root


def _validate_command_tree(bot):
    required = ("swear", "swear top", "swear topall")
    missing = [name for name in required if bot.get_command(name) is None]
    if missing:
        raise RuntimeError(
            "SwearJar: command tree incompleto, mancano: " + ", ".join(missing)
        )


async def setup(bot):
    cog = SwearJar(bot)
    await bot.add_cog(cog)

    # legacy_commands costruisce i comandi a runtime. I decorator di Red devono
    # essere applicati direttamente al Command; non espongono una `.predicate`.
    legacy_commands._guild_only = _guild_only
    legacy_commands._admin = _admin
    legacy_commands.install_command_tree(bot, cog)

    # `.swear` e il comando principale; `.swearjar` rimane compatibile come alias.
    _promote_swear_primary(bot)
    install_leaderboard_commands(bot, cog)
    _validate_command_tree(bot)


async def teardown(bot):
    uninstall_leaderboard_commands(bot)

    # Rimuove il nome principale e, insieme ad esso, anche l'alias `swearjar`.
    command = bot.get_command("swear")
    if command is not None and getattr(command, "_swearjar_legacy_facade", False):
        bot.remove_command("swear")
    else:
        legacy_commands.uninstall_command_tree(bot)
