from redbot.core import commands

from .command_cog import SwearJarCommands
from .v42 import SwearJar


async def _swear_check(command_cog, ctx, *, text: str):
    """Analizza una frase senza incrementare il contatore."""
    result = await command_cog.swearjar.diagnose_text(ctx.guild, text)
    await ctx.send(result)


_check_command = commands.command(name="check", aliases=["test"])(_swear_check)
if SwearJarCommands.swear.get_command("check") is None:
    SwearJarCommands.swear.add_command(_check_command)


async def setup(bot):
    detector = SwearJar(bot)
    await bot.add_cog(detector)

    # v4+ contengono ancora i vecchi entrypoint prefix. Li rimuoviamo dal
    # Bot e registriamo il Cog statico per `.swear` / `.swearjar`.
    # `/top` rimane invece l'app command del Cog principale.
    for name in ("swear", "swearjar"):
        command = bot.get_command(name)
        if command is not None and command.cog is detector:
            bot.remove_command(name)

    await bot.add_cog(SwearJarCommands(bot, detector))
