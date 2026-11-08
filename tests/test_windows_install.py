import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from agent_app.runtime import app_root
from agent_app import startup


class WindowsInstallTests(unittest.TestCase):
    def test_frozen_data_survives_binary_location_changes(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(sys,'frozen',True,create=True), patch.dict(os.environ,{'LOCALAPPDATA':folder},clear=True):
            with patch.object(sys,'executable',str(Path(folder)/'Programs/CodeCompanion/app.exe')):
                first=app_root()
            with patch.object(sys,'executable',str(Path(folder)/'updated/app.exe')):
                self.assertEqual(first,app_root())
            self.assertEqual(first,Path(folder)/'CodeCompanion')

    def test_logon_command_quotes_spaces_and_toggle_only_owns_its_value(self):
        with tempfile.TemporaryDirectory(prefix='companion space ') as folder:
            exe=Path(folder)/'Code Companion.exe';exe.touch()
            reg=MagicMock();values={'OtherApp':'untouched'}
            reg.QueryValueEx.side_effect=lambda key,name:(values[name],1) if name in values else (_ for _ in ()).throw(FileNotFoundError())
            reg.SetValueEx.side_effect=lambda key,name,reserved,kind,value:values.__setitem__(name,value)
            reg.DeleteValue.side_effect=lambda key,name:values.pop(name)
            with patch.dict(sys.modules,{'winreg':reg}),patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(exe)):
                startup.set_enabled(True)
                self.assertEqual(values['CodeCompanion'],f'"{exe}" --startup')
                self.assertTrue(startup.is_enabled())
                startup.set_enabled(False)
                self.assertFalse(startup.is_enabled())
                self.assertEqual(values,{'OtherApp':'untouched'})
