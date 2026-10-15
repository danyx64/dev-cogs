"""A cooperating cog must not erase a snapshot after partial restoration."""
import asyncio
import types
import unittest
from datetime import timedelta
from test_core import Config, Guild, NOW, core


class PartialRestore(unittest.IsolatedAsyncioTestCase):
    async def test_deliberate_action_keeps_original_after_api_failure(self):
        guild, config = Guild(), Config()
        bot = types.SimpleNamespace(get_guild=lambda gid: guild)
        async def enabled(g):
            return True
        memory = core.RoleMemory(bot, config, enabled, delays=(0, 0, 0))
        try:
            member = guild.member(roles=(1, 3))
            await memory.snapshot(member)
            guild.state.pop(10)
            await memory.left(1, 10, member)
            member = guild.member(roles=(), joined=NOW + timedelta(days=1))
            guild.fail_roles.add(3)
            await memory.joined(member)
            self.assertIsNone(await memory.before_role_change(member))
            data = await memory.read(1, 10)
            self.assertEqual(data['roles'], [1, 3])
            self.assertTrue(data['pending'])
            self.assertTrue(data['retry_needed'])
        finally:
            jobs = list(memory._jobs.values())
            memory.close()
            await asyncio.gather(*jobs, return_exceptions=True)
