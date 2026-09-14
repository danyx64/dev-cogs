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
# Italian adaptation for danyx64/dev-cogs. Config structure intentionally
# kept compatible with Jackenmen/JackCogs NitroRole.

from string import Template
from typing import List, Optional, Union

import discord
from redbot.core.config import Config, Group


class GuildData:
    """Cache della configurazione NitroRole di un server."""

    __slots__ = (
        "id",
        "_config",
        "_config_group",
        "role_id",
        "channel_id",
        "messages",
        "message_templates",
        "unassign_on_boost_end",
    )

    def __init__(
        self,
        guild_id: int,
        config: Config,
        *,
        role_id: Optional[int],
        channel_id: Optional[int],
        message_templates: List[str],
        unassign_on_boost_end: bool,
    ) -> None:
        self.id = guild_id
        self._config = config
        self.role_id = role_id
        self.channel_id = channel_id
        self.messages = []
        self.message_templates = []
        self.unassign_on_boost_end = unassign_on_boost_end
        self._update_messages(message_templates)

    @property
    def config_group(self) -> Group:
        try:
            return self._config_group
        except AttributeError:
            self._config_group = self._config.guild_from_id(self.id)
            return self._config_group

    async def set_unassign_on_boost_end(self, state: bool) -> None:
        self.unassign_on_boost_end = state
        await self.config_group.unassign_on_boost_end.set(state)

    async def set_role(self, role: Optional[discord.Role]) -> None:
        if role is None:
            self.role_id = None
            await self.config_group.role_id.clear()
        else:
            self.role_id = role.id
            await self.config_group.role_id.set(role.id)

    async def set_channel(
        self,
        channel: Optional[
            Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel]
        ],
    ) -> None:
        if channel is None:
            self.channel_id = None
            await self.config_group.channel_id.clear()
        else:
            self.channel_id = channel.id
            await self.config_group.channel_id.set(channel.id)

    async def add_message(self, message: str) -> Template:
        template = Template(message)
        self.messages.append(message)
        self.message_templates.append(template)
        await self.config_group.message_templates.set(self.messages)
        return template

    async def remove_message(self, index: int) -> None:
        self.messages.pop(index)
        self.message_templates.pop(index)
        await self.config_group.message_templates.set(self.messages)

    def _update_messages(self, messages: List[str]) -> None:
        self.messages = list(messages)
        self.message_templates = [Template(message) for message in messages]
