import discord
from redbot.core import commands
from redbot.core.bot import Red

from .v4 import DEFAULT_DEITIES, DEFAULT_PROFANITIES, DEFAULT_REPLY, PLACEHOLDER_DESCRIPTIONS


DEITIES_HEADER = (
    "# Una divinita' / riferimento religioso per riga.\n"
    "# Serve sempre anche una voce di parolacce.txt nello stesso messaggio."
)
PROFANITIES_HEADER = (
    "# Una parolaccia / insulto per riga.\n"
    "# Da sola NON fa punteggio: serve sempre anche una voce di divinita.txt."
)
PAGE_SIZE = 20


def _write_dictionary(path, values, header):
    path.write_text(header.rstrip() + "\n" + "\n".join(values) + "\n", encoding="utf-8")


class SwearJarCommands(commands.Cog):
    """Interfaccia prefix statica di SwearJar.

    Questo Cog esiste apposta per evitare gruppi/alias costruiti a runtime:
    `.swear` e' un vero Group Red e `.swearjar` e' il suo alias.
    """

    def __init__(self, bot: Red, swearjar):
        self.bot = bot
        self.swearjar = swearjar

    async def _ranking(self, guild):
        all_members = await self.swearjar.config.all_members(guild)
        ranking = sorted(
            [
                (int(uid), int(data.get("count", 0) or 0))
                for uid, data in all_members.items()
                if int(data.get("count", 0) or 0) > 0
            ],
            key=lambda item: (-item[1], item[0]),
        )
        return ranking, sum(count for _, count in ranking)

    @staticmethod
    def _mentions():
        return discord.AllowedMentions(users=True, roles=False, everyone=False)

    @staticmethod
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
        embed.add_field(name="Totale server", value=f"**{total}** bestemmie rilevate", inline=False)
        embed.set_footer(text="Top 10 del server")
        return embed

    async def _send_menu(self, ctx):
        p = ctx.clean_prefix
        embed = discord.Embed(
            title="SwearJar - Comandi",
            description="Contatore bestemmie e configurazione del server.",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(
            name="Classifica",
            value=(
                "`/top` - top 10 pubblica\n"
                f"`{p}swear top` - stessa top 10\n"
                f"`{p}swear topall` - classifica completa (solo bot owner)"
            ),
            inline=False,
        )
        embed.add_field(
            name="Conteggi e stato",
            value=(
                f"`{p}swear status`\n"
                f"`{p}swear reset <utente|ID>`\n"
                f"`{p}swear set <utente|ID> <numero>`\n"
                f"`{p}swear enable` / `{p}swear disable`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Rilevamento",
            value=(
                f"`{p}swear add <bestemmia>` / `remove` / `list`\n"
                f"`{p}swear deities ...`\n"
                f"`{p}swear profanities ...`\n"
                f"`{p}swear reloadfiles`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Canali e messaggio",
            value=(
                f"`{p}swear channel all`\n"
                f"`{p}swear channel whitelist add <#canale>`\n"
                f"`{p}swear channel blacklist add <#canale>`\n"
                f"`{p}swear message set <testo>`\n"
                f"`{p}swear message placeholders`"
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    async def _resolve_member(self, ctx, target):
        uid = self.swearjar._extract_id(target)
        if uid is not None:
            return uid, ctx.guild.get_member(uid)
        try:
            member = await commands.MemberConverter().convert(ctx, target)
            return member.id, member
        except commands.BadArgument:
            return None, None

    async def _dict_list(self, ctx, path):
        values = self.swearjar._read_dictionary_file(path, [])
        if not values:
            return await ctx.send("Dizionario vuoto.")
        lines = [f"`{i}.` {value}" for i, value in enumerate(values, 1)]
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 1 > 1800:
                await ctx.send(chunk)
                chunk = ""
            chunk += line + "\n"
        if chunk:
            await ctx.send(chunk)

    async def _dict_add(self, ctx, path, header, term):
        term = term.strip()
        if not term:
            return await ctx.send("Termine non valido.")
        values = self.swearjar._read_dictionary_file(path, [])
        normalized = self.swearjar._normalize(term)
        if normalized in {self.swearjar._normalize(x) for x in values}:
            return await ctx.send("Gia presente.")
        values.append(term)
        _write_dictionary(path, values, header)
        await self.swearjar._sync_dictionary_files()
        self.swearjar._term_pattern.cache_clear()
        await ctx.send(f"Aggiunto: `{term}`")

    async def _dict_remove(self, ctx, path, header, term):
        target = self.swearjar._normalize(term)
        values = self.swearjar._read_dictionary_file(path, [])
        new_values = [x for x in values if self.swearjar._normalize(x) != target]
        if len(new_values) == len(values):
            return await ctx.send("Termine non trovato.")
        _write_dictionary(path, new_values, header)
        await self.swearjar._sync_dictionary_files()
        self.swearjar._term_pattern.cache_clear()
        await ctx.send(f"Rimosso: `{term}`")

    @commands.group(name="swear", aliases=["swearjar"], invoke_without_command=True)
    @commands.guild_only()
    async def swear(self, ctx: commands.Context):
        """Mostra e gestisce tutti i comandi SwearJar."""
        await self._send_menu(ctx)

    @swear.command(name="commands", aliases=["comandi", "help"])
    async def swear_commands(self, ctx: commands.Context):
        """Mostra la lista ordinata dei comandi."""
        await self._send_menu(ctx)

    @swear.command(name="top")
    async def swear_top(self, ctx: commands.Context):
        """Mostra la top 10 pubblica del server."""
        ranking, total = await self._ranking(ctx.guild)
        await ctx.send(embed=self._top_embed(ranking, total), allowed_mentions=self._mentions())

    @swear.command(name="topall", hidden=True)
    async def swear_topall(self, ctx: commands.Context):
        """Mostra la classifica completa. Solo bot owner."""
        if not await self.bot.is_owner(ctx.author):
            raise commands.NotOwner("Comando riservato al bot owner.")
        ranking, total = await self._ranking(ctx.guild)
        if not ranking:
            return await ctx.send("La leaderboard e ancora vuota.")
        pages = (len(ranking) + PAGE_SIZE - 1) // PAGE_SIZE
        medals = ["🥇", "🥈", "🥉"]
        for page in range(pages):
            start = page * PAGE_SIZE
            lines = []
            for offset, (uid, count) in enumerate(ranking[start:start + PAGE_SIZE]):
                position = start + offset + 1
                prefix = medals[position - 1] if position <= 3 else f"**{position}.**"
                lines.append(f"{prefix} <@{uid}> — **{count}**")
            embed = discord.Embed(
                title="🏆 Classifica completa Swear Jar",
                description="\n".join(lines),
                colour=discord.Colour.gold(),
            )
            embed.add_field(name="Totale server", value=f"**{total}**", inline=True)
            embed.add_field(name="Membri in classifica", value=f"**{len(ranking)}**", inline=True)
            embed.set_footer(text=f"Pagina {page + 1}/{pages}")
            await ctx.send(embed=embed, allowed_mentions=self._mentions())

    @swear.command(name="status")
    async def swear_status(self, ctx: commands.Context):
        """Mostra lo stato completo di SwearJar."""
        conf = self.swearjar.config.guild(ctx.guild)
        await ctx.send(
            f"Stato: **{'attivo' if await conf.enabled() else 'disattivato'}**\n"
            f"Canali: **{await conf.channel_mode()}** | "
            f"whitelist `{len(await conf.whitelist_channels())}` | "
            f"blacklist `{len(await conf.blacklist_channels())}`\n"
            f"Bestemmie manuali: `{len(await conf.custom_swears())}` | "
            f"totale server: **{await self.swearjar._server_total(ctx.guild)}**"
        )

    @swear.command(name="enable")
    @commands.admin_or_permissions(manage_guild=True)
    async def swear_enable(self, ctx: commands.Context):
        """Abilita il rilevamento."""
        await self.swearjar.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("SwearJar abilitato.")

    @swear.command(name="disable")
    @commands.admin_or_permissions(manage_guild=True)
    async def swear_disable(self, ctx: commands.Context):
        """Disabilita il rilevamento."""
        await self.swearjar.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("SwearJar disabilitato.")

    @swear.command(name="reset", aliases=["resetstats", "resetbestemmie"])
    @commands.admin_or_permissions(manage_guild=True)
    async def swear_reset(self, ctx: commands.Context, *, target: str):
        """Azzera il count di un utente tramite menzione, nome o ID."""
        uid, member = await self._resolve_member(ctx, target)
        if uid is None:
            return await ctx.send("Utente non trovato. Usa menzione, nome o ID Discord.")
        group = self.swearjar.config.member_from_ids(ctx.guild.id, uid)
        previous = await group.count()
        await group.count.set(0)
        total = await self.swearjar._server_total(ctx.guild)
        label = member.mention if member else f"<@{uid}>"
        await ctx.send(
            f"Statistiche di {label} azzerate: **{previous} -> 0**. Totale server: **{total}**.",
            allowed_mentions=self._mentions(),
        )

    @swear.command(name="set", aliases=["fix"])
    @commands.admin_or_permissions(manage_guild=True)
    async def swear_set(self, ctx: commands.Context, target: str, count: int):
        """Imposta manualmente il count di un utente."""
        if count < 0:
            return await ctx.send("Il conteggio non puo essere negativo.")
        uid, member = await self._resolve_member(ctx, target)
        if uid is None:
            return await ctx.send("Utente non trovato. Usa menzione, nome o ID Discord.")
        previous, total = await self.swearjar._set_member_count(ctx.guild, uid, count)
        label = member.mention if member else f"<@{uid}>"
        await ctx.send(
            f"Conteggio di {label}: **{previous} -> {count}**. Totale server: **{total}**.",
            allowed_mentions=self._mentions(),
        )

    @swear.command(name="add")
    @commands.admin_or_permissions(manage_guild=True)
    async def swear_add(self, ctx: commands.Context, *, phrase: str):
        """Aggiunge una bestemmia completa alla lista manuale."""
        phrase = phrase.strip()
        normalized = self.swearjar._normalize(phrase)
        if len(normalized.replace(" ", "")) < 4:
            return await ctx.send("La frase e troppo corta.")
        if len(phrase) > 200:
            return await ctx.send("La frase e troppo lunga (massimo 200 caratteri).")
        async with self.swearjar.config.guild(ctx.guild).custom_swears() as values:
            if normalized in {self.swearjar._normalize(value) for value in values}:
                return await ctx.send("Questa bestemmia e gia presente.")
            values.append(phrase)
        self.swearjar._term_pattern.cache_clear()
        await ctx.send(f"Aggiunta: `{phrase}`")

    @swear.command(name="remove", aliases=["del", "delete"])
    @commands.admin_or_permissions(manage_guild=True)
    async def swear_remove(self, ctx: commands.Context, *, phrase: str):
        """Rimuove una bestemmia completa dalla lista manuale."""
        target = self.swearjar._normalize(phrase)
        async with self.swearjar.config.guild(ctx.guild).custom_swears() as values:
            for existing in list(values):
                if self.swearjar._normalize(existing) == target:
                    values.remove(existing)
                    self.swearjar._term_pattern.cache_clear()
                    return await ctx.send(f"Rimossa: `{existing}`")
        await ctx.send("Bestemmia non trovata nella lista manuale.")

    @swear.command(name="list")
    @commands.admin_or_permissions(manage_guild=True)
    async def swear_list(self, ctx: commands.Context):
        """Mostra le bestemmie complete aggiunte manualmente."""
        values = await self.swearjar.config.guild(ctx.guild).custom_swears()
        if not values:
            return await ctx.send("Nessuna bestemmia manuale configurata.")
        for start in range(0, len(values), 25):
            await ctx.send("\n".join(f"`{i}.` {v}" for i, v in enumerate(values[start:start + 25], start + 1)))

    @swear.group(name="channel", aliases=["channels", "canali"], invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def swear_channel(self, ctx: commands.Context):
        """Configura i canali controllati."""
        conf = self.swearjar.config.guild(ctx.guild)
        await ctx.send(
            f"Modalita: **{await conf.channel_mode()}** | "
            f"whitelist `{len(await conf.whitelist_channels())}` | "
            f"blacklist `{len(await conf.blacklist_channels())}`"
        )

    @swear_channel.command(name="all")
    async def swear_channel_all(self, ctx: commands.Context):
        """Controlla tutti i canali."""
        await self.swearjar.config.guild(ctx.guild).channel_mode.set("all")
        await ctx.send("Modalita canali: **all**.")

    @swear_channel.group(name="whitelist", invoke_without_command=True)
    async def swear_channel_whitelist(self, ctx: commands.Context):
        """Gestisce la whitelist dei canali."""
        conf = self.swearjar.config.guild(ctx.guild)
        await conf.channel_mode.set("whitelist")
        ids = await conf.whitelist_channels()
        text = "\n".join(f"- <#{cid}> (`{cid}`)" for cid in ids) if ids else "Whitelist vuota."
        await ctx.send(text, allowed_mentions=discord.AllowedMentions.none())

    @swear_channel_whitelist.command(name="add")
    async def swear_channel_whitelist_add(self, ctx: commands.Context, channel: str):
        cid = self.swearjar._channel_id_from_argument(channel)
        if cid is None or ctx.guild.get_channel_or_thread(cid) is None:
            return await ctx.send("Canale non valido.")
        conf = self.swearjar.config.guild(ctx.guild)
        await conf.channel_mode.set("whitelist")
        async with conf.whitelist_channels() as values:
            if cid not in values:
                values.append(cid)
        await ctx.send(f"<#{cid}> aggiunto alla whitelist.", allowed_mentions=discord.AllowedMentions.none())

    @swear_channel_whitelist.command(name="remove", aliases=["del"])
    async def swear_channel_whitelist_remove(self, ctx: commands.Context, channel: str):
        cid = self.swearjar._channel_id_from_argument(channel)
        if cid is None:
            return await ctx.send("Canale non valido.")
        async with self.swearjar.config.guild(ctx.guild).whitelist_channels() as values:
            if cid not in values:
                return await ctx.send("Canale non presente nella whitelist.")
            values.remove(cid)
        await ctx.send("Canale rimosso dalla whitelist.")

    @swear_channel_whitelist.command(name="clear")
    async def swear_channel_whitelist_clear(self, ctx: commands.Context):
        await self.swearjar.config.guild(ctx.guild).whitelist_channels.set([])
        await ctx.send("Whitelist svuotata.")

    @swear_channel.group(name="blacklist", invoke_without_command=True)
    async def swear_channel_blacklist(self, ctx: commands.Context):
        """Gestisce la blacklist dei canali."""
        conf = self.swearjar.config.guild(ctx.guild)
        await conf.channel_mode.set("blacklist")
        ids = await conf.blacklist_channels()
        text = "\n".join(f"- <#{cid}> (`{cid}`)" for cid in ids) if ids else "Blacklist vuota."
        await ctx.send(text, allowed_mentions=discord.AllowedMentions.none())

    @swear_channel_blacklist.command(name="add")
    async def swear_channel_blacklist_add(self, ctx: commands.Context, channel: str):
        cid = self.swearjar._channel_id_from_argument(channel)
        if cid is None or ctx.guild.get_channel_or_thread(cid) is None:
            return await ctx.send("Canale non valido.")
        conf = self.swearjar.config.guild(ctx.guild)
        await conf.channel_mode.set("blacklist")
        async with conf.blacklist_channels() as values:
            if cid not in values:
                values.append(cid)
        await ctx.send(f"<#{cid}> aggiunto alla blacklist.", allowed_mentions=discord.AllowedMentions.none())

    @swear_channel_blacklist.command(name="remove", aliases=["del"])
    async def swear_channel_blacklist_remove(self, ctx: commands.Context, channel: str):
        cid = self.swearjar._channel_id_from_argument(channel)
        if cid is None:
            return await ctx.send("Canale non valido.")
        async with self.swearjar.config.guild(ctx.guild).blacklist_channels() as values:
            if cid not in values:
                return await ctx.send("Canale non presente nella blacklist.")
            values.remove(cid)
        await ctx.send("Canale rimosso dalla blacklist.")

    @swear_channel_blacklist.command(name="clear")
    async def swear_channel_blacklist_clear(self, ctx: commands.Context):
        await self.swearjar.config.guild(ctx.guild).blacklist_channels.set([])
        await ctx.send("Blacklist svuotata.")

    @swear.group(name="message", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def swear_message(self, ctx: commands.Context):
        """Configura il messaggio di risposta."""
        current = await self.swearjar.config.guild(ctx.guild).reply_message()
        await ctx.send(f"Messaggio attuale:\n```\n{current}\n```")

    @swear_message.command(name="set")
    async def swear_message_set(self, ctx: commands.Context, *, text: str):
        text = text.strip()
        if not text:
            return await ctx.send("Il messaggio non puo essere vuoto.")
        if len(text) > 2000:
            return await ctx.send("Il messaggio non puo superare 2000 caratteri.")
        error = self.swearjar._validate_template(text)
        if error:
            return await ctx.send(error)
        await self.swearjar.config.guild(ctx.guild).reply_message.set(text)
        await ctx.send("Messaggio aggiornato.")

    @swear_message.command(name="reset")
    async def swear_message_reset(self, ctx: commands.Context):
        await self.swearjar.config.guild(ctx.guild).reply_message.set(DEFAULT_REPLY)
        await ctx.send("Messaggio ripristinato.")

    @swear_message.command(name="placeholders", aliases=["vars", "variabili"])
    async def swear_message_placeholders(self, ctx: commands.Context):
        lines = [f"`{{{name}}}` - {description}" for name, description in PLACEHOLDER_DESCRIPTIONS.items()]
        await ctx.send("**Placeholder disponibili**\n" + "\n".join(lines))

    @swear.command(name="reloadfiles")
    @commands.admin_or_permissions(manage_guild=True)
    async def swear_reloadfiles(self, ctx: commands.Context):
        await self.swearjar._sync_dictionary_files()
        self.swearjar._term_pattern.cache_clear()
        await ctx.send("Dizionari ricaricati da `divinita.txt` e `parolacce.txt`.")

    @swear.group(name="deities", aliases=["divinita"], invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def swear_deities(self, ctx: commands.Context):
        """Gestisce il dizionario delle divinita."""
        await self._dict_list(ctx, self.swearjar.deities_file)

    @swear_deities.command(name="list")
    async def swear_deities_list(self, ctx: commands.Context):
        await self._dict_list(ctx, self.swearjar.deities_file)

    @swear_deities.command(name="add")
    async def swear_deities_add(self, ctx: commands.Context, *, term: str):
        await self._dict_add(ctx, self.swearjar.deities_file, DEITIES_HEADER, term)

    @swear_deities.command(name="remove", aliases=["del", "delete"])
    async def swear_deities_remove(self, ctx: commands.Context, *, term: str):
        await self._dict_remove(ctx, self.swearjar.deities_file, DEITIES_HEADER, term)

    @swear_deities.command(name="reset")
    async def swear_deities_reset(self, ctx: commands.Context):
        _write_dictionary(self.swearjar.deities_file, DEFAULT_DEITIES, DEITIES_HEADER)
        await self.swearjar._sync_dictionary_files()
        self.swearjar._term_pattern.cache_clear()
        await ctx.send("Dizionario divinita ripristinato.")

    @swear.group(name="profanities", aliases=["words", "parole", "parolacce"], invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def swear_profanities(self, ctx: commands.Context):
        """Gestisce il dizionario dei termini offensivi."""
        await self._dict_list(ctx, self.swearjar.profanities_file)

    @swear_profanities.command(name="list")
    async def swear_profanities_list(self, ctx: commands.Context):
        await self._dict_list(ctx, self.swearjar.profanities_file)

    @swear_profanities.command(name="add")
    async def swear_profanities_add(self, ctx: commands.Context, *, term: str):
        await self._dict_add(ctx, self.swearjar.profanities_file, PROFANITIES_HEADER, term)

    @swear_profanities.command(name="remove", aliases=["del", "delete"])
    async def swear_profanities_remove(self, ctx: commands.Context, *, term: str):
        await self._dict_remove(ctx, self.swearjar.profanities_file, PROFANITIES_HEADER, term)

    @swear_profanities.command(name="reset")
    async def swear_profanities_reset(self, ctx: commands.Context):
        _write_dictionary(self.swearjar.profanities_file, DEFAULT_PROFANITIES, PROFANITIES_HEADER)
        await self.swearjar._sync_dictionary_files()
        self.swearjar._term_pattern.cache_clear()
        await ctx.send("Dizionario termini offensivi ripristinato.")
