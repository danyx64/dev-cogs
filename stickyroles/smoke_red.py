"""Import and construct actual Red Cogs without a Discord connection or persistent writes."""
import asyncio
import importlib
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from redbot.core import Config


async def main():
    cases = [
        ('stickyroles.stickyroles', 'StickyRoles', 'sr'),
        ('autorole.sticky_compat', 'AutoRole', 'autorole'),
        ('quarantena.sticky_compat', 'Quarantena', 'quarantena'),
        ('reactionroles.sticky_compat', 'ReactionRoles', 'reactionrole'),
        ('nitrorole.sticky_compat', 'NitroRole', 'nitrorole'),
        ('hierarchyadmin.sticky_compat', 'HierarchyAdmin', 'ha'),
        ('ownercontrol.sticky_compat', 'OwnerControl', 'ownerctl'),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        # The real Red Config constructor normally needs an installed instance.
        # Mock only storage creation, not Cog metaclasses or command registration.
        with patch.object(Config, 'get_conf', side_effect=lambda *a, **kw: MagicMock()):
            with patch('nitrorole.nitrorole.cog_data_path', return_value=Path(tmp)):
                for module_name, class_name, group_name in cases:
                    cls = getattr(importlib.import_module(module_name), class_name)
                    cog = cls(types.SimpleNamespace())
                    roots = {c.name: c for c in cog.get_commands()}
                    assert group_name in roots, (class_name, roots.keys())
                    qualified = [c.qualified_name for c in cog.walk_commands()]
                    assert len(qualified) == len(set(qualified)), qualified
                    if class_name == 'StickyRoles':
                        assert roots['sr'].get_command('attiva') is not None
                        cog.cog_unload()
                    elif class_name == 'AutoRole':
                        assert sum(event == 'on_member_join' for event, _ in cog.get_listeners()) == 1
                    elif class_name == 'OwnerControl':
                        assert roots['ownerctl'].get_command('addrole') is not None
                        assert roots['ownerctl'].get_command('removerole') is not None
                    elif class_name == 'HierarchyAdmin':
                        assert roots['ha'].get_command('give') is not None
                        assert roots['ha'].get_command('remove') is not None
                    print(f'OK {class_name}: {len(qualified)} commands; original group preserved')


if __name__ == '__main__':
    asyncio.run(main())
