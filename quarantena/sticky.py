"""Persistent, guild-scoped quarantine IDs. No credentials or external storage."""

import asyncio
import logging
import re
import weakref
from dataclasses import dataclass

import discord

LOG = logging.getLogger("red.quarantena.sticky")


@dataclass(frozen=True)
class ApplyResult:
    new: bool = False
    applied: bool = False
    note: str = ""


class StickyRoles:
    """Use the existing Config namespace and isolated list; never clear on leave."""

    def __init__(self, cog):
        self.cog = cog
        self.bot = cog.bot
        self.config = cog.config
        self._member_locks = weakref.WeakValueDictionary()
        self._guild_locks = weakref.WeakValueDictionary()
        self._pending = {}
        self._dirty = set()
        self._tasks = set()
        self._closed = False

    @staticmethod
    def _lock(store, key):
        lock = store.get(key)
        if lock is None:
            lock = asyncio.Lock()
            store[key] = lock
        return lock

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task):
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            error = task.exception()
            LOG.error("Errore ripristino sticky", exc_info=(type(error), error, error.__traceback__))

    def close(self):
        self._closed = True
        for task in tuple(self._tasks):
            task.cancel()
        self._dirty.clear()

    async def _active(self, guild, data):
        return (
            not self._closed
            and data["enabled"]
            and not await self.bot.cog_disabled_in_guild(self.cog, guild)
        )

    async def _remember(self, guild, user_id):
        async with self._lock(self._guild_locks, guild.id):
            async with self.config.guild(guild).isolated() as ids:
                if user_id in ids:
                    return False
                ids.append(user_id)
                return True

    async def _nickname(self, member):
        cfg = self.config.guild(member.guild)
        key = str(member.id)
        async with self._lock(self._guild_locks, member.guild.id):
            names = await cfg.sticky_nicks()
            if key in names:
                return names[key]
            counter = await cfg.counter()
            # Preserve legacy UserN names where possible, without reusing a serial.
            match = re.fullmatch(r"User([1-9][0-9]{0,26})", member.nick or "")
            if match and member.nick not in names.values():
                number = int(match.group(1))
            else:
                number = counter + 1
            nickname = f"User{number}"
            await cfg.counter.set(max(counter, number))
            async with cfg.sticky_nicks() as stored:
                stored[key] = nickname
            return nickname

    async def apply(self, member, reason, *, record=False):
        """Remember before API calls. Reapplying never allocates another nickname."""
        guild = member.guild
        async with self._lock(self._member_locks, (guild.id, member.id)):
            data = await self.config.guild(guild).all()
            if not await self._active(guild, data):
                return ApplyResult(note="Quarantena disattivata.")
            known = member.id in data["isolated"]
            if not known and not record:
                return ApplyResult(note="ID non nella lista sticky.")
            if member.bot or member.id == guild.owner_id:
                return ApplyResult(note="Bot e proprietario sono esclusi.")
            if not known and member.guild_permissions.administrator:
                return ApplyResult(note="Nuove violazioni degli amministratori escluse.")
            # Persist before any Discord request, including fetch_member: a user
            # leaving immediately after a violation must not escape the ID list.
            first = await self._remember(guild, member.id) if record else False
            # Fetch current roles, not a stale gateway snapshot, to avoid role loops.
            try:
                member = await guild.fetch_member(member.id)
            except discord.NotFound:
                return ApplyResult(first, False, "Utente non presente; ID conservato.")
            except discord.HTTPException as exc:
                LOG.warning("Lettura membro %s/%s fallita: %s", guild.id, member.id, exc)
                return ApplyResult(first, False, "Impossibile leggere il membro; ID conservato.")
            if member.bot or member.id == guild.owner_id:
                return ApplyResult(note="Bot e proprietario sono esclusi.")
            # A recorded member cannot escape by receiving an administrator role.
            nickname = await self._nickname(member)
            role = guild.get_role(data["role_id"])
            me = guild.me
            problem = None
            if me is None or not me.guild_permissions.manage_roles:
                problem = "Al bot manca Gestire ruoli."
            elif role is None or role.is_default() or role.managed:
                problem = "Ruolo quarantena inesistente o non assegnabile."
            elif role.permissions.administrator:
                problem = "Il ruolo quarantena non deve essere Amministratore."
            elif role >= me.top_role or member.top_role >= me.top_role:
                problem = "Il ruolo del bot deve essere sopra il ruolo quarantena e il membro."
            if problem:
                LOG.warning("Sticky %s/%s: %s ID conservato.", guild.id, member.id, problem)
                return ApplyResult(first, False, problem)
            try:
                if role not in member.roles:
                    await member.add_roles(role, reason=reason, atomic=True)
            except discord.HTTPException as exc:
                LOG.warning("Ruolo sticky non assegnato a %s/%s: %s", guild.id, member.id, exc)
                return ApplyResult(first, False, "Assegnazione fallita; ID conservato.")
            failed = []
            retained = []
            for old_role in member.roles:
                if old_role.is_default() or old_role.id == role.id:
                    continue
                if old_role.managed or old_role >= me.top_role:
                    retained.append(old_role.id)
                    continue
                try:
                    await member.remove_roles(old_role, reason=reason, atomic=True)
                except discord.HTTPException as exc:
                    failed.append(old_role.id)
                    LOG.warning("Rimozione ruolo %s da %s fallita: %s", old_role.id, member.id, exc)
            if member.nick != nickname and me.guild_permissions.manage_nicknames:
                try:
                    await member.edit(nick=nickname, reason=reason)
                except discord.HTTPException as exc:
                    LOG.warning("Nickname sticky %s non applicato: %s", member.id, exc)
            if retained or failed:
                LOG.warning("Sticky %s: non rimovibili=%s, falliti=%s", member.id, retained, failed)
            note = "Ruolo applicato e ruoli gestibili rimossi."
            if retained or failed:
                note += f" Ruoli rimasti/non rimossi: {retained + failed}."
            return ApplyResult(first, not failed, note)

    def schedule(self, member):
        """Coalesce role/nickname events, including late autoroles and our own events."""
        if self._closed:
            return
        key = (member.guild.id, member.id)
        self._dirty.add(key)
        if key not in self._pending:
            self._pending[key] = self._spawn(self._recheck(member, key))

    async def _recheck(self, member, key):
        try:
            # Bounded passes prevent one event burst from creating an endless loop.
            for _ in range(3):
                if self._closed or key not in self._dirty:
                    break
                self._dirty.discard(key)
                await asyncio.sleep(0.5)
                await self.apply(member, "Ripristino ruoli/nickname sticky")
            if key in self._dirty:
                LOG.warning("Conflitto autorole per %s: escludere gli isolati dagli altri bot.", key)
        finally:
            self._dirty.discard(key)
            self._pending.pop(key, None)

    def restore(self, guild=None):
        if not self._closed:
            self._spawn(self._restore(guild))

    async def _restore(self, guild=None):
        await self.bot.wait_until_red_ready()
        guilds = [guild] if guild is not None else list(self.bot.guilds)
        for current in guilds:
            data = await self.config.guild(current).all()
            if not await self._active(current, data):
                continue
            for user_id in tuple(data["isolated"]):
                if self._closed:
                    return
                member = current.get_member(user_id)
                if member is None:
                    try:
                        member = await current.fetch_member(user_id)
                    except discord.NotFound:
                        continue  # Keep IDs of users outside the server.
                    except discord.HTTPException as exc:
                        LOG.warning("Ripristino sticky %s/%s: %s", current.id, user_id, exc)
                        continue
                await self.apply(member, "Ripristino sticky dopo avvio/attivazione")

    async def add_id(self, guild, user_id):
        if not 0 < user_id < 2**64 or user_id == guild.owner_id:
            return "ID non valido oppure appartenente al proprietario."
        async with self._lock(self._member_locks, (guild.id, user_id)):
            data = await self.config.guild(guild).all()
            role = guild.get_role(data["role_id"])
            if role is None or role.is_default() or role.managed or role.permissions.administrator:
                return "Configura prima un ruolo quarantena valido."
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                member = None
            except discord.HTTPException:
                return "Verifica del membro fallita: nessuna modifica, riprova."
            if member is not None and (
                member.bot or (member.guild_permissions.administrator and user_id not in data["isolated"])
            ):
                return "Bot e nuovi amministratori sono esclusi."
            await self._remember(guild, user_id)
        if member is None:
            return "ID salvato: quarantena applicata al prossimo ingresso, quando attiva."
        result = await self.apply(member, "Aggiunta manuale alla lista sticky")
        return "ID salvato. " + result.note

    async def release(self, guild, user_id):
        """Forget first, under the same lock as apply; pending tasks cannot re-add it."""
        async with self._lock(self._member_locks, (guild.id, user_id)):
            cfg = self.config.guild(guild)
            async with self._lock(self._guild_locks, guild.id):
                async with cfg.isolated() as ids:
                    if user_id not in ids:
                        return "ID non presente nella lista sticky."
                    ids.remove(user_id)
                async with cfg.sticky_nicks() as names:
                    names.pop(str(user_id), None)
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                return "ID rimosso: nessun blocco sticky al prossimo ingresso."
            except discord.HTTPException:
                return "ID rimosso. Controlla manualmente il ruolo: lettura membro fallita."
            role = guild.get_role(await cfg.role_id())
            if role is not None and role in member.roles:
                try:
                    await member.remove_roles(role, reason="Rimozione dalla lista sticky", atomic=True)
                except discord.HTTPException:
                    return "ID rimosso, ma il ruolo va tolto manualmente: permessi/API insufficienti."
            return "ID e ruolo quarantena rimossi. I vecchi ruoli e il nickname non vengono ripristinati."

    async def forget_everywhere(self, user_id):
        for guild_id in await self.config.all_guilds():
            async with self._lock(self._member_locks, (guild_id, user_id)):
                cfg = self.config.guild_from_id(guild_id)
                async with self._lock(self._guild_locks, guild_id):
                    async with cfg.isolated() as ids:
                        ids[:] = [value for value in ids if value != user_id]
                    async with cfg.sticky_nicks() as names:
                        names.pop(str(user_id), None)
