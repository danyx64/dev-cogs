"""Deterministic tests of real RoleMemory code; Discord/Config are test doubles.

No credentials, connection to Discord, or installed Red instance is required.
"""
import asyncio
import copy
import importlib.util
import sys
import types
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch


class HTTPException(Exception):
    def __init__(self, status=500):
        self.status = status


class NotFound(HTTPException):
    pass


fake_discord = types.SimpleNamespace(HTTPException=HTTPException, NotFound=NotFound)
spec = importlib.util.spec_from_file_location("sr_core_tested", Path(__file__).parents[1] / "core.py")
core = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"discord": fake_discord}):
    spec.loader.exec_module(core)


class Group:
    def __init__(self, config, key):
        self.config, self.key = config, key

    async def all(self):
        return copy.deepcopy(self.config.data.get(self.key, core.MEMBER_DEFAULTS))

    async def set(self, value):
        self.config.data[self.key] = copy.deepcopy(value)

    async def clear(self):
        self.config.data.pop(self.key, None)


class Config:
    def __init__(self):
        self.data = {}

    def member_from_ids(self, guild_id, user_id):
        return Group(self, (guild_id, user_id))


class Role:
    def __init__(self, role_id, position, *, managed=False, default=False):
        self.id, self.position, self.managed, self.default = role_id, position, managed, default

    def is_default(self):
        return self.default

    def __ge__(self, other):
        return self.position >= other.position


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class Member:
    def __init__(self, guild, user_id, role_ids, joined):
        self.guild, self.id, self.joined_at = guild, user_id, joined
        self.roles = [guild.roles[r] for r in [0, *sorted(role_ids)] if r in guild.roles]

    async def add_roles(self, role, *, reason, atomic):
        await self._change(role, True)

    async def remove_roles(self, role, *, reason, atomic):
        await self._change(role, False)

    async def _change(self, role, add):
        guild = self.guild
        guild.requests += 1
        if self.id not in guild.state:
            raise NotFound(404)
        if guild.fail or role.id in guild.fail_roles:
            raise HTTPException(403)
        roles, joined = guild.state[self.id]
        roles = roles | {role.id} if add else roles - {role.id}
        guild.state[self.id] = (roles, joined)
        guild.ops.append(("add" if add else "remove", self.id, role.id))
        if guild.after_change:
            await guild.after_change(self)
        # Do not mutate this Member, just like discord.py's atomic role methods.


class Guild:
    def __init__(self, guild_id=1):
        self.id = guild_id
        self.owner_id = 9999
        self.roles = {0: Role(0, 0, default=True), 1: Role(1, 1), 2: Role(2, 2),
                      3: Role(3, 3), 4: Role(4, 4, managed=True), 5: Role(5, 101)}
        self.me = types.SimpleNamespace(id=9998, top_role=Role(99, 100),
                                        guild_permissions=types.SimpleNamespace(manage_roles=True))
        self.state, self.ops = {}, []
        self.fail, self.fail_roles, self.after_change = False, set(), None
        self.requests = 0

    def member(self, user_id=10, roles=(1,), joined=NOW):
        self.state[user_id] = (set(roles), joined)
        return self.get_member(user_id)

    def get_member(self, user_id):
        if user_id not in self.state:
            return None
        roles, joined = self.state[user_id]
        return Member(self, user_id, roles, joined)

    async def fetch_member(self, user_id):
        member = self.get_member(user_id)
        if member is None:
            raise NotFound(404)
        return member

    def get_role(self, role_id):
        return self.roles.get(role_id)


class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.guild = Guild()
        self.config = Config()
        self.enabled = True
        self.bot = types.SimpleNamespace(get_guild=lambda gid: self.guild if gid == self.guild.id else None)
        async def enabled(guild):
            return self.enabled
        self.memory = core.RoleMemory(self.bot, self.config, enabled, delays=(0, 0, 0))

    async def asyncTearDown(self):
        jobs = list(self.memory._jobs.values())
        self.memory.close()
        await asyncio.gather(*jobs, return_exceptions=True)

    async def drain(self):
        tasks = list(self.memory._jobs.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)

    async def saved(self, user_id=10):
        return await self.memory.read(self.guild.id, user_id)

    async def rejoin(self, roles=(1,), current=()):
        member = self.guild.member(roles=roles)
        await self.memory.snapshot(member)
        self.guild.state.pop(member.id)
        await self.memory.left(self.guild.id, member.id, member)
        return self.guild.member(roles=current, joined=NOW + timedelta(days=1))

    async def test_snapshot_all_roles_including_managed_but_not_everyone(self):
        await self.memory.snapshot(self.guild.member(roles=(1, 4, 5)))
        self.assertEqual((await self.saved())["roles"], [1, 4, 5])

    async def test_new_user_autorole_before_sticky(self):
        member = self.guild.member(roles=())
        self.assertFalse(await self.memory.should_skip_autorole(member))
        await member.add_roles(self.guild.roles[2], reason="AutoRole", atomic=True)
        await self.memory.observe(self.guild.get_member(10))
        self.assertEqual((await self.saved())["roles"], [2])

    async def test_new_user_autorole_after_sticky(self):
        member = self.guild.member(roles=())
        await self.memory.joined(member)
        self.assertFalse(await self.memory.should_skip_autorole(member))

    async def test_rest_join_timestamp_precision_does_not_block_restore(self):
        member = await self.rejoin(roles=(1, 3), current=(2,))
        # Discord Gateway vs REST can differ slightly in joined_at precision.
        original_fetch = self.guild.fetch_member
        async def rest_fetch(user_id):
            result = await original_fetch(user_id)
            result.joined_at += timedelta(milliseconds=600)
            return result
        self.guild.fetch_member = rest_fetch
        await self.memory.joined(member)
        await self.drain()
        self.assertEqual(self.guild.state[10][0], {1, 3})
        self.assertFalse((await self.saved())["pending"])
    async def test_sticky_nickname_saved_and_restored_on_rejoin(self):
        member = self.guild.member(roles=(1,))
        member.nick = "Veterano"
        await self.memory.snapshot(member)
        data = await self.saved()
        self.assertEqual(data["nick"], "Veterano")
        self.assertTrue(data["nick_known"])
        self.guild.state.pop(10)
        await self.memory.left(1, 10, member)
        joined = self.guild.member(roles=(), joined=NOW + timedelta(days=1))
        async def fetch_member(user_id):
            fresh = self.guild.get_member(user_id)
            fresh.nick = None
            fresh.top_role = self.guild.roles[1]
            async def edit(*, nick, reason):
                self.guild.restored_nick = nick
            fresh.edit = edit
            return fresh
        self.guild.fetch_member = fetch_member
        self.guild.me.guild_permissions.manage_nicknames = True
        await self.memory.joined(joined)
        await self.drain()
        self.assertEqual(self.guild.restored_nick, "Veterano")
        self.assertEqual(self.guild.state[10][0], {1})
    async def test_returnee_blocks_autorole_before_listener(self):
        member = await self.rejoin()
        self.assertTrue(await self.memory.should_skip_autorole(member))
        await self.memory.joined(member)
        await self.drain()
        self.assertEqual(self.guild.state[10][0], {1})
        self.assertTrue(await self.memory.should_skip_autorole(self.guild.get_member(10)))

    async def test_returnee_empty_snapshot_blocks_autorole(self):
        member = await self.rejoin(roles=(), current=(2,))
        self.assertTrue(await self.memory.should_skip_autorole(member))
        await self.drain()
        self.assertEqual(self.guild.state[10][0], set())
        self.assertTrue((await self.saved())["known"])

    async def test_unexpected_role_removed_before_restore(self):
        member = await self.rejoin(current=(2,))
        await self.memory.joined(member)
        await self.drain()
        self.assertEqual(self.guild.ops[:2], [("remove", 10, 2), ("add", 10, 1)])

    async def test_live_role_changes_saved_not_permanently_locked(self):
        member = await self.rejoin()
        await self.memory.joined(member)
        await self.drain()
        await self.guild.get_member(10).add_roles(self.guild.roles[3], reason="Staff", atomic=True)
        await self.memory.observe(self.guild.get_member(10))
        self.assertEqual((await self.saved())["roles"], [1, 3])

    async def test_unmanaged_higher_managed_and_deleted_roles_not_mutated(self):
        member = await self.rejoin(roles=(1, 4, 5), current=(2, 4))
        self.guild.roles.pop(1)
        await self.memory.joined(member)
        await self.drain()
        self.assertEqual(self.guild.state[10][0], {4})
        self.assertTrue((await self.saved())["last_result"]["skipped"])
        self.assertFalse(any(op[2] in (0, 4, 5) for op in self.guild.ops))

    async def test_leave_while_pending_preserves_original_snapshot(self):
        member = await self.rejoin(roles=(1, 3))
        await self.memory.joined(member)
        self.guild.state.pop(10)
        await self.memory.left(1, 10, member)
        self.assertEqual((await self.saved())["roles"], [1, 3])
        self.assertFalse((await self.saved())["present"])

    async def test_raw_leave_without_cache_preserves_snapshot(self):
        await self.memory.snapshot(self.guild.member(roles=(1, 3)))
        self.guild.state.pop(10)
        await self.memory.left(1, 10)
        self.assertEqual((await self.saved())["roles"], [1, 3])
        self.assertFalse((await self.saved())["present"])

    async def test_raw_then_cached_leave_same_result(self):
        member = self.guild.member(roles=(1, 3))
        await self.memory.snapshot(member)
        self.guild.state.pop(10)
        await self.memory.left(1, 10)
        await self.memory.left(1, 10, member)
        self.assertEqual((await self.saved())["roles"], [1, 3])

    async def test_stale_leave_does_not_clobber_new_join(self):
        old = self.guild.member()
        await self.memory.snapshot(old)
        fresh = self.guild.member(roles=(), joined=NOW + timedelta(days=1))
        await self.memory.joined(fresh)
        await self.memory.left(1, 10, old)
        await self.drain()
        self.assertTrue((await self.saved())["present"])
        self.assertEqual((await self.saved())["joined"], fresh.joined_at.isoformat())

    async def test_stale_update_does_not_replace_new_session(self):
        old = self.guild.member(roles=(2,))
        fresh = await self.rejoin(roles=(1,))
        await self.memory.observe(old)
        await self.memory.joined(fresh)
        await self.drain()
        self.assertEqual(self.guild.state[10][0], {1})

    async def test_role_update_before_join_handler_cannot_save_empty_roles(self):
        member = await self.rejoin(roles=(1,))
        await self.memory.observe(member)
        await self.drain()
        self.assertEqual((await self.saved())["roles"], [1])

    async def test_api_failure_does_not_erase_snapshot_or_spin(self):
        member = await self.rejoin()
        self.guild.fail = True
        await self.memory.joined(member)
        await self.drain()
        self.assertTrue((await self.saved())["retry_needed"])
        self.assertEqual((await self.saved())["roles"], [1])
        old_requests = self.guild.requests
        await self.memory.observe(member)
        await self.drain()
        self.assertEqual(self.guild.requests, old_requests)

    async def test_retry_after_fixing_permissions(self):
        member = await self.rejoin()
        self.guild.fail = True
        await self.memory.joined(member)
        await self.drain()
        self.guild.fail = False
        await self.memory.retry(member)
        await self.drain()
        self.assertEqual(self.guild.state[10][0], {1})
        self.assertFalse((await self.saved())["pending"])

    async def test_reload_resumes_persistent_pending(self):
        member = await self.rejoin(roles=(1, 3))
        await self.memory.joined(member)
        jobs = list(self.memory._jobs.values())
        self.memory.close()
        await asyncio.gather(*jobs, return_exceptions=True)
        old = self.memory
        self.memory = core.RoleMemory(self.bot, self.config, old.enabled, delays=(0,))
        await self.memory.joined(member)
        await self.drain()
        self.assertEqual(self.guild.state[10][0], {1, 3})

    async def test_disabled_does_not_restore_or_block_autorole(self):
        member = await self.rejoin()
        self.enabled = False
        self.assertFalse(await self.memory.should_skip_autorole(member))
        await self.memory.joined(member)
        self.assertFalse(self.memory._jobs)

    async def test_duplicate_join_calls_one_job(self):
        member = await self.rejoin()
        await asyncio.gather(self.memory.joined(member), self.memory.joined(member),
                             self.memory.should_skip_autorole(member))
        await self.drain()
        self.assertEqual(self.guild.ops, [("add", 10, 1)])

    async def test_quarantine_deliberate_change_not_undone(self):
        member = await self.rejoin(roles=(1, 3))
        await self.memory.joined(member)
        fresh = await self.memory.before_role_change(member)
        self.assertEqual(core.role_ids(fresh), [1, 3])
        await fresh.add_roles(self.guild.roles[2], reason="Quarantena", atomic=True)
        await fresh.remove_roles(self.guild.roles[1], reason="Quarantena", atomic=True)
        await fresh.remove_roles(self.guild.roles[3], reason="Quarantena", atomic=True)
        await self.memory.observe(self.guild.get_member(10))
        await self.drain()
        self.assertEqual((await self.saved())["roles"], [2])
        self.assertEqual(self.guild.state[10][0], {2})

    async def test_forced_snapshot_cancels_rejoin_guard(self):
        member = await self.rejoin(roles=(1,), current=(3,))
        await self.memory.joined(member)
        await self.memory.snapshot(member, force=True)
        await self.drain()
        self.assertEqual((await self.saved())["roles"], [3])
        self.assertEqual(self.guild.state[10][0], {3})

    async def test_seed_does_not_overwrite_pending(self):
        member = await self.rejoin()
        self.assertFalse(await self.memory.snapshot(member))
        self.assertEqual((await self.saved())["roles"], [1])
        await self.drain()

    async def test_forget_cancels_pending_without_changing_roles(self):
        member = await self.rejoin(current=(3,))
        await self.memory.joined(member)
        await self.memory.forget(1, 10)
        await self.drain()
        self.assertFalse((await self.saved())["known"])
        self.assertEqual(self.guild.state[10][0], {3})

    async def test_guild_data_is_isolated(self):
        await self.memory.snapshot(self.guild.member(roles=(1,)))
        other = Guild(2)
        await self.memory.snapshot(other.member(roles=(3,)))
        self.assertEqual((await self.memory.read(1, 10))["roles"], [1])
        self.assertEqual((await self.memory.read(2, 10))["roles"], [3])

    async def test_cannot_strip_bot_own_roles(self):
        user = self.guild.me.id
        member = self.guild.member(user_id=user, roles=(1,))
        await self.memory.snapshot(member)
        member = self.guild.member(user_id=user, roles=(3,), joined=NOW + timedelta(days=1))
        await self.memory.joined(member)
        await self.drain()
        self.assertEqual(self.guild.state[user][0], {3})

    async def test_stale_autorole_event_is_blocked(self):
        old = self.guild.member(roles=())
        self.guild.member(roles=(), joined=NOW + timedelta(days=1))
        self.assertTrue(await self.memory.should_skip_autorole(old))


if __name__ == "__main__":
    unittest.main()
