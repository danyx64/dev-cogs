import discord
from redbot.core import commands


PAGE_SIZE = 20


async def _get_ranking(cog, guild):
    all_members = await cog.config.all_members(guild)
    ranking = sorted(
        [
            (int(uid), int(data.get("count", 0) or 0))
            for uid, data in all_members.items()
            if int(data.get("count", 0) or 0) > 0
        ],
        key=lambda item: (-item[1], item[0]),
    )
    total = sum(count for _, count in ranking)
    return ranking, total


def _allowed_mentions():
    return discord.AllowedMentions(
        users=True,
        roles=False,
        everyone=False,
    )


def _top_embed(ranking, total):
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for position, (uid, count) in enumerate(ranking[:10], 1):
        prefix = medals[position - 1] if position <= 3 else f"**{position}.**"
        lines.append(f"{prefix} <@{uid}> — **{count}**")

    embed = discord.Embed(
        title="🏆 Classifica Swear Jar",
        description="\n".join(lines) if lines else "La leaderboard e ancora vuota.",
        colour=discord.Colour.gold(),
    )
    embed.add_field(
        name="Totale server",
        value=f"**{total}** bestemmie rilevate",
        inline=False,
    )
    embed.set_footer(text="Top 10 del server")
    return embed


def _full_embed(ranking, total, page_index, total_pages):
    start = page_index * PAGE_SIZE
    page_entries = ranking[start:start + PAGE_SIZE]
    medals = ["🥇", "🥈", "🥉"]
    lines = []

    for offset, (uid, count) in enumerate(page_entries):
        position = start + offset + 1
        prefix = medals[position - 1] if position <= 3 else f"**{position}.**"
        lines.append(f"{prefix} <@{uid}> — **{count}**")

    embed = discord.Embed(
        title="🏆 Classifica completa Swear Jar",
        description="\n".join(lines),
        colour=discord.Colour.gold(),
    )
    embed.add_field(
        name="Totale server",
        value=f"**{total}** bestemmie rilevate",
        inline=True,
    )
    embed.add_field(
        name="Membri in classifica",
        value=f"**{len(ranking)}**",
        inline=True,
    )
    embed.set_footer(
        text=f"Pagina {page_index + 1}/{total_pages} • {PAGE_SIZE} utenti per pagina"
    )
    return embed


def install_leaderboard_commands(bot, cog):
    root = bot.get_command("swearjar")
    if not isinstance(root, commands.Group):
        return

    for name in ("top", "topall"):
        existing = root.get_command(name)
        if existing is not None and getattr(existing, "_swearjar_leaderboard_command", False):
            root.remove_command(name)

    async def top_callback(ctx: commands.Context):
        ranking, total = await _get_ranking(cog, ctx.guild)
        await ctx.send(
            embed=_top_embed(ranking, total),
            allowed_mentions=_allowed_mentions(),
        )

    top_command = commands.Command(
        top_callback,
        name="top",
        help="Mostra la top 10 SwearJar del server.",
        brief="Mostra la top 10 del server.",
    )
    top_command._swearjar_leaderboard_command = True
    root.add_command(top_command)

    async def topall_callback(ctx: commands.Context):
        if not await ctx.bot.is_owner(ctx.author):
            raise commands.NotOwner("Questo comando e riservato al bot owner.")

        ranking, total = await _get_ranking(cog, ctx.guild)
        if not ranking:
            return await ctx.send("La leaderboard e ancora vuota.")

        total_pages = (len(ranking) + PAGE_SIZE - 1) // PAGE_SIZE
        for page_index in range(total_pages):
            await ctx.send(
                embed=_full_embed(ranking, total, page_index, total_pages),
                allowed_mentions=_allowed_mentions(),
            )

    topall_command = commands.Command(
        topall_callback,
        name="topall",
        help="Mostra la classifica SwearJar completa. Solo bot owner.",
        brief="Classifica completa riservata al bot owner.",
    )
    topall_command.hidden = True
    topall_command._swearjar_leaderboard_command = True
    root.add_command(topall_command)


def uninstall_leaderboard_commands(bot):
    root = bot.get_command("swearjar")
    if not isinstance(root, commands.Group):
        return

    for name in ("top", "topall"):
        command = root.get_command(name)
        if command is not None and getattr(command, "_swearjar_leaderboard_command", False):
            root.remove_command(name)
