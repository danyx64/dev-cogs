"""Integrazione Red-Web-Dashboard per InviteTracker.

Basata sul cog InviteTracker di TaakoOfficial/TaakosCogs, distribuito sotto
GNU Affero General Public License v3.0. Modificata e tradotta in italiano.
"""

from __future__ import annotations

import html
import logging
import typing
from datetime import datetime, timezone

import discord
from redbot.core import commands

log = logging.getLogger("red.taakoscogs.invitetracker.dashboard")


def dashboard_page(*args, **kwargs):
    """Decorator compatibile con le pagine third-party di Red-Web-Dashboard."""

    def decorator(func: typing.Callable):
        func.__dashboard_decorator_params__ = (args, kwargs)
        return func

    return decorator


class DashboardIntegration:
    """Mixin Dashboard per InviteTracker."""

    @commands.Cog.listener()
    async def on_dashboard_cog_add(self, dashboard_cog: commands.Cog) -> None:
        handler = dashboard_cog.rpc.third_parties_handler
        try:
            handler.add_third_party(self, overwrite=True)
        except TypeError:
            handler.add_third_party(self)

    @dashboard_page(
        name=None,
        description="Configura il tracciamento inviti, aggiorna la cache, azzera le statistiche e visualizza i riepiloghi.",
        methods=("GET", "POST"),
    )
    async def dashboard_page(
        self,
        user: discord.User,
        guild: discord.Guild,
        **kwargs,
    ) -> dict[str, typing.Any]:
        _member, can_manage = await self._dashboard_member_can_manage(user, guild)
        if not can_manage:
            return {
                "status": 1,
                "error_title": "Permessi insufficienti",
                "error_message": "Servono Gestisci server, permessi admin di Red oppure accesso owner del bot.",
            }

        notifications: list[dict[str, str]] = []
        form_data = self._dashboard_form_data(kwargs)

        if kwargs.get("method", "GET") == "POST":
            action = self._dash_value(form_data, "action")
            try:
                messages = await self._dashboard_handle_action(guild, action, form_data)
            except commands.CommandError as error:
                notifications.append({"message": str(error), "category": "error"})
            except Exception as error:
                log.exception("Azione Dashboard di InviteTracker fallita.")
                notifications.append(
                    {
                        "message": f"Azione Dashboard di InviteTracker fallita: {error}",
                        "category": "error",
                    }
                )
            else:
                notifications.extend(messages)

        return {
            "status": 0,
            "notifications": notifications,
            "web_content": {
                "source": await self._dashboard_source(guild, kwargs),
                "expanded": True,
            },
        }

    async def _dashboard_member_can_manage(
        self,
        user: discord.User,
        guild: discord.Guild,
    ) -> tuple[discord.Member | None, bool]:
        member = guild.get_member(user.id)
        is_owner = user.id in getattr(self.bot, "owner_ids", set())
        is_admin = member is not None and await self.bot.is_admin(member)
        can_manage = is_owner or is_admin or (
            member is not None and member.guild_permissions.manage_guild
        )
        return member, can_manage

    @staticmethod
    def _dashboard_form_data(kwargs: dict[str, typing.Any]) -> typing.Any:
        data = kwargs.get("data") or {}
        if isinstance(data, dict) and ("form" in data or "json" in data):
            return data.get("form") or data.get("json") or {}
        return data

    def _dash_value(self, form_data: typing.Any, key: str, default: str = "") -> str:
        if not hasattr(form_data, "get"):
            return default
        value = form_data.get(key, default)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else default
        if value is None:
            return default
        return str(value)

    def _dash_bool(self, form_data: typing.Any, key: str) -> bool:
        if hasattr(form_data, "__contains__") and key in form_data:
            value = self._dash_value(form_data, key, "on").lower()
            return value not in {"0", "false", "off", "no", ""}
        return False

    def _dash_int(
        self,
        form_data: typing.Any,
        key: str,
        *,
        default: int | None = None,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        value = self._dash_value(form_data, key).strip()
        try:
            number = int(value)
        except (TypeError, ValueError):
            if default is None:
                raise commands.BadArgument(f"`{key}` deve essere un numero intero.")
            number = default
        if minimum is not None:
            number = max(minimum, number)
        if maximum is not None:
            number = min(maximum, number)
        return number

    def _dash_optional_id(self, form_data: typing.Any, key: str) -> int | None:
        value = self._dash_value(form_data, key).strip()
        if not value:
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise commands.BadArgument(f"`{key}` deve essere un ID Discord.") from exc

    @staticmethod
    def _dash_csrf(kwargs: dict[str, typing.Any]) -> str:
        csrf_token = kwargs.get("csrf_token")
        if not isinstance(csrf_token, (tuple, list)) or len(csrf_token) != 2:
            return ""
        value = html.escape(str(csrf_token[1]), quote=True)
        return f'<input type="hidden" name="csrf_token" value="{value}">'

    async def _dashboard_handle_action(
        self,
        guild: discord.Guild,
        action: str,
        form_data: typing.Any,
    ) -> list[dict[str, str]]:
        if action == "save_settings":
            cache_size = await self._dashboard_save_settings(guild, form_data)
            message = "Impostazioni InviteTracker salvate."
            if cache_size is not None:
                message += f" Cache aggiornata con {self._count(cache_size)} inviti."
            return [{"message": message, "category": "success"}]

        if action == "refresh_cache":
            invite_cache = await self._refresh_invite_cache(guild)
            return [
                {
                    "message": f"Cache inviti aggiornata: {self._count(len(invite_cache))} inviti.",
                    "category": "success",
                }
            ]

        if action == "reset_stats":
            confirmation = self._dash_value(form_data, "reset_confirm").strip().lower()
            if confirmation not in {"confirm", "conferma"}:
                raise commands.BadArgument(
                    "Scrivi `conferma` (oppure `confirm`) per azzerare tutte le statistiche InviteTracker."
                )
            await self.config.guild(guild).inviters.set({})
            await self.config.guild(guild).members.set({})
            await self.config.guild(guild).unknown_joins.set(0)
            try:
                invite_cache = await self._refresh_invite_cache(guild)
            except commands.CommandError as error:
                return [
                    {
                        "message": f"Statistiche azzerate, ma non sono riuscito ad aggiornare la cache inviti: {error}",
                        "category": "warning",
                    }
                ]
            return [
                {
                    "message": f"Statistiche InviteTracker azzerate. Cache: {self._count(len(invite_cache))} inviti.",
                    "category": "success",
                }
            ]

        if action:
            raise commands.BadArgument("Azione Dashboard InviteTracker sconosciuta.")
        return []

    async def _dashboard_save_settings(
        self,
        guild: discord.Guild,
        form_data: typing.Any,
    ) -> int | None:
        enabled = self._dash_bool(form_data, "enabled")
        include_bots = self._dash_bool(form_data, "include_bots")
        log_channel_id = self._dash_optional_id(form_data, "log_channel_id")
        fake_age_hours = self._dash_int(
            form_data,
            "fake_age_hours",
            default=24,
            minimum=0,
            maximum=8760,
        )

        if log_channel_id is not None:
            channel = guild.get_channel(log_channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise commands.BadArgument("Il canale dei log inviti deve essere testuale.")
            me = guild.me
            if me is None:
                raise commands.CommandError("Non riesco a controllare i miei permessi nel canale.")
            permissions = channel.permissions_for(me)
            if not permissions.send_messages or not permissions.embed_links:
                raise commands.CommandError(
                    f"Mi servono Invia messaggi e Incorpora link in #{channel.name}."
                )

        guild_conf = self.config.guild(guild)
        await guild_conf.log_channel_id.set(log_channel_id)
        await guild_conf.include_bots.set(include_bots)
        await guild_conf.fake_age_hours.set(fake_age_hours)

        cache_size = None
        if enabled:
            invite_cache = await self._refresh_invite_cache(guild)
            cache_size = len(invite_cache)
        await guild_conf.enabled.set(enabled)
        return cache_size

    async def _dashboard_source(
        self,
        guild: discord.Guild,
        kwargs: dict[str, typing.Any],
    ) -> str:
        settings = await self.config.guild(guild).all()
        invite_cache = settings.get("invite_cache") or {}
        inviters = settings.get("inviters") or {}
        members = settings.get("members") or {}
        csrf = self._dash_csrf(kwargs)

        total_joins = sum(int(stats.get("joins", 0)) for stats in inviters.values())
        total_leaves = sum(int(stats.get("leaves", 0)) for stats in inviters.values())
        total_fake = sum(int(stats.get("fake", 0)) for stats in inviters.values())
        active_members = sum(1 for record in members.values() if not record.get("left_at"))

        leaderboard = self._leaderboard_rows(guild, inviters)
        recent_members = self._recent_member_rows(guild, members)

        return f"""
<style>
.it-dash {{color:#eef1f5;display:grid;gap:16px}}
.it-grid {{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}}
.it-card {{background:#171b22;border:1px solid #2a303a;border-radius:8px;padding:16px}}
.it-stats {{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}}
.it-stat {{border:1px solid #2a303a;border-radius:8px;padding:12px}}
.it-stat strong {{display:block;font-size:22px}}
.it-muted,.it-field label {{color:#aab2bf}}
.it-field {{display:grid;gap:6px;margin-bottom:10px}}
.it-field input,.it-field select {{background:#0c0f14;color:#eef1f5;border:1px solid #2a303a;border-radius:6px;padding:9px 10px}}
.it-check {{display:flex;align-items:center;gap:8px;margin-bottom:10px;color:#aab2bf}}
.it-actions {{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}}
.it-actions button {{background:#8ab4ff;color:#07111f;border:0;border-radius:6px;padding:9px 12px;font-weight:700;cursor:pointer}}
.it-actions button.danger {{background:#ff6b6b;color:#210909}}
.it-table {{width:100%;border-collapse:collapse;font-size:14px}}
.it-table th,.it-table td {{border-bottom:1px solid #2a303a;padding:8px;text-align:left;vertical-align:top}}
.it-table th {{color:#aab2bf;font-weight:600}}
</style>
<div class="it-dash">
  <div class="it-stats">
    <div class="it-stat"><strong>{self._h("Attivo" if settings.get("enabled") else "Disattivato")}</strong><span>stato</span></div>
    <div class="it-stat"><strong>{self._count(total_joins)}</strong><span>entrati</span></div>
    <div class="it-stat"><strong>{self._count(total_leaves)}</strong><span>usciti</span></div>
    <div class="it-stat"><strong>{self._count(total_fake)}</strong><span>join fake</span></div>
    <div class="it-stat"><strong>{self._count(active_members)}</strong><span>membri tracciati attivi</span></div>
    <div class="it-stat"><strong>{self._count(len(invite_cache))}</strong><span>inviti in cache</span></div>
  </div>
  <div class="it-grid">
    <form class="it-card" method="post">
      {csrf}
      <input type="hidden" name="action" value="save_settings">
      <h2>Impostazioni</h2>
      {self._checkbox("enabled", "Attiva tracciamento inviti", settings.get("enabled"))}
      {self._select("log_channel_id", "Canale log inviti", self._text_options(guild), settings.get("log_channel_id"), "Nessun canale log")}
      {self._input("fake_age_hours", "Soglia join fake (ore)", settings.get("fake_age_hours") or 0, "number", 0, 8760)}
      {self._checkbox("include_bots", "Traccia anche i bot", settings.get("include_bots"))}
      <div class="it-actions"><button type="submit">Salva impostazioni</button></div>
    </form>
    <div class="it-card">
      <h2>Manutenzione</h2>
      <form method="post" class="it-actions">
        {csrf}<input type="hidden" name="action" value="refresh_cache">
        <button type="submit">Aggiorna cache inviti</button>
      </form>
      <form method="post" class="it-actions">
        {csrf}<input type="hidden" name="action" value="reset_stats">
        <input name="reset_confirm" placeholder="scrivi conferma">
        <button class="danger" type="submit">Azzera statistiche</button>
      </form>
      <p class="it-muted">L'azzeramento elimina totali invitatori, sorgenti dei membri tracciati e conteggio join sconosciuti.</p>
    </div>
    <div class="it-card"><h2>Classifica invitatori</h2>{leaderboard}</div>
    <div class="it-card"><h2>Membri tracciati recenti</h2>{recent_members}</div>
  </div>
</div>
"""

    def _leaderboard_rows(
        self,
        guild: discord.Guild,
        inviters: dict[str, typing.Any],
    ) -> str:
        if not inviters:
            return '<p class="it-muted">Nessuna statistica inviti registrata.</p>'
        ranked = sorted(
            inviters.items(),
            key=lambda item: (
                self._net_joins(item[1]),
                int(item[1].get("joins", 0)),
                -int(item[1].get("leaves", 0)),
            ),
            reverse=True,
        )
        rows = []
        for index, (user_id, stats) in enumerate(ranked[:10], start=1):
            rows.append(
                "<tr>"
                f"<td>{index}</td>"
                f"<td>{self._h(self._member_label(guild, user_id))}</td>"
                f"<td>{self._count(self._net_joins(stats))}</td>"
                f"<td>{self._count(int(stats.get('joins', 0)))}</td>"
                f"<td>{self._count(int(stats.get('leaves', 0)))}</td>"
                f"<td>{self._count(int(stats.get('fake', 0)))}</td>"
                "</tr>"
            )
        return (
            '<table class="it-table"><thead><tr><th>#</th><th>Invitatore</th>'
            '<th>Netti</th><th>Entrati</th><th>Usciti</th><th>Fake</th></tr></thead><tbody>'
            + "".join(rows)
            + "</tbody></table>"
        )

    def _recent_member_rows(
        self,
        guild: discord.Guild,
        members: dict[str, typing.Any],
    ) -> str:
        if not members:
            return '<p class="it-muted">Nessuna sorgente di ingresso tracciata.</p>'
        records = sorted(
            members.values(),
            key=lambda record: float(record.get("joined_at") or 0),
            reverse=True,
        )
        rows = []
        for record in records[:10]:
            rows.append(
                "<tr>"
                f"<td>{self._h(self._member_label(guild, record.get('member_id')))}</td>"
                f"<td>{self._h(self._member_label(guild, record.get('inviter_id')))}</td>"
                f"<td>{self._h(record.get('invite_code') or 'Sconosciuto')}</td>"
                f"<td>{self._h(self._format_dashboard_time(record.get('joined_at')))}</td>"
                f"<td>{'Si' if record.get('left_at') else 'No'}</td>"
                f"<td>{'Si' if record.get('fake') else 'No'}</td>"
                "</tr>"
            )
        return (
            '<table class="it-table"><thead><tr><th>Membro</th><th>Invitatore</th>'
            '<th>Invito</th><th>Entrato</th><th>Uscito</th><th>Fake</th></tr></thead><tbody>'
            + "".join(rows)
            + "</tbody></table>"
        )

    @staticmethod
    def _text_options(guild: discord.Guild) -> list[tuple[int, str]]:
        return [(channel.id, f"#{channel.name}") for channel in guild.text_channels]

    def _select(
        self,
        name: str,
        label: str,
        options: list[tuple[typing.Any, str]],
        selected: typing.Any = "",
        empty_label: str = "Seleziona...",
    ) -> str:
        option_html = [f'<option value="">{self._h(empty_label)}</option>']
        for value, text in options:
            selected_attr = "selected" if str(value) == str(selected) else ""
            option_html.append(
                f'<option value="{self._h(value)}" {selected_attr}>{self._h(text)}</option>'
            )
        return (
            f'<div class="it-field"><label>{self._h(label)}</label>'
            f'<select name="{self._h(name)}">{"".join(option_html)}</select></div>'
        )

    def _input(
        self,
        name: str,
        label: str,
        value: typing.Any,
        input_type: str = "text",
        min_value: int | None = None,
        max_value: int | None = None,
    ) -> str:
        attrs = []
        if min_value is not None:
            attrs.append(f'min="{min_value}"')
        if max_value is not None:
            attrs.append(f'max="{max_value}"')
        return (
            f'<div class="it-field"><label>{self._h(label)}</label>'
            f'<input type="{self._h(input_type)}" name="{self._h(name)}" '
            f'value="{self._h(value)}" {" ".join(attrs)}></div>'
        )

    def _checkbox(self, name: str, label: str, checked: typing.Any) -> str:
        checked_attr = "checked" if checked else ""
        return (
            '<label class="it-check">'
            f'<input type="checkbox" name="{self._h(name)}" value="1" {checked_attr}>'
            f"{self._h(label)}</label>"
        )

    @staticmethod
    def _member_label(guild: discord.Guild, user_id: typing.Any) -> str:
        if user_id in (None, ""):
            return "Sconosciuto"
        try:
            user_int = int(user_id)
        except (TypeError, ValueError):
            return "Sconosciuto"
        member = guild.get_member(user_int)
        return str(member) if member else f"Utente {user_int}"

    @staticmethod
    def _format_dashboard_time(value: typing.Any) -> str:
        if value in (None, ""):
            return "Sconosciuto"
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return "Sconosciuto"
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    @staticmethod
    def _h(value: typing.Any) -> str:
        return html.escape("" if value is None else str(value), quote=True)
