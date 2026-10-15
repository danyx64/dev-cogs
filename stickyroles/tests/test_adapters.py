"""Test adapter ordering without importing the legacy third-party dependencies."""
import ast
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[2]


def load_adapter(folder, name, base):
    path = ROOT / folder / 'sticky_compat.py'
    source = ast.parse(path.read_text())
    node = next(n for n in source.body if isinstance(n, ast.ClassDef))
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    names = {node.bases[0].id: base, 'commands': types.SimpleNamespace(
        Cog=types.SimpleNamespace(listener=lambda: (lambda fn: fn)))}
    exec(compile(module, str(path), 'exec'), names)
    return names[name]


class Base:
    def __init__(self, sticky=None):
        self.bot = types.SimpleNamespace(get_cog=lambda name: sticky)
        self.calls = []

    async def on_member_join(self, member):
        self.calls.append(('join', member))

    async def isolate(self, member, reason):
        self.calls.append(('isolate', member, reason))

    async def _member_from_payload(self, payload):
        return payload.member

    async def maybe_assign_role(self, data, member):
        self.calls.append(('add', member))

    async def maybe_unassign_role(self, data, member):
        self.calls.append(('remove', member))


class Adapters(unittest.IsolatedAsyncioTestCase):
    async def test_autorole_skips_known_returnee(self):
        sticky = types.SimpleNamespace(should_skip_autorole=AsyncMock(return_value=True))
        cog = load_adapter('autorole', 'AutoRole', Base)(sticky)
        await cog.on_member_join('member')
        self.assertEqual(cog.calls, [])
        sticky.should_skip_autorole.assert_awaited_once_with('member')

    async def test_autorole_keeps_first_join(self):
        sticky = types.SimpleNamespace(should_skip_autorole=AsyncMock(return_value=False))
        cog = load_adapter('autorole', 'AutoRole', Base)(sticky)
        await cog.on_member_join('member')
        self.assertEqual(cog.calls, [('join', 'member')])

    async def test_without_sticky_autorole_behaviour_unchanged(self):
        cog = load_adapter('autorole', 'AutoRole', Base)()
        await cog.on_member_join('member')
        self.assertEqual(cog.calls, [('join', 'member')])

    async def test_quarantine_waits_and_uses_fresh_roles(self):
        sticky = types.SimpleNamespace(before_role_change=AsyncMock(return_value='fresh'))
        cog = load_adapter('quarantena', 'Quarantena', Base)(sticky)
        await cog.isolate('stale', 'reason')
        self.assertEqual(cog.calls, [('isolate', 'fresh', 'reason')])
        sticky.before_role_change.assert_awaited_once_with('stale')

    async def test_quarantine_unchanged_if_sticky_not_loaded(self):
        cog = load_adapter('quarantena', 'Quarantena', Base)()
        await cog.isolate('member', 'reason')
        self.assertEqual(cog.calls, [('isolate', 'member', 'reason')])

    async def test_reactions_get_fresh_member_after_restoration(self):
        sticky = types.SimpleNamespace(before_role_change=AsyncMock(return_value='fresh'))
        cog = load_adapter('reactionroles', 'ReactionRoles', Base)(sticky)
        member = types.SimpleNamespace(bot=False)
        self.assertEqual(await cog._member_from_payload(types.SimpleNamespace(member=member)), 'fresh')

    async def test_boost_start_and_end_coordinate_with_sticky(self):
        sticky = types.SimpleNamespace(before_role_change=AsyncMock(return_value='fresh'))
        cog = load_adapter('nitrorole', 'NitroRole', Base)(sticky)
        await cog.maybe_assign_role(None, 'stale')
        await cog.maybe_unassign_role(None, 'stale')
        self.assertEqual(cog.calls, [('add', 'fresh'), ('remove', 'fresh')])

    def test_cog_command_and_permission_declarations(self):
        src = (ROOT / 'stickyroles' / 'stickyroles.py').read_text()
        ast.parse(src)
        self.assertIn('@commands.group(name="sr"', src)
        self.assertIn('@commands.admin_or_permissions(administrator=True)', src)
        self.assertIn('register_member(**MEMBER_DEFAULTS)', src)
        self.assertIn('red_delete_data_for_user', src)


if __name__ == '__main__':
    unittest.main()
