"""Finite compatibility commands require existing reader ownership state."""
import contextlib,io,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from french_notes.telegram_bot.__main__ import main,StartupError
from french_notes.app import main as editor_main

class EntrypointPathTests(unittest.TestCase):
    def test_finite_preflight_cannot_create_missing_reader_namespace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);notes=root/'notes';notes.mkdir();(notes/'Practice.txt').write_text('Bonjour\n');state=root/'missing'
            with patch('sys.argv',['french-reader','--data-dir',str(notes),'--state-dir',str(state),'--preflight']):
                with self.assertRaisesRegex(StartupError,'missing'):main()
            self.assertFalse(state.exists())
    def test_explicit_unregistered_paths_cannot_reach_legacy_polling(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);notes=root/'notes';notes.mkdir();state=root/'state'
            with patch('sys.argv',['french-reader','--data-dir',str(notes),'--state-dir',str(state)]):
                with self.assertRaisesRegex(StartupError,'registered canonical'):main()
            self.assertFalse(state.exists())

    def canonical_fixture(self,root):
        notes=root/'notes';notes.mkdir();(notes/'Practice.txt').write_text('Bonjour    |    سلام    |    Hello\n',encoding='utf-8')
        manifest={'notes_dir':str(notes),'data_dir':str(root/'reader'),'editor_state_dir':str(root/'editor'),
                  'history_path':str(root/'editor/history.json')}
        return manifest,SimpleNamespace(root=root,require_state=lambda identity:manifest)

    def test_explicit_root_maintenance_uses_its_own_lease_and_editor_guard(self):
        from french_notes.telegram_bot.state import State
        from french_notes.telegram_bot.refresh import LibraryManager
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);manifest,installation=self.canonical_fixture(root)
            state=State(root/'reader',root/'notes');manager=LibraryManager(root/'notes',state)
            manager.publish(manager.candidate());state.close()
            def resolve(value=None):
                if value is None:
                    return SimpleNamespace(root=root/'different-installation',bot=lambda identity:manifest)
                self.assertEqual(value,str(root))
                return installation
            with patch('sys.argv',['french-reader','--root',str(root),'--preflight']),\
                 patch('bot_center_runtime.paths.Installation',side_effect=resolve),\
                 patch('bot_center_runtime.locks.FileLease',return_value=contextlib.nullcontext()) as lease,\
                 patch('french_notes.telegram_bot.__main__.ReadOnlyGuard') as guard,\
                 contextlib.redirect_stdout(io.StringIO()):
                main()
            lease.assert_called_once_with(root/'data/maintenance.lock')
            guard.assert_called_once_with(manifest['notes_dir'],manifest['editor_state_dir'])

    def test_root_maintenance_rejects_other_reader_namespace_before_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);_,installation=self.canonical_fixture(root);other=root/'other-reader'
            with patch('sys.argv',['french-reader','--root',str(root),'--state-dir',str(other),'--preflight']),\
                 patch('bot_center_runtime.paths.Installation',return_value=installation):
                with self.assertRaisesRegex(StartupError,'preserved reader state'):main()
            self.assertFalse(other.exists())

    def test_canonical_editor_missing_directory_blocks_without_initializing_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);_,installation=self.canonical_fixture(root)
            with patch('sys.argv',['french-editor','--root',str(root),'--no-browser']),\
                 patch('bot_center_runtime.paths.Installation',return_value=installation):
                with self.assertRaisesRegex(RuntimeError,'editor state is missing'):editor_main()
            self.assertFalse((root/'editor').exists())

    def test_canonical_editor_missing_declared_history_blocks_before_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);manifest,installation=self.canonical_fixture(root)
            manifest['required_editor_state']=['history.json'];(root/'editor').mkdir()
            with patch('sys.argv',['french-editor','--root',str(root),'--no-browser']),\
                 patch('bot_center_runtime.paths.Installation',return_value=installation):
                with self.assertRaisesRegex(RuntimeError,'editor state is missing'):editor_main()
            self.assertEqual(list((root/'editor').iterdir()),[])

    def test_canonical_editor_rejects_override_instead_of_creating_other_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);_,installation=self.canonical_fixture(root);other=root/'other-editor'
            with patch('sys.argv',['french-editor','--root',str(root),'--state-dir',str(other),'--no-browser']),\
                 patch('bot_center_runtime.paths.Installation',return_value=installation):
                with self.assertRaisesRegex(RuntimeError,'preserved canonical editor state'):editor_main()
            self.assertFalse(other.exists())

if __name__=='__main__':unittest.main()
