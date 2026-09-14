# Copyright 2018-present Jakub Kuczys (https://github.com/Jackenmen)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Italian adaptation for danyx64/dev-cogs. The original Config identifier,
# class name, folder name and persistent keys are intentionally preserved so
# existing Jackenmen/JackCogs NitroRole data can be reused.

import asyncio
import logging
import random
from string import Template
from typing import Any, Awaitable, Callable, Dict, Literal, Optional, Union, cast

import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.commands import GuildContext, NoParseOptional
from redbot.core.config import Config
from redbot.core.data_manager import cog_data_path
from redbot.core.utils.chat_formatting import box, pagify
from redbot.core.utils.predicates import MessagePredicate

from .guild_data import GuildData

log = logging.getLogger("red.jackcogs.nitrorole")

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]
ChannelType = Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel]


class NitroRole(commands.Cog):
    """Dai il benvenuto ai nuovi booster Nitro e/o assegna loro un ruolo speciale."""

    __author__ = "Jakub Kuczys / adattamento italiano danyx64"
    __version__ = "1.0.1-it"

    def __init__(self, bot: Red) -> None:
        self.bot = bot

        # IDENTIFIER E CHIAVI IDENTICI ALL'ORIGINALE JACKCOGS.
        self.config = Config.get_conf(
            self, identifier=176070082584248320, force_registration=True
        )
        self.config.register_guild(
            role_id=None,
            channel_id=None,
            message_templates=[],
            unassign_on_boost_end=False,
        )

        # Anche il percorso dati resta legato allo stesso cog NitroRole, cosi le
        # immagini gia salvate con il cog originale possono continuare a essere usate.
        self.message_images = cog_data_path(self) / "message_images"
        self.message_images.mkdir(parents=True, exist_ok=True)
        self.guild_cache: Dict[int, GuildData] = {}

    async def red_get_data_for_user(self, *, user_id: int) -> Dict[str, Any]:
        return {}

    async def red_delete_data_for_user(
        self, *, requester: RequestType, user_id: int
    ) -> None:
        return None

    async def get_guild_data(self, guild: discord.Guild) -> GuildData:
        try:
            return self.guild_cache[guild.id]
        except KeyError:
            pass

        settings = await self.config.guild(guild).all()
        data = GuildData(guild.id, self.config, **settings)
        self.guild_cache[guild.id] = data
        return data

    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    @commands.group(name="nitrorole", aliases=["nitroboost"], invoke_without_command=True)
    async def nitrorole(self, ctx: GuildContext) -> None:
        """Configura ruolo e messaggi per i booster Nitro."""
        await ctx.send_help(ctx.command)

    @nitrorole.command(
        name="unassignonboostend",
        aliases=["rimuoviallafine", "removeonboostend"],
    )
    async def nitrorole_unassign_on_boost_end(
        self, ctx: GuildContext, enabled: NoParseOptional[bool] = None
    ) -> None:
        """Sceglie se rimuovere il ruolo quando un utente smette di boostare."""
        guild_data = await self.get_guild_data(ctx.guild)

        if enabled is None:
            if guild_data.unassign_on_boost_end:
                await ctx.send(
                    "✅ Il ruolo booster viene rimosso quando un utente smette di boostare il server."
                )
            else:
                await ctx.send(
                    "⏸️ Il ruolo booster non viene rimosso quando un utente smette di boostare il server."
                )
            return

        await guild_data.set_unassign_on_boost_end(enabled)
        if enabled:
            await ctx.send(
                "✅ Da ora rimuovero il ruolo booster quando un utente smette di boostare il server."
            )
        else:
            await ctx.send(
                "✅ Da ora non rimuovero il ruolo booster quando un utente smette di boostare il server."
            )

    @nitrorole.command(name="autoassignrole", aliases=["ruolo", "role"])
    async def nitrorole_autoassignrole(
        self, ctx: GuildContext, *, role: NoParseOptional[discord.Role] = None
    ) -> None:
        """Imposta il ruolo da assegnare automaticamente a chi inizia a boostare."""
        guild = ctx.guild
        guild_data = await self.get_guild_data(guild)

        if role is None:
            await guild_data.set_role(None)
            await ctx.send("✅ Il ruolo non verra piu assegnato automaticamente ai booster.")
            return

        if role.is_default() or role.managed:
            await ctx.send("❌ Quel ruolo non puo essere assegnato manualmente dal bot.")
            return

        if (
            guild.owner_id != ctx.author.id
            and role >= ctx.author.top_role
            and not await self.bot.is_owner(ctx.author)
        ):
            await ctx.send("❌ Non puoi configurare un ruolo superiore al tuo ruolo piu alto.")
            return

        me = guild.me
        if me is None or not me.guild_permissions.manage_roles or role >= me.top_role:
            await ctx.send(
                "❌ Non posso assegnare quel ruolo: mi serve **Gestisci ruoli** e il mio ruolo deve stare sopra."
            )
            return

        await guild_data.set_role(role)
        await ctx.send(f"✅ I nuovi booster riceveranno automaticamente {role.mention}.")

    @nitrorole.command(name="channel", aliases=["canale"])
    async def nitrorole_channel(
        self,
        ctx: GuildContext,
        channel: NoParseOptional[ChannelType] = None,
    ) -> None:
        """Imposta il canale dei messaggi booster. Senza canale li disattiva."""
        guild_data = await self.get_guild_data(ctx.guild)
        await guild_data.set_channel(channel)

        if channel is None:
            await ctx.send("✅ Messaggi di benvenuto per i nuovi booster disattivati.")
            return

        await ctx.send(
            f"✅ I messaggi per i nuovi booster verranno inviati in {channel.mention}."
        )

    @nitrorole.command(name="addmessage", aliases=["aggiungimessaggio"])
    async def nitrorole_addmessage(self, ctx: GuildContext, *, message: str) -> None:
        """
        Aggiunge un messaggio casuale per i nuovi booster.

        Placeholder disponibili:
        $mention  - menzione del booster
        $username - nome visualizzato del booster
        $server   - nome del server
        $count    - numero totale di boost del server
        $plural   - vuoto se count e 1, altrimenti "s" (compatibilita originale)
        """
        guild = ctx.guild
        guild_data = await self.get_guild_data(guild)
        template = await guild_data.add_message(message)
        content = template.safe_substitute(
            mention=ctx.author.mention,
            username=ctx.author.display_name,
            server=guild.name,
            count="2",
            plural="s",
        )

        filename = next(self.message_images.glob(f"{guild.id}.*"), None)
        file = None
        warning = ""

        if filename is not None:
            channel_id = guild_data.channel_id
            target = guild.get_channel(channel_id) if channel_id is not None else None
            if target is not None:
                me = guild.me
                if me is None or not target.permissions_for(me).attach_files:
                    warning = (
                        "⚠️ Nel canale dei booster non ho il permesso **Allega file**, quindi l'immagine non verra inviata.\n\n"
                    )

            me = guild.me
            if me is None or not ctx.channel.permissions_for(me).attach_files:
                await ctx.send(
                    f"{warning}✅ Messaggio booster aggiunto. Non posso mostrare qui l'anteprima dell'immagine perche mi manca **Allega file**."
                )
                return
            file = discord.File(str(filename))

        await ctx.send(f"{warning}✅ Messaggio booster aggiunto. Anteprima:")
        await ctx.send(content, file=file)

    @nitrorole.command(
        name="removemessage",
        aliases=["deletemessage", "rimuovimessaggio", "eliminamessaggio"],
    )
    async def nitrorole_removemessage(self, ctx: GuildContext) -> None:
        """Rimuove uno dei messaggi booster configurati."""
        guild_data = await self.get_guild_data(ctx.guild)
        if not guild_data.messages:
            await ctx.send("❌ Non ci sono messaggi booster configurati.")
            return

        text = "Scegli il numero del messaggio booster da eliminare:\n\n"
        for index, template in enumerate(guild_data.messages, 1):
            text += f"  {index}. {template}\n"

        for page in pagify(text):
            await ctx.send(box(page))

        pred = MessagePredicate.valid_int(ctx)
        try:
            await self.bot.wait_for(
                "message",
                check=lambda m: pred(m) and cast(int, pred.result) >= 1,
                timeout=30,
            )
        except asyncio.TimeoutError:
            await ctx.send("⌛ Tempo scaduto: nessun messaggio eliminato.")
            return

        result = cast(int, pred.result)
        try:
            await guild_data.remove_message(result - 1)
        except IndexError:
            await ctx.send("❌ Quel numero non corrisponde a nessun messaggio.")
            return

        await ctx.send("✅ Messaggio booster eliminato.")

    @nitrorole.command(name="listmessages", aliases=["messaggi", "listamessaggi"])
    async def nitrorole_listmessages(self, ctx: GuildContext) -> None:
        """Mostra tutti i template dei messaggi booster."""
        guild_data = await self.get_guild_data(ctx.guild)
        if not guild_data.messages:
            await ctx.send("❌ Non ci sono template di messaggi booster configurati.")
            return

        text = "Template messaggi booster:\n\n"
        for index, template in enumerate(guild_data.messages, 1):
            text += f"  {index}. {template}\n"

        for page in pagify(text):
            await ctx.send(box(page))

    @nitrorole.command(name="setimage", aliases=["immagine"])
    async def nitrorole_setimage(self, ctx: GuildContext) -> None:
        """Imposta l'immagine allegata ai messaggi dei nuovi booster."""
        guild = ctx.guild
        if len(ctx.message.attachments) != 1:
            await ctx.send("❌ Devi allegare esattamente una immagine al comando.")
            return

        attachment = ctx.message.attachments[0]
        if attachment.width is None:
            await ctx.send("❌ L'allegato deve essere una immagine.")
            return

        ext = attachment.filename.rpartition(".")[2] or "png"
        filename = self.message_images / f"{guild.id}.{ext}"
        with open(filename, "wb") as fp:
            await attachment.save(fp)

        for old_file in self.message_images.glob(f"{guild.id}.*"):
            if old_file != filename:
                old_file.unlink()

        guild_data = await self.get_guild_data(guild)
        target = (
            guild.get_channel(guild_data.channel_id)
            if guild_data.channel_id is not None
            else None
        )
        me = guild.me
        if target is not None and (
            me is None or not target.permissions_for(me).attach_files
        ):
            await ctx.send(
                "⚠️ Immagine salvata, ma nel canale dei booster mi manca il permesso **Allega file**."
            )
        else:
            await ctx.send("✅ Immagine booster impostata.")

    @nitrorole.command(name="unsetimage", aliases=["rimuoviimmagine"])
    async def nitrorole_unsetimage(self, ctx: GuildContext) -> None:
        """Rimuove l'immagine dei messaggi booster."""
        for file in self.message_images.glob(f"{ctx.guild.id}.*"):
            file.unlink()
        await ctx.send("✅ Immagine booster rimossa.")

    @nitrorole.command(
        name="settings",
        aliases=["show", "showsettings", "setting", "status", "stato", "impostazioni"],
    )
    async def nitrorole_settings(self, ctx: GuildContext) -> None:
        """Mostra tutte le impostazioni NitroRole del server."""
        guild = ctx.guild
        guild_data = await self.get_guild_data(guild)

        role = guild.get_role(guild_data.role_id) if guild_data.role_id is not None else None
        channel = (
            guild.get_channel(guild_data.channel_id)
            if guild_data.channel_id is not None
            else None
        )
        image = next(self.message_images.glob(f"{guild.id}.*"), None)

        embed = discord.Embed(title="🚀 NitroRole - Stato", color=discord.Color.purple())
        embed.add_field(
            name="Messaggi booster",
            value="✅ Attivi" if guild_data.channel_id is not None else "⏸️ Disattivati",
            inline=True,
        )
        embed.add_field(
            name="Ruolo automatico",
            value=role.mention if role is not None else "Nessuno",
            inline=True,
        )
        embed.add_field(
            name="Canale messaggi",
            value=channel.mention if channel is not None else "Nessuno",
            inline=True,
        )
        embed.add_field(
            name="Rimuovi ruolo a fine boost",
            value="✅ Si" if guild_data.unassign_on_boost_end else "❌ No",
            inline=True,
        )
        embed.add_field(
            name="Template configurati",
            value=str(len(guild_data.messages)),
            inline=True,
        )
        embed.add_field(
            name="Immagine",
            value="✅ Configurata" if image is not None else "Nessuna",
            inline=True,
        )
        embed.add_field(name="Versione", value=self.__version__, inline=True)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @nitrorole.command(name="version", aliases=["versione"])
    async def nitrorole_version(self, ctx: GuildContext) -> None:
        await ctx.send(f"NitroRole **v{self.__version__}**")

    async def cog_disabled_in_guild(self, guild: Optional[discord.Guild]) -> bool:
        func: Optional[
            Callable[[commands.Cog, Optional[discord.Guild]], Awaitable[bool]]
        ] = getattr(self.bot, "cog_disabled_in_guild", None)
        if func is None:
            return False
        return await func(self, guild)

    @commands.Cog.listener()
    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        if before.premium_since == after.premium_since:
            return

        if await self.cog_disabled_in_guild(after.guild):
            return

        guild_data = await self.get_guild_data(after.guild)
        if before.premium_since is None and after.premium_since is not None:
            await self.maybe_assign_role(guild_data, after)
            await self.maybe_announce(guild_data, after)
        elif before.premium_since is not None and after.premium_since is None:
            await self.maybe_unassign_role(guild_data, after)

    def get_role_to_assign(
        self, guild: discord.Guild, guild_data: GuildData
    ) -> Optional[discord.Role]:
        role_id = guild_data.role_id
        if role_id is None:
            return None

        role = guild.get_role(role_id)
        if role is None:
            log.error(
                "NitroRole: ruolo %s non trovato nel server %s.", role_id, guild.id
            )
            return None

        me = guild.me
        if me is None or not me.guild_permissions.manage_roles or role >= me.top_role:
            log.error(
                "NitroRole: il bot non puo gestire il ruolo %s nel server %s.",
                role_id,
                guild.id,
            )
            return None
        return role

    async def maybe_assign_role(
        self, guild_data: GuildData, member: discord.Member
    ) -> None:
        role = self.get_role_to_assign(member.guild, guild_data)
        if role is None or member.get_role(role.id) is not None:
            return
        try:
            await member.add_roles(role, reason="NitroRole: nuovo booster")
        except (discord.Forbidden, discord.HTTPException):
            log.exception(
                "NitroRole: impossibile assegnare il ruolo %s al membro %s.",
                role.id,
                member.id,
            )

    async def maybe_unassign_role(
        self, guild_data: GuildData, member: discord.Member
    ) -> None:
        if not guild_data.unassign_on_boost_end:
            return

        role = self.get_role_to_assign(member.guild, guild_data)
        if role is None or member.get_role(role.id) is None:
            return
        try:
            await member.remove_roles(role, reason="NitroRole: boost terminato")
        except (discord.Forbidden, discord.HTTPException):
            log.exception(
                "NitroRole: impossibile rimuovere il ruolo %s al membro %s.",
                role.id,
                member.id,
            )

    async def maybe_announce(
        self, guild_data: GuildData, member: discord.Member
    ) -> None:
        channel_id = guild_data.channel_id
        if channel_id is None:
            return

        guild = member.guild
        channel = cast(Optional[ChannelType], guild.get_channel(channel_id))
        if channel is None:
            log.error(
                "NitroRole: canale %s non trovato nel server %s.", channel_id, guild.id
            )
            return

        templates = guild_data.message_templates
        if not templates:
            return

        count = guild.premium_subscription_count or 0
        template: Template = random.choice(templates)
        content = template.safe_substitute(
            mention=member.mention,
            username=member.display_name,
            server=guild.name,
            count=str(count),
            plural="" if count == 1 else "s",
        )

        kwargs = {}
        filename = next(self.message_images.glob(f"{guild.id}.*"), None)
        me = guild.me
        if filename is not None and me is not None and channel.permissions_for(me).attach_files:
            kwargs["file"] = discord.File(str(filename))

        try:
            await channel.send(content, **kwargs)
        except (discord.Forbidden, discord.HTTPException):
            log.exception(
                "NitroRole: impossibile inviare il messaggio booster nel canale %s del server %s.",
                channel_id,
                guild.id,
            )
