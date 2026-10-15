"""Coordinate staff commands without turning diagnostic reads into role writes."""
import types
import unittest
from unittest.mock import AsyncMock
from test_adapters import Base, load_adapter


class StaffBase(Base):
    async def _member(self, ctx, user_id):
        return ctx.member

    def get_guild(self, guild_id):
        return getattr(self, 'test_guild', None)


class StaffAdapters(unittest.IsolatedAsyncioTestCase):
    async def test_staff_role_changes_wait_for_restore(self):
        sticky = types.SimpleNamespace(before_role_change=AsyncMock(return_value='fresh'))
        cog = load_adapter('hierarchyadmin', 'HierarchyAdmin', StaffBase)(sticky)
        for command in ('ha give', 'ha remove'):
            ctx = types.SimpleNamespace(command=types.SimpleNamespace(qualified_name=command), member='stale')
            self.assertEqual(await cog._member(ctx, 10), 'fresh')
        self.assertEqual(sticky.before_role_change.await_count, 2)

    async def test_staff_read_only_does_not_change_roles(self):
        sticky = types.SimpleNamespace(before_role_change=AsyncMock(return_value='fresh'))
        cog = load_adapter('hierarchyadmin', 'HierarchyAdmin', StaffBase)(sticky)
        ctx = types.SimpleNamespace(command=types.SimpleNamespace(qualified_name='ha member'), member='stale')
        self.assertEqual(await cog._member(ctx, 10), 'stale')
        sticky.before_role_change.assert_not_awaited()

    async def test_owner_role_commands_wait_before_atomic_mutation(self):
        sticky = types.SimpleNamespace(before_role_change=AsyncMock(return_value='fresh'))
        cog = load_adapter('ownercontrol', 'OwnerControl', StaffBase)(sticky)
        cog.test_guild = types.SimpleNamespace(get_member=lambda uid: 'stale')
        for command in ('ownerctl addrole', 'ownerctl removerole'):
            ctx = types.SimpleNamespace(command=types.SimpleNamespace(qualified_name=command), args=[cog, None, 1, 10, 2])
            await cog.cog_before_invoke(ctx)
        self.assertEqual(sticky.before_role_change.await_count, 2)

    async def test_owner_read_only_skips_restore(self):
        sticky = types.SimpleNamespace(before_role_change=AsyncMock(return_value='fresh'))
        cog = load_adapter('ownercontrol', 'OwnerControl', StaffBase)(sticky)
        ctx = types.SimpleNamespace(command=types.SimpleNamespace(qualified_name='ownerctl members'))
        await cog.cog_before_invoke(ctx)
        sticky.before_role_change.assert_not_awaited()
