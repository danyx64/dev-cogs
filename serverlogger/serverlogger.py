import asyncio
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path


ITALY_TZ = ZoneInfo("Europe/Rome")
MAX_RESULTS = 25
AUDIT_WINDOW_SECONDS = 12


class ServerLogger(commands.Cog):
    """Audit logger persistente del server con ricerca tramite il gruppo ``log``."""

    __author__ = "danyx64"
    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=846217530194627311, force_registration=True)
        self.config.register_guild(log_channel_id=None, enabled=True)
        self._db_path: Path = cog_data_path(self) / "serverlogs.sqlite3"
        self._db_lock = asyncio.Lock()
        self._audit_cache: Dict[Tuple[int, int, str], Tuple[datetime, Optional[int]]] = {}
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, action TEXT NOT NULL, staffer_id INTEGER, user_id INTEGER, channel_id INTEGER, created_at TEXT NOT NULL, details TEXT)""")
            for name, cols in (("guild", "guild_id"), ("staffer", "guild_id, staffer_id"), ("user", "guild_id, user_id"), ("channel", "guild_id, channel_id"), ("action", "guild_id, action"), ("created", "guild_id, created_at")):
                db.execute(f"CREATE INDEX IF NOT EXISTS idx_logs_{name} ON logs({cols})")
            db.commit()

    async def _insert_log(self, guild_id, action, staffer_id, user_id, channel_id, created_at, details=None):
        payload = json.dumps(details or {}, ensure_ascii=False, default=str)
        async with self._db_lock:
            return await asyncio.to_thread(self._insert_log_sync, guild_id, action, staffer_id, user_id, channel_id, created_at.isoformat(), payload)

    def _insert_log_sync(self, guild_id, action, staffer_id, user_id, channel_id, created_at, details):
        with self._connect() as db:
            cur = db.execute("INSERT INTO logs (guild_id, action, staffer_id, user_id, channel_id, created_at, details) VALUES (?, ?, ?, ?, ?, ?, ?)", (guild_id, action, staffer_id, user_id, channel_id, created_at, details))
            db.commit()
            return int(cur.lastrowid)

    async def _query_logs(self, guild_id, filters, limit=10):
        limit = max(1, min(int(limit), MAX_RESULTS))
        async with self._db_lock:
            return await asyncio.to_thread(self._query_logs_sync, guild_id, filters, limit)

    def _query_logs_sync(self, guild_id, filters, limit):
        clauses, params = ["guild_id = ?"], [guild_id]
        for key, col in (("id", "id"), ("staffer_id", "staffer_id"), ("user_id", "user_id"), ("channel_id", "channel_id")):
            if filters.get(key) is not None:
                clauses.append(f"{col} = ?"); params.append(filters[key])
        if filters.get("action"):
            clauses.append("LOWER(action) LIKE ?"); params.append(f"%{str(filters['action']).lower()}%")
        if filters.get("date"):
            start = datetime.strptime(filters["date"], "%d/%m/%Y").replace(tzinfo=ITALY_TZ)
            end = start + timedelta(days=1)
            clauses.append("created_at >= ? AND created_at < ?")
            params.extend([start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()])
        params.append(limit)
        sql = f"SELECT * FROM logs WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?"
        with self._connect() as db:
            return list(db.execute(sql, params).fetchall())

    @staticmethod
    def _clean_text(value, max_len=900):
        if value is None: return "—"
        text = str(value).strip()
        if not text: return "—"
        return text if len(text) <= max_len else text[:max_len - 1] + "…"

    @staticmethod
    def _object_id(obj):
        return getattr(obj, "id", None)

    @staticmethod
    def _mention_or_id(guild, object_id, *, channel=False):
        if not object_id: return "—"
        obj = (guild.get_channel(object_id) or guild.get_thread(object_id)) if channel else guild.get_member(object_id)
        return getattr(obj, "mention", str(obj)) if obj is not None else f"`{object_id}`"

    def _make_embed(self, guild, log_id, action, staffer_id, user_id, channel_id, when, details=None):
        local = when.astimezone(ITALY_TZ)
        embed = discord.Embed(title=f"Log #{log_id}", colour=discord.Colour.blurple())
        embed.add_field(name="Azione", value=self._clean_text(action), inline=False)
        embed.add_field(name="Staffer", value=self._mention_or_id(guild, staffer_id), inline=False)
        embed.add_field(name="Utente", value=self._mention_or_id(guild, user_id), inline=False)
        embed.add_field(name="Canale", value=self._mention_or_id(guild, channel_id, channel=True), inline=False)
        embed.add_field(name="Data", value=local.strftime("%d/%m/%Y"), inline=True)
        embed.add_field(name="Ora", value=local.strftime("%H:%M:%S"), inline=True)
        if details:
            visible = [f"**{k}:** {self._clean_text(v, 350)}" for k, v in details.items() if v not in (None, "", [], {}, ())]
            if visible: embed.add_field(name="Dettagli", value=self._clean_text("\n".join(visible), 1000), inline=False)
        return embed

    async def _emit(self, guild, action, *, staffer=None, user=None, channel=None, details=None, when=None):
        if guild is None or not await self.config.guild(guild).enabled(): return
        when = when or discord.utils.utcnow()
        staffer_id, user_id, channel_id = self._object_id(staffer), self._object_id(user), self._object_id(channel)
        log_id = await self._insert_log(guild.id, action, staffer_id, user_id, channel_id, when, details)
        cid = await self.config.guild(guild).log_channel_id()
        log_channel = guild.get_channel(int(cid)) if cid else None
        if not isinstance(log_channel, (discord.TextChannel, discord.Thread)): return
        try:
            await log_channel.send(embed=self._make_embed(guild, log_id, action, staffer_id, user_id, channel_id, when, details), allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException): pass

    async def _find_audit_actor(self, guild, action, *, target_id=None, channel_id=None):
        if guild.me is None or not guild.me.guild_permissions.view_audit_log: return None
        now = discord.utils.utcnow()
        cache_key = (guild.id, target_id or channel_id or 0, str(action))
        cached = self._audit_cache.get(cache_key)
        if cached and (now - cached[0]).total_seconds() < 3:
            return guild.get_member(cached[1]) if cached[1] else None
        try:
            async for entry in guild.audit_logs(limit=8, action=action):
                if abs((now - entry.created_at).total_seconds()) > AUDIT_WINDOW_SECONDS: continue
                tid = self._object_id(entry.target)
                if target_id is not None and tid not in (None, target_id): continue
                if channel_id is not None:
                    extra_id = self._object_id(getattr(getattr(entry, "extra", None), "channel", None))
                    if channel_id not in (extra_id, tid): continue
                self._audit_cache[cache_key] = (now, self._object_id(entry.user))
                return entry.user
        except (discord.Forbidden, discord.HTTPException): pass
        return None

    async def _find_recent_audit_actor(self, guild, actions, *, target_id=None, channel_id=None):
        for action in actions:
            actor = await self._find_audit_actor(guild, action, target_id=target_id, channel_id=channel_id)
            if actor is not None: return actor, action
        return None, None

    @commands.group(name="log", invoke_without_command=True)
    @commands.guild_only()
    async def log_group(self, ctx):
        """Configura e consulta ServerLogger."""
        await ctx.send_help(ctx.command)

    @log_group.command(name="setchannel")
    @commands.admin_or_permissions(manage_guild=True)
    async def log_setchannel(self, ctx, channel_id: int):
        """Imposta il canale dei log usando il suo ID."""
        channel = ctx.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send("Non trovo un canale testuale con questo ID in questo server.")
        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            return await ctx.send("Non ho i permessi necessari in quel canale: Visualizza canale, Invia messaggi ed Incorpora link.")
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
        await ctx.send(f"Canale log impostato su {channel.mention} (`{channel.id}`).")

    @log_group.command(name="enable")
    @commands.admin_or_permissions(manage_guild=True)
    async def log_enable(self, ctx):
        await self.config.guild(ctx.guild).enabled.set(True); await ctx.send("ServerLogger abilitato.")

    @log_group.command(name="disable")
    @commands.admin_or_permissions(manage_guild=True)
    async def log_disable(self, ctx):
        await self.config.guild(ctx.guild).enabled.set(False); await ctx.send("ServerLogger disabilitato.")

    @log_group.command(name="status")
    @commands.mod_or_permissions(manage_guild=True)
    async def log_status(self, ctx):
        enabled = await self.config.guild(ctx.guild).enabled(); cid = await self.config.guild(ctx.guild).log_channel_id(); channel = ctx.guild.get_channel(cid) if cid else None
        await ctx.send(f"Stato: **{'attivo' if enabled else 'disattivato'}**\nCanale: {channel.mention if channel else '—'}" + (f" (`{cid}`)" if cid else ""))

    @log_group.command(name="last")
    @commands.mod_or_permissions(manage_messages=True)
    async def log_last(self, ctx, amount: int = 10): await self._send_search_results(ctx, {}, amount)

    @log_group.command(name="id")
    @commands.mod_or_permissions(manage_messages=True)
    async def log_by_id(self, ctx, log_id: int): await self._send_search_results(ctx, {"id": log_id}, 1)

    @log_group.command(name="user")
    @commands.mod_or_permissions(manage_messages=True)
    async def log_user(self, ctx, user_id: int, amount: int = 10): await self._send_search_results(ctx, {"user_id": user_id}, amount)

    @log_group.command(name="staff")
    @commands.mod_or_permissions(manage_messages=True)
    async def log_staff(self, ctx, staffer_id: int, amount: int = 10): await self._send_search_results(ctx, {"staffer_id": staffer_id}, amount)

    @log_group.command(name="channel")
    @commands.mod_or_permissions(manage_messages=True)
    async def log_channel(self, ctx, channel_id: int, amount: int = 10): await self._send_search_results(ctx, {"channel_id": channel_id}, amount)

    @log_group.command(name="action")
    @commands.mod_or_permissions(manage_messages=True)
    async def log_action(self, ctx, *, action: str): await self._send_search_results(ctx, {"action": action}, 10)

    @log_group.command(name="date")
    @commands.mod_or_permissions(manage_messages=True)
    async def log_date(self, ctx, date: str, amount: int = 10):
        if not self._valid_date(date): return await ctx.send("Data non valida. Usa `GG/MM/AAAA`.")
        await self._send_search_results(ctx, {"date": date}, amount)

    @log_group.command(name="search")
    @commands.mod_or_permissions(manage_messages=True)
    async def log_search(self, ctx, *, query: str):
        try: filters, limit = self._parse_search(query)
        except ValueError as exc: return await ctx.send(str(exc))
        await self._send_search_results(ctx, filters, limit)

    @log_group.command(name="clear")
    @commands.admin_or_permissions(manage_guild=True)
    async def log_clear(self, ctx, confirmation: str = ""):
        if confirmation.upper() != "CONFERMO": return await ctx.send("Per eliminare definitivamente i log usa `.log clear CONFERMO`.")
        async with self._db_lock: deleted = await asyncio.to_thread(self._clear_guild_logs_sync, ctx.guild.id)
        await ctx.send(f"Archivio cancellato: {deleted} log eliminati.")

    def _clear_guild_logs_sync(self, guild_id):
        with self._connect() as db:
            cur = db.execute("DELETE FROM logs WHERE guild_id = ?", (guild_id,)); db.commit(); return int(cur.rowcount)

    @staticmethod
    def _valid_date(value):
        try: datetime.strptime(value, "%d/%m/%Y"); return True
        except ValueError: return False

    def _parse_search(self, query):
        filters, limit = {}, 10
        pattern = re.compile(r'(user|staff|channel|date|limit):(?:(?:"([^"]+)")|(\S+))', re.I)
        for match in pattern.finditer(query):
            key, value = match.group(1).lower(), (match.group(2) or match.group(3) or "").strip()
            if key in {"user", "staff", "channel", "limit"}:
                if not value.isdigit(): raise ValueError(f"Il valore di `{key}:` deve essere numerico.")
                n = int(value)
                if key == "user": filters["user_id"] = n
                elif key == "staff": filters["staffer_id"] = n
                elif key == "channel": filters["channel_id"] = n
                else: limit = max(1, min(n, MAX_RESULTS))
            elif key == "date":
                if not self._valid_date(value): raise ValueError("`date:` deve essere nel formato GG/MM/AAAA.")
                filters["date"] = value
        am = re.search(r'action:(?:"([^"]+)"|(.+?))(?=\s+(?:user|staff|channel|date|limit):|$)', query, flags=re.I)
        if am: filters["action"] = (am.group(1) or am.group(2) or "").strip()
        if not filters: raise ValueError("Nessun filtro valido. Esempio: `.log search user:123 staff:456 action:\"ban\" date:20/08/2026`.")
        return filters, limit

    async def _send_search_results(self, ctx, filters, amount):
        rows = await self._query_logs(ctx.guild.id, filters, amount)
        if not rows: return await ctx.send("Nessun log trovato con questi criteri.")
        for row in rows:
            await ctx.send(embed=self._make_embed(ctx.guild, int(row["id"]), row["action"], row["staffer_id"], row["user_id"], row["channel_id"], datetime.fromisoformat(row["created_at"]), json.loads(row["details"] or "{}")), allowed_mentions=discord.AllowedMentions.none())

    @commands.Cog.listener()
    async def on_member_join(self, member): await self._emit(member.guild, "Ingresso nel server", user=member)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        guild = member.guild
        if await self._find_audit_actor(guild, discord.AuditLogAction.ban, target_id=member.id): return
        actor = await self._find_audit_actor(guild, discord.AuditLogAction.kick, target_id=member.id)
        await self._emit(guild, "Kick dal server" if actor else "Uscita dal server", staffer=actor, user=member)

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user): await self._emit(guild, "Ban", staffer=await self._find_audit_actor(guild, discord.AuditLogAction.ban, target_id=user.id), user=user)

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user): await self._emit(guild, "Unban", staffer=await self._find_audit_actor(guild, discord.AuditLogAction.unban, target_id=user.id), user=user)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        guild = after.guild
        if before.nick != after.nick:
            actor = await self._find_audit_actor(guild, discord.AuditLogAction.member_update, target_id=after.id)
            await self._emit(guild, "Nickname modificato", staffer=actor, user=after, details={"Prima": before.nick or before.name, "Dopo": after.nick or after.name})
        br, ar = {r.id:r for r in before.roles}, {r.id:r for r in after.roles}
        added, removed = [r for i,r in ar.items() if i not in br], [r for i,r in br.items() if i not in ar]
        if added or removed:
            actor = await self._find_audit_actor(guild, discord.AuditLogAction.member_role_update, target_id=after.id)
            if added: await self._emit(guild, "Ruolo aggiunto", staffer=actor, user=after, details={"Ruoli": ", ".join(r.name for r in added)})
            if removed: await self._emit(guild, "Ruolo rimosso", staffer=actor, user=after, details={"Ruoli": ", ".join(r.name for r in removed)})
        if before.timed_out_until != after.timed_out_until:
            actor = await self._find_audit_actor(guild, discord.AuditLogAction.member_update, target_id=after.id)
            until = after.timed_out_until.astimezone(ITALY_TZ).strftime("%d/%m/%Y %H:%M:%S") if after.timed_out_until else None
            await self._emit(guild, "Timeout applicato/modificato" if after.timed_out_until else "Timeout rimosso", staffer=actor, user=after, details={"Fino a": until})

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.guild is None or message.author.bot: return
        actor = await self._find_audit_actor(message.guild, discord.AuditLogAction.message_delete, target_id=message.author.id, channel_id=message.channel.id)
        await self._emit(message.guild, "Messaggio eliminato", staffer=actor, user=message.author, channel=message.channel, details={"Messaggio ID": message.id, "Contenuto": self._clean_text(message.content, 450) if message.content else "—"})

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages):
        if not messages or messages[0].guild is None: return
        guild, channel = messages[0].guild, messages[0].channel
        actor = await self._find_audit_actor(guild, discord.AuditLogAction.message_bulk_delete, channel_id=channel.id)
        await self._emit(guild, "Eliminazione massiva messaggi", staffer=actor, channel=channel, details={"Quantità": len(messages)})

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        if payload.cached_message is not None or payload.guild_id is None: return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None: return
        channel = guild.get_channel(payload.channel_id) or guild.get_thread(payload.channel_id)
        actor = await self._find_audit_actor(guild, discord.AuditLogAction.message_delete, channel_id=payload.channel_id)
        await self._emit(guild, "Messaggio eliminato (non in cache)", staffer=actor, channel=channel, details={"Messaggio ID": payload.message_id})

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.guild is None or before.author.bot or before.content == after.content: return
        await self._emit(before.guild, "Messaggio modificato", user=before.author, channel=before.channel, details={"Messaggio ID": before.id, "Prima": self._clean_text(before.content, 400), "Dopo": self._clean_text(after.content, 400)})

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        guild = member.guild
        if before.channel != after.channel:
            if before.channel is None and after.channel is not None: await self._emit(guild, "Ingresso in vocale", user=member, channel=after.channel)
            elif before.channel is not None and after.channel is None:
                actor = await self._find_audit_actor(guild, discord.AuditLogAction.member_disconnect)
                await self._emit(guild, "Disconnesso dalla vocale" if actor else "Uscita dalla vocale", staffer=actor, user=member, channel=before.channel)
            elif before.channel is not None and after.channel is not None:
                actor = await self._find_audit_actor(guild, discord.AuditLogAction.member_move)
                await self._emit(guild, "Spostato in un altro canale vocale" if actor else "Cambio canale vocale", staffer=actor, user=member, channel=after.channel, details={"Da": before.channel.name, "A": after.channel.name})
        for old, new, on, off in ((before.mute, after.mute, "Server mute", "Server unmute"), (before.deaf, after.deaf, "Server deafen", "Server undeafen")):
            if old != new:
                actor = await self._find_audit_actor(guild, discord.AuditLogAction.member_update, target_id=member.id)
                await self._emit(guild, on if new else off, staffer=actor, user=member, channel=after.channel or before.channel)
        for old, new, on, off in ((before.self_mute, after.self_mute, "Self mute", "Self unmute"), (before.self_deaf, after.self_deaf, "Self deafen", "Self undeafen"), (before.self_stream, after.self_stream, "Streaming avviato", "Streaming terminato"), (before.self_video, after.self_video, "Webcam attivata", "Webcam disattivata")):
            if old != new: await self._emit(guild, on if new else off, user=member, channel=after.channel or before.channel)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel): await self._emit(channel.guild, "Canale creato", staffer=await self._find_audit_actor(channel.guild, discord.AuditLogAction.channel_create, target_id=channel.id), channel=channel, details={"Nome": channel.name})

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel): await self._emit(channel.guild, "Canale eliminato", staffer=await self._find_audit_actor(channel.guild, discord.AuditLogAction.channel_delete, target_id=channel.id), channel=channel, details={"Nome": channel.name})

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        actor = await self._find_audit_actor(after.guild, discord.AuditLogAction.channel_update, target_id=after.id)
        details = {}
        if before.name != after.name: details.update({"Nome prima": before.name, "Nome dopo": after.name})
        if getattr(before, "category_id", None) != getattr(after, "category_id", None): details.update({"Categoria prima": getattr(getattr(before, "category", None), "name", "—"), "Categoria dopo": getattr(getattr(after, "category", None), "name", "—")})
        if getattr(before, "position", None) != getattr(after, "position", None): details["Posizione"] = f"{getattr(before, 'position', '—')} → {getattr(after, 'position', '—')}"
        await self._emit(after.guild, "Canale modificato", staffer=actor, channel=after, details=details)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role): await self._emit(role.guild, "Ruolo creato", staffer=await self._find_audit_actor(role.guild, discord.AuditLogAction.role_create, target_id=role.id), details={"Ruolo": role.name, "ID": role.id})

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role): await self._emit(role.guild, "Ruolo eliminato", staffer=await self._find_audit_actor(role.guild, discord.AuditLogAction.role_delete, target_id=role.id), details={"Ruolo": role.name, "ID": role.id})

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        actor = await self._find_audit_actor(after.guild, discord.AuditLogAction.role_update, target_id=after.id); details={"Ruolo": after.name, "ID": after.id}
        if before.name != after.name: details["Nome"] = f"{before.name} → {after.name}"
        if before.permissions != after.permissions: details["Permessi"] = "Modificati"
        if before.colour != after.colour: details["Colore"] = f"{before.colour} → {after.colour}"
        await self._emit(after.guild, "Ruolo modificato", staffer=actor, details=details)

    @commands.Cog.listener()
    async def on_guild_update(self, before, after):
        actor = await self._find_audit_actor(after, discord.AuditLogAction.guild_update, target_id=after.id); details={}
        if before.name != after.name: details["Nome"] = f"{before.name} → {after.name}"
        if before.verification_level != after.verification_level: details["Verifica"] = f"{before.verification_level} → {after.verification_level}"
        await self._emit(after, "Server modificato", staffer=actor, details=details)

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild, before, after):
        b,a={e.id:e for e in before},{e.id:e for e in after}
        for i,e in a.items():
            if i not in b: await self._emit(guild, "Emoji creata", staffer=await self._find_audit_actor(guild, discord.AuditLogAction.emoji_create, target_id=i), details={"Emoji":e.name,"ID":i})
            elif b[i].name != e.name: await self._emit(guild, "Emoji modificata", staffer=await self._find_audit_actor(guild, discord.AuditLogAction.emoji_update, target_id=i), details={"Emoji":e.name,"ID":i})
        for i,e in b.items():
            if i not in a: await self._emit(guild, "Emoji eliminata", staffer=await self._find_audit_actor(guild, discord.AuditLogAction.emoji_delete, target_id=i), details={"Emoji":e.name,"ID":i})

    @commands.Cog.listener()
    async def on_guild_stickers_update(self, guild, before, after):
        b,a={s.id:s for s in before},{s.id:s for s in after}
        for i,s in a.items():
            if i not in b: await self._emit(guild, "Sticker creato", staffer=await self._find_audit_actor(guild, discord.AuditLogAction.sticker_create, target_id=i), details={"Sticker":s.name,"ID":i})
            elif b[i].name != s.name: await self._emit(guild, "Sticker modificato", staffer=await self._find_audit_actor(guild, discord.AuditLogAction.sticker_update, target_id=i), details={"Sticker":s.name,"ID":i})
        for i,s in b.items():
            if i not in a: await self._emit(guild, "Sticker eliminato", staffer=await self._find_audit_actor(guild, discord.AuditLogAction.sticker_delete, target_id=i), details={"Sticker":s.name,"ID":i})

    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        if not isinstance(invite.guild, discord.Guild): return
        await self._emit(invite.guild, "Invito creato", staffer=invite.inviter or await self._find_audit_actor(invite.guild, discord.AuditLogAction.invite_create), channel=invite.channel, details={"Codice":invite.code,"Scadenza":invite.expires_at})

    @commands.Cog.listener()
    async def on_invite_delete(self, invite):
        if isinstance(invite.guild, discord.Guild): await self._emit(invite.guild, "Invito eliminato", staffer=await self._find_audit_actor(invite.guild, discord.AuditLogAction.invite_delete), channel=invite.channel, details={"Codice":invite.code})

    @commands.Cog.listener()
    async def on_thread_create(self, thread): await self._emit(thread.guild, "Thread creato", staffer=await self._find_audit_actor(thread.guild, discord.AuditLogAction.thread_create, target_id=thread.id), channel=thread, details={"Nome":thread.name})

    @commands.Cog.listener()
    async def on_thread_delete(self, thread): await self._emit(thread.guild, "Thread eliminato", staffer=await self._find_audit_actor(thread.guild, discord.AuditLogAction.thread_delete, target_id=thread.id), channel=thread, details={"Nome":thread.name})

    @commands.Cog.listener()
    async def on_thread_update(self, before, after): await self._emit(after.guild, "Thread modificato", staffer=await self._find_audit_actor(after.guild, discord.AuditLogAction.thread_update, target_id=after.id), channel=after, details={"Nome": f"{before.name} → {after.name}" if before.name != after.name else after.name})

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel):
        actor, action = await self._find_recent_audit_actor(channel.guild, (discord.AuditLogAction.webhook_create, discord.AuditLogAction.webhook_update, discord.AuditLogAction.webhook_delete), channel_id=channel.id)
        label={discord.AuditLogAction.webhook_create:"Webhook creato",discord.AuditLogAction.webhook_update:"Webhook modificato",discord.AuditLogAction.webhook_delete:"Webhook eliminato"}.get(action,"Webhook modificati")
        await self._emit(channel.guild, label, staffer=actor, channel=channel)

    @commands.Cog.listener()
    async def on_scheduled_event_create(self, event): await self._emit(event.guild, "Evento programmato creato", staffer=await self._find_audit_actor(event.guild, discord.AuditLogAction.scheduled_event_create, target_id=event.id), channel=event.channel, details={"Evento":event.name})

    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, event): await self._emit(event.guild, "Evento programmato eliminato", staffer=await self._find_audit_actor(event.guild, discord.AuditLogAction.scheduled_event_delete, target_id=event.id), channel=event.channel, details={"Evento":event.name})

    @commands.Cog.listener()
    async def on_scheduled_event_update(self, before, after): await self._emit(after.guild, "Evento programmato modificato", staffer=await self._find_audit_actor(after.guild, discord.AuditLogAction.scheduled_event_update, target_id=after.id), channel=after.channel, details={"Evento":after.name})
