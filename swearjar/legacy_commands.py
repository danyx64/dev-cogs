import re
from typing import Optional, Tuple

import discord
from redbot.core import commands


DEITIES_HEADER = (
    "# Una divinita' / riferimento religioso per riga.\n"
    "# Serve sempre anche una voce di parolacce.txt nello stesso messaggio."
)
PROFANITIES_HEADER = (
    "# Una parolaccia / insulto per riga.\n"
    "# Da sola NON fa punteggio: serve sempre anche una voce di divinita.txt."
)


def _guild_only(command):
    command.guild_only = True
    return command


def _admin(command):
    command.checks.append(commands.has_permissions(manage_guild=True).predicate)
    command.guild_only = True
    return command


def _write_dictionary(path, values, header):
    path.write_text(header.rstrip() + "\n" + "\n".join(values) + "\n", encoding="utf-8")


async def _resolve_target(ctx: commands.Context, target: str) -> Tuple[Optional[int], Optional[discord.Member]]:
    value = target.strip()
    match = re.fullmatch(r"<@!?(\d{15,25})>", value)
    if match:
        uid = int(match.group(1))
        return uid, ctx.guild.get_member(uid)
    if value.isdigit() and 15 <= len(value) <= 25:
        uid = int(value)
        return uid, ctx.guild.get_member(uid)
    try:
        member = await commands.MemberConverter().convert(ctx, value)
        return member.id, member
    except commands.BadArgument:
        return None, None


async def _target_label(cog, uid: int, member: Optional[discord.Member]) -> str:
    if member is not None:
        return member.mention
    try:
        user = cog.bot.get_user(uid) or await cog.bot.fetch_user(uid)
        return f"{user} (`{uid}`)"
    except (discord.NotFound, discord.HTTPException):
        return f"utente `{uid}`"


def uninstall_command_tree(bot):
    command = bot.get_command("swearjar")
    if command is not None and getattr(command, "_swearjar_legacy_facade", False):
        bot.remove_command("swearjar")


def install_command_tree(bot, cog):
    """Ripristina .swearjar/.swear come gruppo storico, mantenendo le scorciatoie v4."""
    bot.remove_command("swearjar")
    old_swear = bot.remove_command("swear")

    async def root_callback(ctx: commands.Context):
        await ctx.send_help(ctx.command)

    root = commands.Group(
        root_callback,
        name="swearjar",
        aliases=["swear"],
        invoke_without_command=True,
        help="Mostra l'aiuto e configura SwearJar.",
        brief="Gestisce conteggi, canali, dizionari e messaggi.",
    )
    _guild_only(root)
    root._swearjar_legacy_facade = True

    # Mantiene i nuovi .swear add/remove/list/set gia implementati in v4.
    if old_swear is not None:
        for child in list(old_swear.commands):
            old_swear.remove_command(child.name)
            root.add_command(child)

    async def commands_callback(ctx: commands.Context):
        p = ctx.clean_prefix
        await ctx.send(
            "**Comandi SwearJar**\n"
            f"`{p}swear status` - stato completo.\n"
            f"`{p}swear enable` / `{p}swear disable` - attiva/disattiva.\n"
            f"`{p}swear reset <utente|ID>` - azzera il count di un membro.\n"
            f"`{p}swear set <ID> <numero>` - corregge manualmente un count.\n"
            f"`{p}swear add <bestemmia>` - aggiunge una bestemmia completa.\n"
            f"`{p}swear remove <bestemmia>` / `{p}swear list`.\n"
            f"`{p}swear message ...` - configura la risposta.\n"
            f"`{p}swear channels ...` - all/whitelist/blacklist.\n"
            f"`{p}swear deities ...` - dizionario divinita.\n"
            f"`{p}swear profanities ...` - dizionario termini offensivi.\n"
            f"`{p}swear reloadfiles` - ricarica i file dizionario.\n"
            "`/top` - top 10 con menzioni + totale server.\n\n"
            f"Scorciatoie: `{p}reset`, `{p}setcount`, `{p}enable`, `{p}disable`, `{p}channel`, `{p}message`."
        )

    cmd = commands.Command(commands_callback, name="commands", aliases=["comandi", "help"], help="Mostra tutti i comandi SwearJar con esempi.", brief="Mostra la lista dei comandi.")
    _guild_only(cmd)
    root.add_command(cmd)

    async def status_callback(ctx: commands.Context):
        conf = cog.config.guild(ctx.guild)
        enabled = await conf.enabled()
        mode = await conf.channel_mode()
        whitelist = await conf.whitelist_channels()
        blacklist = await conf.blacklist_channels()
        custom = await conf.custom_swears()
        total = await cog._server_total(ctx.guild)
        await ctx.send(
            f"Stato: **{'attivo' if enabled else 'disattivato'}**\n"
            f"Canali: **{mode}** | whitelist `{len(whitelist)}` | blacklist `{len(blacklist)}`\n"
            f"Bestemmie manuali: `{len(custom)}` | totale server: **{total}**"
        )

    cmd = commands.Command(status_callback, name="status", help="Mostra stato, canali e totale server.", brief="Mostra lo stato di SwearJar.")
    _guild_only(cmd)
    root.add_command(cmd)

    async def enable_callback(ctx: commands.Context):
        await cog.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("SwearJar abilitato.")

    cmd = commands.Command(enable_callback, name="enable", help="Abilita il rilevamento SwearJar.")
    _admin(cmd)
    root.add_command(cmd)

    async def disable_callback(ctx: commands.Context):
        await cog.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("SwearJar disabilitato.")

    cmd = commands.Command(disable_callback, name="disable", help="Disabilita il rilevamento SwearJar.")
    _admin(cmd)
    root.add_command(cmd)

    async def reset_callback(ctx: commands.Context, *, target: str):
        uid, member = await _resolve_target(ctx, target)
        if uid is None:
            return await ctx.send("Utente non trovato. Usa menzione, nome o ID Discord.")
        group = cog.config.member_from_ids(ctx.guild.id, uid)
        previous = await group.count()
        await group.count.set(0)
        total = await cog._server_total(ctx.guild)
        label = await _target_label(cog, uid, member)
        await ctx.send(
            f"Statistiche di {label} azzerate: **{previous} -> 0**. Totale server: **{total}**.",
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )

    cmd = commands.Command(reset_callback, name="reset", aliases=["resetstats", "resetbestemmie"], help="Azzera il count di un membro; accetta menzione, nome o ID.", brief="Azzera il count di un membro.")
    _admin(cmd)
    root.add_command(cmd)

    async def reload_callback(ctx: commands.Context):
        await cog._sync_dictionary_files()
        cog._term_pattern.cache_clear()
        await ctx.send("Dizionari ricaricati da `divinita.txt` e `parolacce.txt`.")

    cmd = commands.Command(reload_callback, name="reloadfiles", help="Ricarica i dizionari dai file del cog.")
    _admin(cmd)
    root.add_command(cmd)

    # .swear message ... usa la stessa logica dei comandi top-level .message ...
    async def message_root(ctx: commands.Context):
        current = await cog.config.guild(ctx.guild).reply_message()
        await ctx.send(f"Messaggio attuale:\n```\n{current}\n```")
        await ctx.send_help(ctx.command)

    message_group = commands.Group(message_root, name="message", invoke_without_command=True, help="Gestisce il messaggio di risposta.", brief="Configura il messaggio di risposta.")
    _admin(message_group)

    async def message_set(ctx: commands.Context, *, text: str):
        await cog.message_set.callback(cog, ctx, text=text)

    cmd = commands.Command(message_set, name="set", help="Imposta il messaggio di risposta con placeholder.")
    _admin(cmd)
    message_group.add_command(cmd)

    async def message_reset(ctx: commands.Context):
        await cog.message_reset.callback(cog, ctx)

    cmd = commands.Command(message_reset, name="reset", help="Ripristina il messaggio predefinito.")
    _admin(cmd)
    message_group.add_command(cmd)

    async def message_placeholders(ctx: commands.Context):
        await cog.message_placeholders.callback(cog, ctx)

    cmd = commands.Command(message_placeholders, name="placeholders", aliases=["usage", "vars", "variabili"], help="Mostra i placeholder disponibili.")
    _admin(cmd)
    message_group.add_command(cmd)
    root.add_command(message_group)

    # .swear channels ...: vecchia voce help + nuova struttura all/whitelist/blacklist.
    async def channels_root(ctx: commands.Context):
        conf = cog.config.guild(ctx.guild)
        await ctx.send(
            f"Modalita: **{await conf.channel_mode()}** | whitelist `{len(await conf.whitelist_channels())}` | blacklist `{len(await conf.blacklist_channels())}`"
        )
        await ctx.send_help(ctx.command)

    channels_group = commands.Group(channels_root, name="channels", aliases=["channel", "canali"], invoke_without_command=True, help="Configura i canali controllati.", brief="Configura all/whitelist/blacklist.")
    _admin(channels_group)

    async def channel_all(ctx: commands.Context):
        await cog.channel_all.callback(cog, ctx)

    cmd = commands.Command(channel_all, name="all", help="Controlla tutti i canali.")
    _admin(cmd)
    channels_group.add_command(cmd)

    async def channel_mode(ctx: commands.Context, mode: str):
        mapped = {"include": "whitelist", "exclude": "blacklist"}.get(mode.casefold(), mode.casefold())
        if mapped not in {"all", "whitelist", "blacklist"}:
            return await ctx.send("Modalita valide: `all`, `whitelist`, `blacklist`.")
        await cog.config.guild(ctx.guild).channel_mode.set(mapped)
        await ctx.send(f"Modalita canali impostata su **{mapped}**.")

    cmd = commands.Command(channel_mode, name="mode", help="Imposta all/whitelist/blacklist; include/exclude restano compatibili.")
    _admin(cmd)
    channels_group.add_command(cmd)

    def add_channel_list_group(name: str, config_key: str):
        async def list_root(ctx: commands.Context):
            await cog.config.guild(ctx.guild).channel_mode.set(name)
            ids = await getattr(cog.config.guild(ctx.guild), config_key)()
            text = "\n".join(f"- <#{cid}> (`{cid}`)" for cid in ids) if ids else f"{name.title()} vuota."
            await ctx.send(text, allowed_mentions=discord.AllowedMentions.none())
            await ctx.send_help(ctx.command)

        subgroup = commands.Group(list_root, name=name, invoke_without_command=True, help=f"Gestisce la {name} dei canali.")
        _admin(subgroup)

        async def add_callback(ctx: commands.Context, channel: str):
            method = cog.channel_whitelist_add if name == "whitelist" else cog.channel_blacklist_add
            await method.callback(cog, ctx, channel)

        cmd = commands.Command(add_callback, name="add", help=f"Aggiunge un canale alla {name}.")
        _admin(cmd)
        subgroup.add_command(cmd)

        async def remove_callback(ctx: commands.Context, channel: str):
            method = cog.channel_whitelist_remove if name == "whitelist" else cog.channel_blacklist_remove
            await method.callback(cog, ctx, channel)

        cmd = commands.Command(remove_callback, name="remove", help=f"Rimuove un canale dalla {name}.")
        _admin(cmd)
        subgroup.add_command(cmd)

        async def clear_callback(ctx: commands.Context):
            method = cog.channel_whitelist_clear if name == "whitelist" else cog.channel_blacklist_clear
            await method.callback(cog, ctx)

        cmd = commands.Command(clear_callback, name="clear", help=f"Svuota la {name}.")
        _admin(cmd)
        subgroup.add_command(cmd)
        channels_group.add_command(subgroup)

    add_channel_list_group("whitelist", "whitelist_channels")
    add_channel_list_group("blacklist", "blacklist_channels")
    root.add_command(channels_group)

    # Vecchi dizionari, ripristinati come nel cog storico.
    def add_dictionary_group(name, aliases, path, header, fallback_name, label):
        async def dict_root(ctx: commands.Context):
            await ctx.send_help(ctx.command)

        group = commands.Group(dict_root, name=name, aliases=aliases, invoke_without_command=True, help=f"Gestisce il dizionario {label}.", brief=f"Gestisce {label}.")
        _admin(group)

        async def list_callback(ctx: commands.Context):
            values = cog._read_dictionary_file(path, [])
            if not values:
                return await ctx.send("Dizionario vuoto.")
            text = "\n".join(f"`{i}.` {value}" for i, value in enumerate(values, 1))
            for start in range(0, len(text), 1800):
                await ctx.send(text[start:start + 1800])

        cmd = commands.Command(list_callback, name="list", help=f"Mostra {label} configurati.")
        _admin(cmd)
        group.add_command(cmd)

        async def add_callback(ctx: commands.Context, *, term: str):
            term = term.strip()
            values = cog._read_dictionary_file(path, [])
            if cog._normalize(term) in {cog._normalize(x) for x in values}:
                return await ctx.send("Gia presente.")
            values.append(term)
            _write_dictionary(path, values, header)
            await cog._sync_dictionary_files()
            cog._term_pattern.cache_clear()
            await ctx.send(f"Aggiunto: `{term}`")

        cmd = commands.Command(add_callback, name="add", help=f"Aggiunge un termine a {label}.")
        _admin(cmd)
        group.add_command(cmd)

        async def remove_callback(ctx: commands.Context, *, term: str):
            target = cog._normalize(term)
            values = cog._read_dictionary_file(path, [])
            new_values = [x for x in values if cog._normalize(x) != target]
            if len(new_values) == len(values):
                return await ctx.send("Termine non trovato.")
            _write_dictionary(path, new_values, header)
            await cog._sync_dictionary_files()
            cog._term_pattern.cache_clear()
            await ctx.send(f"Rimosso: `{term}`")

        cmd = commands.Command(remove_callback, name="remove", aliases=["del", "delete"], help=f"Rimuove un termine da {label}.")
        _admin(cmd)
        group.add_command(cmd)

        async def reset_callback(ctx: commands.Context):
            from . import v4
            fallback = list(getattr(v4, fallback_name))
            _write_dictionary(path, fallback, header)
            await cog._sync_dictionary_files()
            cog._term_pattern.cache_clear()
            await ctx.send("Dizionario ripristinato.")

        cmd = commands.Command(reset_callback, name="reset", help=f"Ripristina il dizionario {label}.")
        _admin(cmd)
        group.add_command(cmd)
        root.add_command(group)

    add_dictionary_group("deities", ["divinita"], cog.deities_file, DEITIES_HEADER, "DEFAULT_DEITIES", "delle divinita")
    add_dictionary_group("profanities", ["words", "parole", "parolacce"], cog.profanities_file, PROFANITIES_HEADER, "DEFAULT_PROFANITIES", "dei termini offensivi")

    bot.add_command(root)
    return root
