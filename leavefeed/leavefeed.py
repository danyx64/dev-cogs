import asyncio
import re
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


ITALY_TZ = ZoneInfo("Europe/Rome")
KEY_RE = re.compile(r"^[a-z0-9_-]{1,24}$")
STYLE_MAP = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}


class LeaveFeedButton(discord.ui.Button):
    def __init__(self, cog: "LeaveFeed", guild_id: int, message_key: str, button_key: str, data: Dict):
        self.cog = cog
        self.guild_id = guild_id
        self.message_key = message_key
        self.button_key = button_key
        super().__init__(
            label=str(data.get("label") or button_key)[:80],
            style=STYLE_MAP.get(str(data.get("style", "primary")).lower(), discord.ButtonStyle.primary),
            custom_id=f"leavefeed:{guild_id}:{message_key}:{button_key}"[:100],
        )

    async def callback(self, interaction: discord.Interaction):
        await self.cog.open_modal(interaction, self.guild_id, self.button_key)


class LeaveFeedView(discord.ui.View):
    def __init__(self, cog: "LeaveFeed", guild_id: int, message_key: str, message_data: Dict, buttons: Dict):
        super().__init__(timeout=None)
        for button_key in list(message_data.get("buttons", []))[:25]:
            data = buttons.get(button_key)
            if data:
                self.add_item(LeaveFeedButton(cog, guild_id, message_key, button_key, data))


class LeaveFeedModal(discord.ui.Modal):
    def __init__(self, cog: "LeaveFeed", guild_id: int, modal_key: str, data: Dict):
        super().__init__(title=str(data.get("title") or "Feedback")[:45], timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.modal_key = modal_key
        self.inputs: List[discord.ui.TextInput] = []

        for index, question in enumerate(list(data.get("questions", []))[:5]):
            style = discord.TextStyle.short if str(question.get("style", "long")).lower() == "short" else discord.TextStyle.paragraph
            min_length = int(question.get("min_length", 0) or 0)
            max_length = max(1, min(int(question.get("max_length", 1000) or 1000), 4000))
            item = discord.ui.TextInput(
                label=str(question.get("label") or f"Domanda {index + 1}")[:45],
                placeholder=(str(question.get("placeholder"))[:100] if question.get("placeholder") else None),
                style=style,
                required=bool(question.get("required", True)),
                min_length=(min_length if min_length > 0 else None),
                max_length=max_length,
                custom_id=f"q{index}",
            )
            self.inputs.append(item)
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        answers = [str(item.value or "").strip() for item in self.inputs]
        ok, response = await self.cog.submit_feedback(
            self.guild_id, self.modal_key, interaction.user, answers
        )
        await interaction.response.send_message(
            response if ok else "Non sono riuscito a consegnare il feedback allo staff."
        )


class LeaveFeed(commands.Cog):
    """Invia un DM configurabile a chi lascia volontariamente il server."""

    __author__ = "danyx64"
    __version__ = "2.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=742951603118804261, force_registration=True)
        self.config.register_guild(
            enabled=False,
            feedback_channel_id=None,
            message="Ci dispiace vederti andare da {server}. Se vuoi, lasciaci un feedback.",
            modals={},
            messages={},
            buttons={},
            active_message=None,
        )

    async def cog_load(self):
        for guild_id in (await self.config.all_guilds()).keys():
            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                continue
            data = await self._ensure_schema(guild)
            for message_key, message_data in data["messages"].items():
                if message_data.get("buttons"):
                    self.bot.add_view(
                        LeaveFeedView(self, guild.id, message_key, message_data, data["buttons"])
                    )

    async def _ensure_schema(self, guild: discord.Guild) -> Dict:
        data = await self.config.guild(guild).all()
        messages = dict(data.get("messages") or {})
        buttons = dict(data.get("buttons") or {})
        modals = dict(data.get("modals") or {})
        active_message = data.get("active_message")

        changed = False
        if not messages:
            messages = {
                "default": {
                    "content": data.get("message") or "Ci dispiace vederti andare da {server}. Se vuoi, lasciaci un feedback.",
                    "buttons": [],
                }
            }
            changed = True

        # Migrazione automatica dalla vecchia configurazione in cui ogni modal
        # conteneva anche il proprio pulsante.
        if modals and not buttons:
            for modal_key, modal_data in modals.items():
                buttons[modal_key] = {
                    "label": modal_data.get("button_label") or modal_key,
                    "style": modal_data.get("button_style") or "primary",
                    "modal": modal_key,
                }
                messages.setdefault("default", {"content": data.get("message") or "", "buttons": []})
                if modal_key not in messages["default"].setdefault("buttons", []):
                    messages["default"]["buttons"].append(modal_key)
                modal_data.setdefault("submit_message", "Grazie per il feedback.")
            changed = True

        for modal_data in modals.values():
            if "submit_message" not in modal_data:
                modal_data["submit_message"] = "Grazie per il feedback."
                changed = True

        if not active_message or active_message not in messages:
            active_message = next(iter(messages), "default")
            changed = True

        if changed:
            await self.config.guild(guild).messages.set(messages)
            await self.config.guild(guild).buttons.set(buttons)
            await self.config.guild(guild).modals.set(modals)
            await self.config.guild(guild).active_message.set(active_message)

        data.update(messages=messages, buttons=buttons, modals=modals, active_message=active_message)
        return data

    @staticmethod
    def _clean(value, limit=900):
        text = str(value or "—").strip() or "—"
        return text if len(text) <= limit else text[: limit - 1] + "…"

    @staticmethod
    def _valid_key(key: str) -> bool:
        return bool(KEY_RE.fullmatch(key.lower()))

    @staticmethod
    def _render_message(template: str, user, guild: discord.Guild) -> str:
        return (
            str(template)
            .replace("{user}", getattr(user, "name", str(user)))
            .replace("{server}", guild.name)
        )

    async def _send_leave_dm(self, user, guild: discord.Guild) -> bool:
        data = await self._ensure_schema(guild)
        if not data.get("enabled"):
            return False

        message_key = data.get("active_message")
        message_data = data["messages"].get(message_key)
        if not message_data:
            return False

        content = self._render_message(message_data.get("content") or "", user, guild)
        view = None
        if message_data.get("buttons"):
            view = LeaveFeedView(self, guild.id, message_key, message_data, data["buttons"])

        try:
            await user.send(
                content=content or None,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def _was_kicked_or_banned(self, member: discord.Member) -> bool:
        guild = member.guild
        me = guild.me
        if me is None or not me.guild_permissions.view_audit_log:
            return False

        for delay in (0.30, 0.70, 1.20):
            await asyncio.sleep(delay)
            now = discord.utils.utcnow()
            for action in (discord.AuditLogAction.kick, discord.AuditLogAction.ban):
                try:
                    async for entry in guild.audit_logs(limit=6, action=action):
                        if getattr(getattr(entry, "target", None), "id", None) != member.id:
                            continue
                        age = (now - entry.created_at).total_seconds()
                        if 0 <= age <= 12:
                            return True
                except (discord.Forbidden, discord.HTTPException):
                    return False
        return False

    async def open_modal(self, interaction: discord.Interaction, guild_id: int, button_key: str):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return await interaction.response.send_message("Questo server non è più disponibile.")

        data = await self._ensure_schema(guild)
        button = data["buttons"].get(button_key)
        if not button:
            return await interaction.response.send_message("Questo pulsante non è più configurato.")
        modal_key = button.get("modal")
        modal = data["modals"].get(modal_key)
        if not modal:
            return await interaction.response.send_message("Il modal collegato non è più disponibile.")
        if not modal.get("questions"):
            return await interaction.response.send_message("Questo modal non ha ancora domande configurate.")

        await interaction.response.send_modal(LeaveFeedModal(self, guild.id, modal_key, modal))

    async def submit_feedback(self, guild_id: int, modal_key: str, user, answers: List[str]):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return False, ""

        data = await self._ensure_schema(guild)
        channel_id = data.get("feedback_channel_id")
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return False, ""

        modal = data["modals"].get(modal_key) or {}
        questions = modal.get("questions") or []
        now = discord.utils.utcnow().astimezone(ITALY_TZ)

        lines = [
            f"**Utente:** <@{user.id}>",
            f"**Data:** {now.strftime('%d/%m/%Y')}",
            f"**Ora:** {now.strftime('%H:%M:%S')}",
            f"**ID:** `{user.id}`",
            "**Motivo:**",
        ]
        for index, answer in enumerate(answers):
            label = questions[index].get("label") if index < len(questions) else f"Domanda {index + 1}"
            lines.append(f"**{self._clean(label, 80)}:** {self._clean(answer)}")

        try:
            await channel.send(
                embed=discord.Embed(description="\n".join(lines)[:4096], colour=discord.Colour.blurple()),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True, str(modal.get("submit_message") or "Grazie per il feedback.")[:1900]
        except (discord.Forbidden, discord.HTTPException):
            return False, ""

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Invia il feedback solo in caso di uscita volontaria, mai su kick o ban."""
        if member.bot:
            return
        if await self._was_kicked_or_banned(member):
            return
        await self._send_leave_dm(member, member.guild)

    @commands.group(name="leavefeed", invoke_without_command=True)
    @commands.guild_only()
    async def leavefeed(self, ctx: commands.Context):
        """Gestisce messaggi, pulsanti, modal e impostazioni di LeaveFeed."""
        await ctx.send_help(ctx.command)

    @leavefeed.command(name="setchannel")
    @commands.admin_or_permissions(manage_guild=True)
    async def setchannel(self, ctx: commands.Context, channel_id: int):
        """Imposta tramite ID il canale in cui ricevere le risposte dei modal."""
        channel = ctx.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send("Non trovo un canale testuale con questo ID.")
        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            return await ctx.send("Mi servono Visualizza canale, Invia messaggi e Incorpora link in quel canale.")
        await self.config.guild(ctx.guild).feedback_channel_id.set(channel.id)
        await ctx.send(f"Canale feedback impostato su {channel.mention} (`{channel.id}`).")

    @leavefeed.command(name="enable")
    @commands.admin_or_permissions(manage_guild=True)
    async def enable(self, ctx: commands.Context):
        """Abilita l'invio automatico dei DM a chi lascia volontariamente il server."""
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("LeaveFeed abilitato.")

    @leavefeed.command(name="disable")
    @commands.admin_or_permissions(manage_guild=True)
    async def disable(self, ctx: commands.Context):
        """Disabilita completamente l'invio automatico dei DM LeaveFeed."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("LeaveFeed disabilitato.")

    @leavefeed.command(name="status")
    @commands.admin_or_permissions(manage_guild=True)
    async def status(self, ctx: commands.Context):
        """Mostra stato, canale, messaggio attivo e quantità di elementi configurati."""
        data = await self._ensure_schema(ctx.guild)
        channel = ctx.guild.get_channel(data.get("feedback_channel_id")) if data.get("feedback_channel_id") else None
        await ctx.send(
            f"Stato: **{'attivo' if data.get('enabled') else 'disattivato'}**\n"
            f"Canale: {channel.mention if channel else '—'}\n"
            f"Messaggio attivo: `{data.get('active_message') or '—'}`\n"
            f"Messaggi: **{len(data['messages'])}** | Pulsanti: **{len(data['buttons'])}** | Modali: **{len(data['modals'])}**"
        )

    @leavefeed.command(name="test")
    @commands.admin_or_permissions(manage_guild=True)
    async def test(self, ctx: commands.Context):
        """Invia a te stesso il messaggio LeaveFeed attivo per provarlo senza uscire."""
        ok = await self._send_leave_dm(ctx.author, ctx.guild)
        await ctx.send("DM di test inviato." if ok else "Non sono riuscito a mandarti il DM di test.")

    @leavefeed.group(name="message", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def message_group(self, ctx: commands.Context):
        """Crea, visualizza, modifica, elimina e seleziona i messaggi DM."""
        await ctx.send_help(ctx.command)

    @message_group.command(name="add")
    async def message_add(self, ctx: commands.Context, key: str, *, content: str):
        """Crea un nuovo messaggio DM: .leavefeed message add CHIAVE TESTO."""
        key = key.lower()
        if not self._valid_key(key):
            return await ctx.send("Chiave non valida: usa lettere minuscole, numeri, `_` o `-`, massimo 24 caratteri.")
        if not 1 <= len(content) <= 1900:
            return await ctx.send("Il testo deve essere lungo da 1 a 1900 caratteri.")
        async with self.config.guild(ctx.guild).messages() as messages:
            if key in messages:
                return await ctx.send("Esiste già un messaggio con questa chiave.")
            messages[key] = {"content": content, "buttons": []}
        await ctx.send(f"Messaggio `{key}` creato.")

    @message_group.command(name="list")
    async def message_list(self, ctx: commands.Context):
        """Elenca tutti i messaggi configurati e indica quale viene inviato all'uscita."""
        data = await self._ensure_schema(ctx.guild)
        active = data.get("active_message")
        lines = []
        for key, msg in data["messages"].items():
            marker = " **[ATTIVO]**" if key == active else ""
            lines.append(f"`{key}`{marker} — {len(msg.get('buttons', []))} pulsanti — {self._clean(msg.get('content'), 120)}")
        await ctx.send("\n".join(lines) if lines else "Nessun messaggio configurato.")

    @message_group.command(name="view")
    async def message_view(self, ctx: commands.Context, key: str):
        """Mostra per intero un messaggio e i pulsanti collegati."""
        data = await self._ensure_schema(ctx.guild)
        msg = data["messages"].get(key.lower())
        if not msg:
            return await ctx.send("Messaggio non trovato.")
        buttons = msg.get("buttons", [])
        button_lines = []
        for button_key in buttons:
            button = data["buttons"].get(button_key, {})
            button_lines.append(f"`{button_key}` → modal `{button.get('modal', '—')}` → {button.get('label', button_key)}")
        await ctx.send(
            f"**Messaggio:** `{key.lower()}`\n"
            f"**Testo:**\n{self._clean(msg.get('content'), 1800)}\n\n"
            f"**Pulsanti:**\n" + ("\n".join(button_lines) if button_lines else "—")
        )

    @message_group.command(name="edit")
    async def message_edit(self, ctx: commands.Context, key: str, *, content: str):
        """Modifica il testo di un messaggio esistente."""
        if not 1 <= len(content) <= 1900:
            return await ctx.send("Il testo deve essere lungo da 1 a 1900 caratteri.")
        async with self.config.guild(ctx.guild).messages() as messages:
            msg = messages.get(key.lower())
            if not msg:
                return await ctx.send("Messaggio non trovato.")
            msg["content"] = content
        await ctx.send(f"Messaggio `{key.lower()}` aggiornato.")

    @message_group.command(name="use")
    async def message_use(self, ctx: commands.Context, key: str):
        """Sceglie quale messaggio viene inviato automaticamente quando un utente esce."""
        messages = await self.config.guild(ctx.guild).messages()
        if key.lower() not in messages:
            return await ctx.send("Messaggio non trovato.")
        await self.config.guild(ctx.guild).active_message.set(key.lower())
        await ctx.send(f"Messaggio attivo impostato su `{key.lower()}`.")

    @message_group.command(name="remove")
    async def message_remove(self, ctx: commands.Context, key: str):
        """Elimina un messaggio DM; non permette di eliminare l'unico messaggio rimasto."""
        key = key.lower()
        async with self.config.guild(ctx.guild).messages() as messages:
            if key not in messages:
                return await ctx.send("Messaggio non trovato.")
            if len(messages) <= 1:
                return await ctx.send("Deve rimanere almeno un messaggio configurato.")
            del messages[key]
            remaining = next(iter(messages))
        if await self.config.guild(ctx.guild).active_message() == key:
            await self.config.guild(ctx.guild).active_message.set(remaining)
        await ctx.send(f"Messaggio `{key}` eliminato.")

    @leavefeed.group(name="button", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def button_group(self, ctx: commands.Context):
        """Crea pulsanti, collega un modal e assegna i pulsanti ai messaggi DM."""
        await ctx.send_help(ctx.command)

    @button_group.command(name="add")
    async def button_add(self, ctx: commands.Context, key: str, modal_key: str, *, label: str):
        """Crea un pulsante e lo collega a un modal: .leavefeed button add CHIAVE MODAL TESTO."""
        key, modal_key = key.lower(), modal_key.lower()
        if not self._valid_key(key):
            return await ctx.send("Chiave pulsante non valida.")
        if not 1 <= len(label) <= 80:
            return await ctx.send("Il testo del pulsante deve essere lungo da 1 a 80 caratteri.")
        data = await self._ensure_schema(ctx.guild)
        if modal_key not in data["modals"]:
            return await ctx.send("Modal non trovato: crealo prima con `.leavefeed modal add`.")
        async with self.config.guild(ctx.guild).buttons() as buttons:
            if key in buttons:
                return await ctx.send("Esiste già un pulsante con questa chiave.")
            if len(buttons) >= 25:
                return await ctx.send("Puoi configurare al massimo 25 pulsanti.")
            buttons[key] = {"label": label, "style": "primary", "modal": modal_key}
        await ctx.send(f"Pulsante `{key}` creato e collegato al modal `{modal_key}`.")

    @button_group.command(name="list")
    async def button_list(self, ctx: commands.Context):
        """Elenca tutti i pulsanti, il loro stile e il modal che aprono."""
        data = await self._ensure_schema(ctx.guild)
        if not data["buttons"]:
            return await ctx.send("Nessun pulsante configurato.")
        lines = [
            f"`{key}` — {button.get('label', key)} — `{button.get('style', 'primary')}` — modal `{button.get('modal', '—')}`"
            for key, button in data["buttons"].items()
        ]
        await ctx.send("\n".join(lines))

    @button_group.command(name="view")
    async def button_view(self, ctx: commands.Context, key: str):
        """Mostra configurazione e messaggi a cui è assegnato un pulsante."""
        data = await self._ensure_schema(ctx.guild)
        button = data["buttons"].get(key.lower())
        if not button:
            return await ctx.send("Pulsante non trovato.")
        assigned = [mkey for mkey, msg in data["messages"].items() if key.lower() in msg.get("buttons", [])]
        await ctx.send(
            f"**Pulsante:** `{key.lower()}`\n"
            f"**Testo:** {button.get('label', '—')}\n"
            f"**Stile:** `{button.get('style', 'primary')}`\n"
            f"**Modal:** `{button.get('modal', '—')}`\n"
            f"**Messaggi:** {', '.join(f'`{x}`' for x in assigned) if assigned else '—'}"
        )

    @button_group.command(name="assign")
    async def button_assign(self, ctx: commands.Context, button_key: str, message_key: str):
        """Assegna un pulsante a un messaggio DM."""
        button_key, message_key = button_key.lower(), message_key.lower()
        data = await self._ensure_schema(ctx.guild)
        if button_key not in data["buttons"]:
            return await ctx.send("Pulsante non trovato.")
        async with self.config.guild(ctx.guild).messages() as messages:
            msg = messages.get(message_key)
            if not msg:
                return await ctx.send("Messaggio non trovato.")
            assigned = msg.setdefault("buttons", [])
            if button_key in assigned:
                return await ctx.send("Questo pulsante è già assegnato al messaggio.")
            if len(assigned) >= 25:
                return await ctx.send("Un messaggio può avere al massimo 25 pulsanti.")
            assigned.append(button_key)
        await ctx.send(f"Pulsante `{button_key}` assegnato al messaggio `{message_key}`.")

    @button_group.command(name="unassign")
    async def button_unassign(self, ctx: commands.Context, button_key: str, message_key: str):
        """Rimuove un pulsante da uno specifico messaggio senza eliminare il pulsante."""
        button_key, message_key = button_key.lower(), message_key.lower()
        async with self.config.guild(ctx.guild).messages() as messages:
            msg = messages.get(message_key)
            if not msg:
                return await ctx.send("Messaggio non trovato.")
            assigned = msg.setdefault("buttons", [])
            if button_key not in assigned:
                return await ctx.send("Questo pulsante non è assegnato al messaggio.")
            assigned.remove(button_key)
        await ctx.send(f"Pulsante `{button_key}` rimosso dal messaggio `{message_key}`.")

    @button_group.command(name="label")
    async def button_label(self, ctx: commands.Context, key: str, *, label: str):
        """Modifica il testo visualizzato su un pulsante."""
        if not 1 <= len(label) <= 80:
            return await ctx.send("Il testo del pulsante deve essere lungo da 1 a 80 caratteri.")
        async with self.config.guild(ctx.guild).buttons() as buttons:
            button = buttons.get(key.lower())
            if not button:
                return await ctx.send("Pulsante non trovato.")
            button["label"] = label
        await ctx.send("Testo del pulsante aggiornato.")

    @button_group.command(name="style")
    async def button_style(self, ctx: commands.Context, key: str, style: str):
        """Modifica il colore/stile del pulsante: primary, secondary, success o danger."""
        style = style.lower()
        if style not in STYLE_MAP:
            return await ctx.send("Stili validi: `primary`, `secondary`, `success`, `danger`.")
        async with self.config.guild(ctx.guild).buttons() as buttons:
            button = buttons.get(key.lower())
            if not button:
                return await ctx.send("Pulsante non trovato.")
            button["style"] = style
        await ctx.send("Stile del pulsante aggiornato.")

    @button_group.command(name="modal")
    async def button_modal(self, ctx: commands.Context, key: str, modal_key: str):
        """Cambia il modal aperto da un pulsante esistente."""
        modal_key = modal_key.lower()
        data = await self._ensure_schema(ctx.guild)
        if modal_key not in data["modals"]:
            return await ctx.send("Modal non trovato.")
        async with self.config.guild(ctx.guild).buttons() as buttons:
            button = buttons.get(key.lower())
            if not button:
                return await ctx.send("Pulsante non trovato.")
            button["modal"] = modal_key
        await ctx.send(f"Il pulsante `{key.lower()}` ora apre il modal `{modal_key}`.")

    @button_group.command(name="remove")
    async def button_remove(self, ctx: commands.Context, key: str):
        """Elimina un pulsante e lo rimuove automaticamente da tutti i messaggi."""
        key = key.lower()
        async with self.config.guild(ctx.guild).buttons() as buttons:
            if key not in buttons:
                return await ctx.send("Pulsante non trovato.")
            del buttons[key]
        async with self.config.guild(ctx.guild).messages() as messages:
            for msg in messages.values():
                if key in msg.get("buttons", []):
                    msg["buttons"].remove(key)
        await ctx.send(f"Pulsante `{key}` eliminato.")

    @leavefeed.group(name="modal", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def modal_group(self, ctx: commands.Context):
        """Crea e modifica modal, titolo, risposta finale e domande."""
        await ctx.send_help(ctx.command)

    @modal_group.command(name="add")
    async def modal_add(self, ctx: commands.Context, key: str, *, title: str):
        """Crea un modal vuoto con un titolo personalizzato."""
        key = key.lower()
        if not self._valid_key(key):
            return await ctx.send("Chiave modal non valida.")
        if not 1 <= len(title) <= 45:
            return await ctx.send("Il titolo deve essere lungo da 1 a 45 caratteri.")
        async with self.config.guild(ctx.guild).modals() as modals:
            if key in modals:
                return await ctx.send("Esiste già un modal con questa chiave.")
            modals[key] = {"title": title, "submit_message": "Grazie per il feedback.", "questions": []}
        await ctx.send(f"Modal `{key}` creato.")

    @modal_group.command(name="list")
    async def modal_list(self, ctx: commands.Context):
        """Elenca tutti i modal configurati con titolo e numero di domande."""
        modals = (await self._ensure_schema(ctx.guild))["modals"]
        if not modals:
            return await ctx.send("Nessun modal configurato.")
        await ctx.send("\n".join(
            f"`{key}` — {modal.get('title', key)} — {len(modal.get('questions', []))} domande"
            for key, modal in modals.items()
        ))

    @modal_group.command(name="view")
    async def modal_view(self, ctx: commands.Context, key: str):
        """Mostra titolo, risposta finale e tutte le domande di un modal."""
        modal = (await self._ensure_schema(ctx.guild))["modals"].get(key.lower())
        if not modal:
            return await ctx.send("Modal non trovato.")
        questions = modal.get("questions", [])
        qlines = [
            f"**{i}.** {q.get('label')} | `{q.get('style')}` | {'obbligatoria' if q.get('required') else 'facoltativa'} | {q.get('min_length', 0)}-{q.get('max_length', 1000)}"
            for i, q in enumerate(questions, 1)
        ]
        await ctx.send(
            f"**Modal:** `{key.lower()}`\n"
            f"**Titolo:** {modal.get('title', '—')}\n"
            f"**Messaggio dopo invio:** {self._clean(modal.get('submit_message'), 1200)}\n"
            f"**Domande:**\n" + ("\n".join(qlines) if qlines else "—")
        )

    @modal_group.command(name="title")
    async def modal_title(self, ctx: commands.Context, key: str, *, title: str):
        """Modifica il titolo mostrato in alto nel modal."""
        if not 1 <= len(title) <= 45:
            return await ctx.send("Il titolo deve essere lungo da 1 a 45 caratteri.")
        async with self.config.guild(ctx.guild).modals() as modals:
            modal = modals.get(key.lower())
            if not modal:
                return await ctx.send("Modal non trovato.")
            modal["title"] = title
        await ctx.send("Titolo del modal aggiornato.")

    @modal_group.command(name="response")
    async def modal_response(self, ctx: commands.Context, key: str, *, message: str):
        """Imposta il messaggio mostrato all'utente dopo l'invio del modal."""
        if not 1 <= len(message) <= 1900:
            return await ctx.send("Il messaggio deve essere lungo da 1 a 1900 caratteri.")
        async with self.config.guild(ctx.guild).modals() as modals:
            modal = modals.get(key.lower())
            if not modal:
                return await ctx.send("Modal non trovato.")
            modal["submit_message"] = message
        await ctx.send("Messaggio finale del modal aggiornato.")

    @modal_group.command(name="remove")
    async def modal_remove(self, ctx: commands.Context, key: str):
        """Elimina un modal solo se nessun pulsante lo sta ancora utilizzando."""
        key = key.lower()
        data = await self._ensure_schema(ctx.guild)
        used_by = [bkey for bkey, button in data["buttons"].items() if button.get("modal") == key]
        if used_by:
            return await ctx.send("Prima scollega o elimina questi pulsanti: " + ", ".join(f"`{x}`" for x in used_by))
        async with self.config.guild(ctx.guild).modals() as modals:
            if key not in modals:
                return await ctx.send("Modal non trovato.")
            del modals[key]
        await ctx.send(f"Modal `{key}` eliminato.")

    @leavefeed.group(name="question", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def question_group(self, ctx: commands.Context):
        """Aggiunge e modifica le singole domande presenti nei modal."""
        await ctx.send_help(ctx.command)

    @question_group.command(name="add")
    async def question_add(self, ctx: commands.Context, modal_key: str, style: str, required: bool, min_length: int, max_length: int, *, label: str):
        """Aggiunge una domanda indicando stile, obbligatorietà e lunghezza risposta."""
        style, modal_key = style.lower(), modal_key.lower()
        if style not in {"short", "long"}:
            return await ctx.send("Lo stile deve essere `short` oppure `long`.")
        if not 1 <= len(label) <= 45:
            return await ctx.send("La domanda deve essere lunga da 1 a 45 caratteri.")
        if min_length < 0 or max_length < 1 or max_length > 4000 or min_length > max_length:
            return await ctx.send("Lunghezze non valide: min >= 0, max tra 1 e 4000 e min <= max.")
        async with self.config.guild(ctx.guild).modals() as modals:
            modal = modals.get(modal_key)
            if not modal:
                return await ctx.send("Modal non trovato.")
            questions = modal.setdefault("questions", [])
            if len(questions) >= 5:
                return await ctx.send("Un modal può avere al massimo 5 domande.")
            questions.append({
                "label": label,
                "placeholder": "",
                "style": style,
                "required": required,
                "min_length": min_length,
                "max_length": max_length,
            })
        await ctx.send("Domanda aggiunta.")

    @question_group.command(name="list")
    async def question_list(self, ctx: commands.Context, modal_key: str):
        """Elenca tutte le domande presenti in uno specifico modal."""
        modal = (await self._ensure_schema(ctx.guild))["modals"].get(modal_key.lower())
        if not modal:
            return await ctx.send("Modal non trovato.")
        questions = modal.get("questions", [])
        if not questions:
            return await ctx.send("Questo modal non ha domande.")
        await ctx.send("\n".join(
            f"**{i}.** {q.get('label')} | `{q.get('style')}` | {'obbligatoria' if q.get('required') else 'facoltativa'} | {q.get('min_length', 0)}-{q.get('max_length', 1000)}"
            for i, q in enumerate(questions, 1)
        ))

    async def _get_question(self, ctx: commands.Context, modal_key: str, index: int) -> Optional[Dict]:
        modal = (await self._ensure_schema(ctx.guild))["modals"].get(modal_key.lower())
        if not modal:
            await ctx.send("Modal non trovato.")
            return None
        questions = modal.get("questions", [])
        if index < 1 or index > len(questions):
            await ctx.send("Indice domanda non valido.")
            return None
        return questions[index - 1]

    @question_group.command(name="remove")
    async def question_remove(self, ctx: commands.Context, modal_key: str, index: int):
        """Rimuove una domanda dal modal usando il suo numero mostrato in question list."""
        if await self._get_question(ctx, modal_key, index) is None:
            return
        async with self.config.guild(ctx.guild).modals() as modals:
            modals[modal_key.lower()]["questions"].pop(index - 1)
        await ctx.send("Domanda rimossa.")

    @question_group.command(name="label")
    async def question_label(self, ctx: commands.Context, modal_key: str, index: int, *, label: str):
        """Modifica il testo della domanda visualizzato nel modal."""
        if not 1 <= len(label) <= 45:
            return await ctx.send("La domanda deve essere lunga da 1 a 45 caratteri.")
        if await self._get_question(ctx, modal_key, index) is None:
            return
        async with self.config.guild(ctx.guild).modals() as modals:
            modals[modal_key.lower()]["questions"][index - 1]["label"] = label
        await ctx.send("Testo della domanda aggiornato.")

    @question_group.command(name="placeholder")
    async def question_placeholder(self, ctx: commands.Context, modal_key: str, index: int, *, text: str):
        """Imposta il testo grigio di esempio dentro lo spazio di risposta; usa - per rimuoverlo."""
        if text == "-":
            text = ""
        if len(text) > 100:
            return await ctx.send("Il placeholder può avere massimo 100 caratteri.")
        if await self._get_question(ctx, modal_key, index) is None:
            return
        async with self.config.guild(ctx.guild).modals() as modals:
            modals[modal_key.lower()]["questions"][index - 1]["placeholder"] = text
        await ctx.send("Placeholder aggiornato.")

    @question_group.command(name="style")
    async def question_style(self, ctx: commands.Context, modal_key: str, index: int, style: str):
        """Imposta una risposta su una riga (short) oppure su più righe (long)."""
        style = style.lower()
        if style not in {"short", "long"}:
            return await ctx.send("Lo stile deve essere `short` oppure `long`.")
        if await self._get_question(ctx, modal_key, index) is None:
            return
        async with self.config.guild(ctx.guild).modals() as modals:
            modals[modal_key.lower()]["questions"][index - 1]["style"] = style
        await ctx.send("Stile della domanda aggiornato.")

    @question_group.command(name="required")
    async def question_required(self, ctx: commands.Context, modal_key: str, index: int, required: bool):
        """Rende una domanda obbligatoria o facoltativa usando true/false."""
        if await self._get_question(ctx, modal_key, index) is None:
            return
        async with self.config.guild(ctx.guild).modals() as modals:
            modals[modal_key.lower()]["questions"][index - 1]["required"] = required
        await ctx.send("Obbligatorietà aggiornata.")

    @question_group.command(name="length")
    async def question_length(self, ctx: commands.Context, modal_key: str, index: int, min_length: int, max_length: int):
        """Modifica il numero minimo e massimo di caratteri accettati dalla domanda."""
        if min_length < 0 or max_length < 1 or max_length > 4000 or min_length > max_length:
            return await ctx.send("Lunghezze non valide: min >= 0, max tra 1 e 4000 e min <= max.")
        if await self._get_question(ctx, modal_key, index) is None:
            return
        async with self.config.guild(ctx.guild).modals() as modals:
            question = modals[modal_key.lower()]["questions"][index - 1]
            question["min_length"] = min_length
            question["max_length"] = max_length
        await ctx.send("Lunghezza della risposta aggiornata.")
