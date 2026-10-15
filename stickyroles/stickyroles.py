"""Sticky Roles for Red-DiscordBot: .sr, independent of Quarantena."""
from __future__ import annotations

import asyncio
import io
import json
import logging

import discord
from redbot.core import Config, commands
from redbot.core.utils.chat_formatting import pagify

from .core import MEMBER_DEFAULTS, RoleMemory

log = logging.getLogger("red.danyx64.stickyroles")


class StickyRoles(commands.Cog):
    """Memorizza i ruoli dei membri e li ripristina al rientro."""

    __version__ = "1.0.3"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8467192531060117, force_registration=True)
        self.config.register_guild(enabled=False)
        self.config.register_member(**MEMBER_DEFAULTS)
        self.memory = RoleMemory(bot, self.config, self._enabled)
        self._startup = None
        self._seed_locks = {}

    async def _enabled(self, guild):
        return (
            self.bot.intents.members
            and await self.config.guild(guild).enabled()
            and not await self.bot.cog_disabled_in_guild(self, guild)
        )

    async def cog_load(self):
        self._startup = asyncio.create_task(self._initialize())

    def cog_unload(self):
        self.memory.close()
        if self._startup is not None:
            self._startup.cancel()

    async def _initialize(self):
        try:
            await self.bot.wait_until_red_ready()
            for guild in tuple(self.bot.guilds):
                if await self._enabled(guild):
                    try:
                        await self.seed(guild, resume=True)
                    except discord.HTTPException:
                        log.exception("Impossibile inizializzare i membri di %s", guild.id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Inizializzazione Sticky Roles fallita; usare .sr salva")

    async def seed(self, guild, *, resume=False):
        """Request the full member list, not just a potentially partial cache."""
        async with self._seed_locks.setdefault(guild.id, asyncio.Lock()):
            await guild.chunk(cache=True)
            saved = 0
            members = list(guild.members)
            for member in members:
                if not await self._enabled(guild):
                    break
                if resume:
                    data = await self.memory.read(guild.id, member.id)
                    if data["pending"]:
                        await self.memory.joined(member)
                saved += int(await self.memory.snapshot(member))
                await asyncio.sleep(0)
            return saved, len(members)

    async def should_skip_autorole(self, member):
        """AutoRole integration: True only for a known returning membership."""
        return await self.memory.should_skip_autorole(member)

    async def before_role_change(self, member):
        return await self.memory.before_role_change(member)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        await self.memory.joined(member)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.roles != after.roles:
            await self.memory.observe(after)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        await self.memory.left(member.guild.id, member.id, member)

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload):
        # No roles in a raw remove payload: keep the saved snapshot, never [] it.
        await self.memory.left(payload.guild_id, payload.user.id)

    async def red_delete_data_for_user(self, *, requester, user_id):
        for guild_id in await self.config.all_members():
            await self.memory.forget(guild_id, user_id)

    async def red_get_data_for_user(self, *, user_id):
        result = {}
        for guild_id, members in (await self.config.all_members()).items():
            if user_id in members:
                result[str(guild_id)] = members[user_id]
        if not result:
            return {}
        return {"stickyroles.json": io.BytesIO(json.dumps(result, indent=2).encode("utf-8"))}

    async def _say(self, ctx, text):
        for page in pagify(text, page_length=1900):
            await ctx.send(page, allowed_mentions=discord.AllowedMentions.none())

    @commands.group(name="sr", aliases=["stickyroles", "stikyroles"], invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def sr(self, ctx):
        """Sticky Roles: attiva, disattiva, salva, utente, ripristina, dimentica, conflitti, stato."""
        await ctx.send_help(ctx.command)

    @sr.command(name="attiva", aliases=["on", "enable"])
    async def enable(self, ctx):
        """Attiva e salva subito i ruoli di tutti i membri presenti."""
        if not self.bot.intents.members:
            return await self._say(ctx, "Abilita Server Members Intent nel bot e nel Developer Portal.")
        if not ctx.guild.me.guild_permissions.manage_roles:
            return await self._say(ctx, "Al bot manca Gestire ruoli.")
        was_enabled = await self._enabled(ctx.guild)
        await self.config.guild(ctx.guild).enabled.set(True)
        try:
            processed, total = await self.seed(ctx.guild, resume=True)
        except (discord.HTTPException, asyncio.TimeoutError) as exc:
            log.warning("Scansione Sticky Roles incompleta in %s: %s", ctx.guild.id, exc)
            return await self._say(ctx, "Attivo, ma scansione incompleta. Riprova con `.sr salva`.")
        saved_members = await self.config.all_members(ctx.guild)
        known = sum(1 for d in saved_members.values() if d.get("known"))
        pending = sum(1 for d in saved_members.values() if d.get("known") and d.get("pending"))
        await self._say(
            ctx,
            f"Sticky Roles {'gia attivo: scansione aggiornata' if was_enabled else 'attivato'}.\n"
            f"Membri presenti rilevati: {total} | Copie totali salvate: {known}\n"
            f"Copie aggiornate in questa scansione: {processed} | Ripristini pendenti: {pending}.\n"
            "Zero aggiornamenti alla seconda scansione e normale: le copie esistenti restano salvate. "
            "Controlla `.sr stato` e `.sr conflitti`."
        )

    @sr.command(name="disattiva", aliases=["off", "disable"])
    async def disable(self, ctx):
        """Ferma salvataggi e ripristini senza cancellare le copie dei membri usciti."""
        await self.config.guild(ctx.guild).enabled.set(False)
        for user_id in await self.config.all_members(ctx.guild):
            async with self.memory.lock(ctx.guild.id, user_id):
                self.memory.cancel((ctx.guild.id, user_id))
                data = await self.memory.read(ctx.guild.id, user_id)
                data.update(pending=False, retry_needed=False)
                await self.memory.write(ctx.guild.id, user_id, data)
        await self._say(ctx, "Sticky Roles disattivato. Ruoli attuali e copie salvate non rimossi.")

    @sr.command(name="salva", aliases=["save"])
    async def save(self, ctx, member: discord.Member = None):
        """Senza utente scansiona tutti. Con @utente accetta subito i suoi ruoli attuali."""
        if not await self._enabled(ctx.guild):
            return await self._say(ctx, "Attiva prima Sticky Roles con `.sr attiva`.")
        if member is not None:
            fresh = await ctx.guild.fetch_member(member.id)
            await self.memory.snapshot(fresh, force=True)
            return await self._say(ctx, f"Salvati i ruoli attuali di {member.id}; eventuale ripristino precedente annullato.")
        try:
            saved, total = await self.seed(ctx.guild)
        except (discord.HTTPException, asyncio.TimeoutError):
            return await self._say(ctx, "Scansione incompleta: riprova. Le copie esistenti non sono cancellate.")
        records = await self.config.all_members(ctx.guild)
        known = sum(1 for d in records.values() if d.get("known"))
        await self._say(ctx, f"Scansione completata: {total} membri rilevati, "
                        f"{saved} copie aggiornate, {known} copie totali memorizzate. "
                        "Zero aggiornamenti non significa zero salvataggi. "
                        "Le copie degli utenti usciti vengono conservate.")

    @sr.command(name="utente", aliases=["user"])
    async def user(self, ctx, user_id: int):
        """Mostra i ruoli salvati per un ID, anche se non e nel server."""
        data = await self.memory.read(ctx.guild.id, user_id)
        if not data["known"]:
            return await self._say(ctx, "Nessun salvataggio per questo ID.")
        roles = [f"{ctx.guild.get_role(r).name if ctx.guild.get_role(r) else 'eliminato'} ({r})" for r in data["roles"]]
        await self._say(ctx, f"Utente {user_id}\nRuoli: " + (", ".join(roles) or "solo @everyone") +
                        f"\nPresente: {data['present']} | Rientro: {data['returning']} | In ripristino: {data['pending']}\n"
                        f"Ultimo risultato: {json.dumps(data['last_result'], ensure_ascii=False)}")

    @sr.command(name="ripristina", aliases=["restore"])
    async def restore(self, ctx, member: discord.Member):
        """Riapplica la copia salvata: rimuove i ruoli gestibili non presenti nella copia."""
        if not await self._enabled(ctx.guild):
            return await self._say(ctx, "Attiva prima Sticky Roles.")
        if not await self.memory.retry(member):
            return await self._say(ctx, "Non esiste una copia salvata per questo membro.")
        await self._say(ctx, "Ripristino avviato. Risultato disponibile in `.sr utente ID_UTENTE`.")

    @sr.command(name="dimentica", aliases=["forget"])
    async def forget(self, ctx, user_id: int, confirmation: str = ""):
        """Elimina solo la copia, non i ruoli Discord; richiede CONFERMO."""
        if confirmation.upper() != "CONFERMO":
            return await self._say(ctx, f"Usa `.sr dimentica {user_id} CONFERMO`. "
                            "Non cambia i ruoli attuali. Future modifiche/scansioni possono salvare di nuovo il membro.")
        await self.memory.forget(ctx.guild.id, user_id)
        await self._say(ctx, "Copia eliminata; nessun ruolo attuale modificato.")

    @sr.command(name="stato", aliases=["status"])
    async def status(self, ctx):
        members = await self.config.all_members(ctx.guild)
        known = [d for d in members.values() if d.get("known")]
        await self._say(ctx, f"Sticky Roles v{self.__version__}\nAttivo: {await self._enabled(ctx.guild)}\n"
                        f"Membri salvati: {len(known)} | In ripristino/da ritentare: {sum(d.get('pending', False) for d in known)}\n"
                        f"Members Intent: {self.bot.intents.members} | Gestire ruoli: {ctx.guild.me.guild_permissions.manage_roles}\n"
                        "Salva @everyone implicitamente; i ruoli gestiti, eliminati o sopra il bot non possono essere ripristinati.")

    @sr.command(name="conflitti", aliases=["conflicts"])
    async def conflicts(self, ctx):
        """Fast compatibility report: never inspect/parse cog source on the event loop."""
        integrated = {"AutoRole", "Quarantena", "ReactionRoles", "NitroRole", "HierarchyAdmin", "OwnerControl"}
        lines = ["Controllo rapido dei cog caricati (nessuna scansione bloccante dei sorgenti):"]
        for name, cog in sorted(self.bot.cogs.items()):
            if cog is self:
                continue
            if name not in integrated and not getattr(cog, "__sticky_roles_compatible__", False):
                continue
            status = "integrato" if getattr(cog, "__sticky_roles_compatible__", False) else "NON aggiornato"
            lines.append(f"{name}: {status}")
        lines.append("Nota: questo elenco non e un audit di tutti i cog/bot esterni. " 
                     "AutoRole, Quarantena e gli altri gestori devono essere aggiornati e ricaricati.")
        await self._say(ctx, "\n".join(lines))
