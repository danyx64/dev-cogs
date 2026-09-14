from typing import Optional

import discord
from redbot.core import commands
from redbot.core.bot import Red


TRUE_VALUES = {"true", "on", "yes", "1", "enable", "enabled", "si", "sì"}
FALSE_VALUES = {"false", "off", "no", "0", "disable", "disabled"}
INHERIT_VALUES = {"inherit", "none", "default", "reset", "-"}


class HierarchyAdmin(commands.Cog):
    """Gestione della gerarchia, dei ruoli e dei permessi del server."""

    __author__ = "danyx64"
    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot

    @staticmethod
    def _bool_value(value: str) -> Optional[bool]:
        value = value.strip().lower()
        if value in TRUE_VALUES:
            return True
        if value in FALSE_VALUES:
            return False
        return None

    @staticmethod
    def _permission_names():
        return sorted(name for name, _ in discord.Permissions.all())

    async def _role(self, ctx: commands.Context, role_id: int, allow_everyone: bool = True):
        role = ctx.guild.get_role(role_id)
        if role is None:
            await ctx.send("Ruolo non trovato. Usa l'ID del ruolo.")
            return None
        if role.is_default() and not allow_everyone:
            await ctx.send("`@everyone` non è valido per questa operazione.")
            return None
        return role

    async def _member(self, ctx: commands.Context, user_id: int):
        member = ctx.guild.get_member(user_id)
        if member is None:
            try:
                member = await ctx.guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                member = None
        if member is None:
            await ctx.send("Membro non trovato in questo server.")
        return member

    async def _channel(self, ctx: commands.Context, channel_id: int):
        channel = ctx.guild.get_channel(channel_id)
        if channel is None:
            await ctx.send("Canale non trovato. Usa l'ID del canale.")
        return channel

    async def _can_edit_role(self, ctx: commands.Context, role: discord.Role, allow_everyone: bool = False):
        me = ctx.guild.me
        if role.managed:
            await ctx.send("Quel ruolo è gestito da Discord, un bot o un'integrazione.")
            return False
        if role.is_default() and not allow_everyone:
            await ctx.send("Questa operazione non è consentita su `@everyone`.")
            return False
        if me is None or not me.guild_permissions.manage_roles:
            await ctx.send("Mi serve il permesso **Gestisci ruoli**.")
            return False
        if not role.is_default() and role >= me.top_role:
            await ctx.send("Non posso modificare un ruolo uguale o superiore al mio ruolo più alto.")
            return False

        author = ctx.author
        if (
            isinstance(author, discord.Member)
            and author.id != ctx.guild.owner_id
            and not author.guild_permissions.administrator
            and not role.is_default()
            and role >= author.top_role
        ):
            await ctx.send("Non puoi modificare un ruolo uguale o superiore al tuo ruolo più alto.")
            return False
        return True

    @commands.group(name="ha", invoke_without_command=True)
    @commands.guild_only()
    async def ha(self, ctx: commands.Context):
        """Gestisce gerarchia, ruoli, membri e permessi del server."""
        await ctx.send_help(ctx.command)

    @ha.command(name="tree", aliases=["hierarchy", "gerarchia"])
    @commands.admin_or_permissions(manage_roles=True)
    async def ha_tree(self, ctx: commands.Context):
        """Mostra tutta la gerarchia dei ruoli dall'alto verso il basso."""
        lines = []
        for role in reversed(ctx.guild.roles):
            flags = []
            if role.is_default():
                flags.append("everyone")
            if role.managed:
                flags.append("gestito")
            if role.hoist:
                flags.append("separato")
            suffix = f" [{', '.join(flags)}]" if flags else ""
            lines.append(f"`{role.position:>3}` {role.mention} (`{role.id}`) - {len(role.members)} membri{suffix}")

        pages, current = [], ""
        for line in lines:
            if len(current) + len(line) + 1 > 3900:
                pages.append(current)
                current = ""
            current += line + "\n"
        if current:
            pages.append(current)

        for index, page in enumerate(pages, 1):
            embed = discord.Embed(
                title=f"Gerarchia ruoli - {ctx.guild.name}",
                description=page,
                colour=discord.Colour.blurple(),
            )
            embed.set_footer(text=f"Pagina {index}/{len(pages)} - Più in alto = più autorità")
            await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @ha.command(name="role")
    @commands.admin_or_permissions(manage_roles=True)
    async def ha_role(self, ctx: commands.Context, role_id: int):
        """Mostra posizione, membri, proprietà e permessi attivi di un ruolo."""
        role = await self._role(ctx, role_id)
        if role is None:
            return
        enabled = [name for name, value in role.permissions if value]
        text = (
            f"**Ruolo:** {role.mention}\n"
            f"**ID:** `{role.id}`\n"
            f"**Posizione:** `{role.position}`\n"
            f"**Membri:** `{len(role.members)}`\n"
            f"**Gestito:** `{'Sì' if role.managed else 'No'}`\n"
            f"**Separato:** `{'Sì' if role.hoist else 'No'}`\n"
            f"**Menzionabile:** `{'Sì' if role.mentionable else 'No'}`\n"
            f"**Colore:** `{role.colour}`\n\n"
            f"**Permessi attivi ({len(enabled)}):**\n"
            + (", ".join(f"`{name}`" for name in enabled) if enabled else "Nessuno")
        )
        await ctx.send(embed=discord.Embed(description=text[:4096], colour=role.colour or discord.Colour.blurple()))

    @ha.command(name="member")
    @commands.admin_or_permissions(manage_roles=True)
    async def ha_member(self, ctx: commands.Context, user_id: int):
        """Mostra ruoli e permessi effettivi di un membro."""
        member = await self._member(ctx, user_id)
        if member is None:
            return
        roles = [role for role in reversed(member.roles) if not role.is_default()]
        perms = [name for name, value in member.guild_permissions if value]
        text = (
            f"**Utente:** {member.mention}\n"
            f"**ID:** `{member.id}`\n"
            f"**Ruolo più alto:** {member.top_role.mention}\n"
            f"**Ruoli:** {', '.join(role.mention for role in roles) if roles else 'Nessuno'}\n\n"
            f"**Permessi effettivi ({len(perms)}):**\n"
            + (", ".join(f"`{name}`" for name in perms) if perms else "Nessuno")
        )
        await ctx.send(embed=discord.Embed(description=text[:4096], colour=member.colour or discord.Colour.blurple()), allowed_mentions=discord.AllowedMentions.none())

    @ha.group(name="perm", invoke_without_command=True)
    @commands.admin_or_permissions(administrator=True)
    async def ha_perm(self, ctx: commands.Context):
        """Visualizza e modifica i permessi server dei ruoli."""
        await ctx.send_help(ctx.command)

    @ha_perm.command(name="list")
    async def ha_perm_list(self, ctx: commands.Context):
        """Elenca tutti i nomi di permesso validi per `.ha perm set`."""
        names = self._permission_names()
        for index in range(0, len(names), 25):
            chunk = names[index:index + 25]
            await ctx.send(embed=discord.Embed(
                title=f"Permessi Discord - pagina {index // 25 + 1}/{(len(names) + 24) // 25}",
                description="\n".join(f"`{name}`" for name in chunk),
                colour=discord.Colour.blurple(),
            ))

    @ha_perm.command(name="view")
    async def ha_perm_view(self, ctx: commands.Context, role_id: int):
        """Mostra tutti i permessi del ruolo con stato attivo/disattivo."""
        role = await self._role(ctx, role_id)
        if role is None:
            return
        lines = [f"{'✅' if value else '❌'} `{name}`" for name, value in role.permissions]
        for index in range(0, len(lines), 25):
            await ctx.send(embed=discord.Embed(
                title=f"Permessi {role.name} - pagina {index // 25 + 1}/{(len(lines) + 24) // 25}",
                description="\n".join(lines[index:index + 25]),
                colour=role.colour or discord.Colour.blurple(),
            ))

    @ha_perm.command(name="set")
    async def ha_perm_set(self, ctx: commands.Context, role_id: int, permission: str, state: str):
        """Attiva/disattiva un permesso: `.ha perm set ROLE_ID manage_messages true`."""
        role = await self._role(ctx, role_id)
        if role is None or not await self._can_edit_role(ctx, role, allow_everyone=True):
            return
        permission = permission.lower().strip()
        if permission not in self._permission_names():
            return await ctx.send("Permesso non valido. Usa `.ha perm list`.")
        value = self._bool_value(state)
        if value is None:
            return await ctx.send("Stato non valido. Usa `true` oppure `false`.")
        permissions = role.permissions
        setattr(permissions, permission, value)
        try:
            await role.edit(permissions=permissions, reason=f"HierarchyAdmin: {ctx.author} ({ctx.author.id})")
            await ctx.send(f"`{permission}` per {role.mention}: **{'attivo' if value else 'disattivo'}**.")
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send("Discord ha rifiutato la modifica del permesso.")

    @ha_perm.command(name="all")
    async def ha_perm_all(self, ctx: commands.Context, role_id: int, state: str):
        """Attiva o disattiva tutti i permessi del ruolo in una sola volta."""
        role = await self._role(ctx, role_id)
        if role is None or not await self._can_edit_role(ctx, role, allow_everyone=True):
            return
        value = self._bool_value(state)
        if value is None:
            return await ctx.send("Stato non valido. Usa `true` oppure `false`.")
        permissions = discord.Permissions.all() if value else discord.Permissions.none()
        try:
            await role.edit(permissions=permissions, reason=f"HierarchyAdmin: {ctx.author} ({ctx.author.id})")
            await ctx.send(f"Tutti i permessi di {role.mention}: **{'attivi' if value else 'disattivi'}**.")
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send("Discord ha rifiutato la modifica dei permessi.")

    @ha.group(name="roleedit", aliases=["re"], invoke_without_command=True)
    @commands.admin_or_permissions(manage_roles=True)
    async def ha_roleedit(self, ctx: commands.Context):
        """Crea, elimina e modifica proprietà e posizione dei ruoli."""
        await ctx.send_help(ctx.command)

    @ha_roleedit.command(name="create")
    async def ha_role_create(self, ctx: commands.Context, *, name: str):
        """Crea un nuovo ruolo senza permessi."""
        if not 1 <= len(name) <= 100:
            return await ctx.send("Il nome deve avere da 1 a 100 caratteri.")
        try:
            role = await ctx.guild.create_role(name=name, permissions=discord.Permissions.none(), reason=f"HierarchyAdmin: {ctx.author} ({ctx.author.id})")
            await ctx.send(f"Ruolo creato: {role.mention} (`{role.id}`).")
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send("Non sono riuscito a creare il ruolo.")

    @ha_roleedit.command(name="delete")
    async def ha_role_delete(self, ctx: commands.Context, role_id: int, confirmation: str = ""):
        """Elimina un ruolo; richiede `CONFERMO`."""
        role = await self._role(ctx, role_id, allow_everyone=False)
        if role is None or not await self._can_edit_role(ctx, role):
            return
        if confirmation.upper() != "CONFERMO":
            return await ctx.send(f"Conferma con `.ha roleedit delete {role.id} CONFERMO`.")
        name = role.name
        try:
            await role.delete(reason=f"HierarchyAdmin: {ctx.author} ({ctx.author.id})")
            await ctx.send(f"Ruolo `{name}` eliminato.")
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send("Non sono riuscito a eliminare il ruolo.")

    @ha_roleedit.command(name="rename")
    async def ha_role_rename(self, ctx: commands.Context, role_id: int, *, name: str):
        """Rinomina un ruolo tramite ID."""
        role = await self._role(ctx, role_id, allow_everyone=False)
        if role is None or not await self._can_edit_role(ctx, role):
            return
        try:
            await role.edit(name=name[:100], reason=f"HierarchyAdmin: {ctx.author} ({ctx.author.id})")
            await ctx.send(f"Ruolo rinominato in {role.mention}.")
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send("Non sono riuscito a rinominare il ruolo.")

    @ha_roleedit.command(name="colour", aliases=["color"])
    async def ha_role_colour(self, ctx: commands.Context, role_id: int, hex_colour: str):
        """Imposta il colore del ruolo, es. `#ff0000`."""
        role = await self._role(ctx, role_id, allow_everyone=False)
        if role is None or not await self._can_edit_role(ctx, role):
            return
        raw = hex_colour.strip().lstrip("#")
        try:
            colour = int(raw, 16)
            if not 0 <= colour <= 0xFFFFFF:
                raise ValueError
        except ValueError:
            return await ctx.send("Colore non valido. Esempio: `#ff0000`.")
        try:
            await role.edit(colour=discord.Colour(colour), reason=f"HierarchyAdmin: {ctx.author} ({ctx.author.id})")
            await ctx.send(f"Colore aggiornato per {role.mention}.")
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send("Non sono riuscito a modificare il colore.")

    @ha_roleedit.command(name="hoist")
    async def ha_role_hoist(self, ctx: commands.Context, role_id: int, state: str):
        """Attiva/disattiva la visualizzazione separata del ruolo nella lista membri."""
        role = await self._role(ctx, role_id, allow_everyone=False)
        if role is None or not await self._can_edit_role(ctx, role):
            return
        value = self._bool_value(state)
        if value is None:
            return await ctx.send("Usa `true` o `false`.")
        try:
            await role.edit(hoist=value, reason=f"HierarchyAdmin: {ctx.author} ({ctx.author.id})")
            await ctx.send(f"Separazione di {role.mention}: **{'attiva' if value else 'disattiva'}**.")
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send("Non sono riuscito a modificare il ruolo.")

    @ha_roleedit.command(name="mentionable")
    async def ha_role_mentionable(self, ctx: commands.Context, role_id: int, state: str):
        """Attiva/disattiva la possibilità di menzionare il ruolo."""
        role = await self._role(ctx, role_id, allow_everyone=False)
        if role is None or not await self._can_edit_role(ctx, role):
            return
        value = self._bool_value(state)
        if value is None:
            return await ctx.send("Usa `true` o `false`.")
        try:
            await role.edit(mentionable=value, reason=f"HierarchyAdmin: {ctx.author} ({ctx.author.id})")
            await ctx.send(f"Menzionabilità di {role.mention}: **{'attiva' if value else 'disattiva'}**.")
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send("Non sono riuscito a modificare il ruolo.")

    @ha_roleedit.command(name="position")
    async def ha_role_position(self, ctx: commands.Context, role_id: int, position: int):
        """Sposta un ruolo alla posizione numerica indicata."""
        role = await self._role(ctx, role_id, allow_everyone=False)
        if role is None or not await self._can_edit_role(ctx, role):
            return
        me = ctx.guild.me
        max_position = max(1, me.top_role.position - 1) if me else 1
        position = max(1, min(position, max_position))
        try:
            await ctx.guild.edit_role_positions(positions={role: position}, reason=f"HierarchyAdmin: {ctx.author} ({ctx.author.id})")
            await ctx.send(f"{role.mention} spostato alla posizione `{position}`.")
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send("Non sono riuscito a spostare il ruolo.")

    @ha.command(name="give")
    @commands.admin_or_permissions(manage_roles=True)
    async def ha_give(self, ctx: commands.Context, user_id: int, role_id: int):
        """Assegna un ruolo a un membro usando gli ID."""
        member = await self._member(ctx, user_id)
        role = await self._role(ctx, role_id, allow_everyone=False)
        if member is None or role is None or not await self._can_edit_role(ctx, role):
            return
        if role in member.roles:
            return await ctx.send("L'utente possiede già quel ruolo.")
        try:
            await member.add_roles(role, reason=f"HierarchyAdmin: {ctx.author} ({ctx.author.id})")
            await ctx.send(f"Assegnato {role.mention} a {member.mention}.")
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send("Non sono riuscito ad assegnare il ruolo.")

    @ha.command(name="remove")
    @commands.admin_or_permissions(manage_roles=True)
    async def ha_remove(self, ctx: commands.Context, user_id: int, role_id: int):
        """Rimuove un ruolo da un membro usando gli ID."""
        member = await self._member(ctx, user_id)
        role = await self._role(ctx, role_id, allow_everyone=False)
        if member is None or role is None or not await self._can_edit_role(ctx, role):
            return
        if role not in member.roles:
            return await ctx.send("L'utente non possiede quel ruolo.")
        try:
            await member.remove_roles(role, reason=f"HierarchyAdmin: {ctx.author} ({ctx.author.id})")
            await ctx.send(f"Rimosso {role.mention} da {member.mention}.")
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send("Non sono riuscito a rimuovere il ruolo.")

    @ha.group(name="channelperm", aliases=["cp"], invoke_without_command=True)
    @commands.admin_or_permissions(administrator=True)
    async def ha_channelperm(self, ctx: commands.Context):
        """Gestisce allow/deny/inherit dei permessi specifici di un canale."""
        await ctx.send_help(ctx.command)

    @ha_channelperm.command(name="view")
    async def ha_channelperm_view(self, ctx: commands.Context, channel_id: int, target_id: int):
        """Mostra gli overwrite di un ruolo o membro nel canale."""
        channel = await self._channel(ctx, channel_id)
        if channel is None:
            return
        target = ctx.guild.get_role(target_id) or ctx.guild.get_member(target_id)
        if target is None:
            return await ctx.send("Target non trovato: usa ID ruolo o ID membro.")
        overwrite = channel.overwrites_for(target)
        lines = []
        for name in self._permission_names():
            value = getattr(overwrite, name, None)
            label = "✅ allow" if value is True else "❌ deny" if value is False else "➖ inherit"
            lines.append(f"{label} `{name}`")
        for index in range(0, len(lines), 25):
            await ctx.send(embed=discord.Embed(
                title=f"Permessi #{channel.name} - {getattr(target, 'name', target_id)}",
                description="\n".join(lines[index:index + 25]),
                colour=discord.Colour.blurple(),
            ))

    @ha_channelperm.command(name="set")
    async def ha_channelperm_set(self, ctx: commands.Context, channel_id: int, target_id: int, permission: str, state: str):
        """Imposta un overwrite `allow`, `deny` o `inherit` per ruolo/membro e canale."""
        channel = await self._channel(ctx, channel_id)
        if channel is None:
            return
        target = ctx.guild.get_role(target_id) or ctx.guild.get_member(target_id)
        if target is None:
            return await ctx.send("Target non trovato: usa ID ruolo o ID membro.")
        if isinstance(target, discord.Role) and not target.is_default() and not await self._can_edit_role(ctx, target):
            return
        permission = permission.lower().strip()
        if permission not in self._permission_names():
            return await ctx.send("Permesso non valido. Usa `.ha perm list`.")
        raw = state.lower().strip()
        if raw in TRUE_VALUES or raw == "allow":
            value = True
        elif raw in FALSE_VALUES or raw == "deny":
            value = False
        elif raw in INHERIT_VALUES:
            value = None
        else:
            return await ctx.send("Stato non valido. Usa `allow`, `deny` oppure `inherit`.")
        overwrite = channel.overwrites_for(target)
        setattr(overwrite, permission, value)
        try:
            await channel.set_permissions(target, overwrite=overwrite, reason=f"HierarchyAdmin: {ctx.author} ({ctx.author.id})")
            await ctx.send(f"`{permission}` impostato su **{raw}** per `{getattr(target, 'name', target_id)}` in {channel.mention}.")
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send("Discord ha rifiutato la modifica dei permessi del canale.")

    @ha_channelperm.command(name="clear")
    async def ha_channelperm_clear(self, ctx: commands.Context, channel_id: int, target_id: int, confirmation: str = ""):
        """Rimuove tutti gli overwrite del target dal canale; richiede `CONFERMO`."""
        channel = await self._channel(ctx, channel_id)
        if channel is None:
            return
        target = ctx.guild.get_role(target_id) or ctx.guild.get_member(target_id)
        if target is None:
            return await ctx.send("Target non trovato.")
        if confirmation.upper() != "CONFERMO":
            return await ctx.send(f"Conferma con `.ha channelperm clear {channel_id} {target_id} CONFERMO`.")
        try:
            await channel.set_permissions(target, overwrite=None, reason=f"HierarchyAdmin: {ctx.author} ({ctx.author.id})")
            await ctx.send("Overwrite del canale rimossi completamente.")
        except (discord.Forbidden, discord.HTTPException):
            await ctx.send("Non sono riuscito a rimuovere gli overwrite.")

    @ha.command(name="usage")
    @commands.admin_or_permissions(manage_roles=True)
    async def ha_usage(self, ctx: commands.Context):
        """Mostra una guida rapida ai comandi principali."""
        await ctx.send(
            "**HierarchyAdmin - guida rapida**\n"
            "`.ha tree` - gerarchia completa\n"
            "`.ha role ROLE_ID` - dettagli ruolo\n"
            "`.ha member USER_ID` - ruoli e permessi membro\n"
            "`.ha perm list` - nomi permessi validi\n"
            "`.ha perm view ROLE_ID` - tutti i permessi del ruolo\n"
            "`.ha perm set ROLE_ID PERMESSO true|false` - modifica singolo permesso\n"
            "`.ha perm all ROLE_ID true|false` - modifica tutti i permessi\n"
            "`.ha roleedit create NOME` - crea ruolo\n"
            "`.ha roleedit rename ROLE_ID NOME` - rinomina\n"
            "`.ha roleedit colour ROLE_ID #RRGGBB` - colore\n"
            "`.ha roleedit hoist ROLE_ID true|false` - separazione membri\n"
            "`.ha roleedit mentionable ROLE_ID true|false` - menzionabile\n"
            "`.ha roleedit position ROLE_ID POSIZIONE` - sposta\n"
            "`.ha roleedit delete ROLE_ID CONFERMO` - elimina\n"
            "`.ha give USER_ID ROLE_ID` / `.ha remove USER_ID ROLE_ID` - assegna/rimuove\n"
            "`.ha channelperm view CANALE_ID TARGET_ID` - overwrite canale\n"
            "`.ha channelperm set CANALE_ID TARGET_ID PERMESSO allow|deny|inherit` - modifica overwrite\n"
            "`.ha channelperm clear CANALE_ID TARGET_ID CONFERMO` - reset overwrite"
        )
