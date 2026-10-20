import unittest
from unittest.mock import patch
from plan_api.startup import Startup, RUN_KEY, VALUE_NAME, startup_command

class StartupTests(unittest.TestCase):
    def test_toggle_only_owns_one_run_value(self):
        import winreg
        from contextlib import nullcontext
        values={'OtherApp':'leave me'}
        def read(key,name):
            if name not in values: raise FileNotFoundError
            return values[name],winreg.REG_SZ
        def write(key,name,reserved,kind,value): values[name]=value
        def delete(key,name):
            if name not in values: raise FileNotFoundError
            del values[name]
        with patch.object(winreg,'OpenKey',return_value=nullcontext('key')), patch.object(winreg,'CreateKeyEx',return_value=nullcontext('key')), patch.object(winreg,'QueryValueEx',side_effect=read), patch.object(winreg,'SetValueEx',side_effect=write), patch.object(winreg,'DeleteValue',side_effect=delete):
            s=Startup(24664)
            self.assertFalse(s.status()['enabled'])
            self.assertTrue(s.set_enabled(True)['enabled'])
            self.assertIn('--no-browser',values[VALUE_NAME])
            self.assertIn('24664',values[VALUE_NAME])
            self.assertFalse(s.status()['needs_update'])
            values[VALUE_NAME]='old path'
            self.assertTrue(s.status()['needs_update'])
            self.assertFalse(s.set_enabled(False)['enabled'])
            s.set_enabled(False)
            self.assertEqual(values,{'OtherApp':'leave me'})

    def test_frozen_command_quotes_path(self):
        with patch('sys.frozen',True,create=True), patch('sys.executable',r'C:\Program Files\LocalPlanAPI\LocalPlanAPI.exe'):
            self.assertEqual(startup_command(24664),'"C:\\Program Files\\LocalPlanAPI\\LocalPlanAPI.exe" --no-browser --port 24664')
