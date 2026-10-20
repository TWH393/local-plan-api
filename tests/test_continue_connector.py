from pathlib import Path
import tempfile
import unittest

from ruamel.yaml import YAML

from plan_api.continue_connector import ContinueConnector,MODEL_ALIAS,MODEL_NAME
from plan_api.errors import APIError
from test_gateway import MemoryStore


class ContinueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=MemoryStore(self.root)
        (self.root/'.vscode/extensions/continue.continue-2.0.0').mkdir(parents=True)
        self.connector=ContinueConnector(self.store,'http://127.0.0.1:24664',home=self.root,config_dir=self.root/'.continue')

    def existing(self,text):
        self.connector.folder.mkdir(exist_ok=True)
        self.connector.path.write_bytes(text.encode('utf-8'))

    def load(self):return YAML().load(self.connector.path)

    def test_status_never_creates_files_or_secrets(self):
        before=dict(self.store.data)
        status=self.connector.status()
        self.assertTrue(status['installed']);self.assertFalse(status['configured'])
        self.assertFalse(self.connector.folder.exists());self.assertEqual(self.store.data,before)

    def test_old_managed_name_upgrades_without_duplicate_and_can_undo(self):
        from unittest.mock import patch
        original='name: Existing\nschema: v1\nmodels: []\n'
        self.existing(original)
        with patch('plan_api.continue_connector.MODEL_NAME','ChatGPT 套餐 · Local Plan API'):
            self.connector.connect('fake-key')
        self.connector.connect('fake-key')
        self.assertEqual(len(self.load()['models']),1)
        self.assertEqual(self.load()['models'][0]['name'],'ChatGPT · Local Plan API')
        self.assertTrue(self.connector.status()['configured'])
        self.connector.undo()
        self.assertEqual(self.connector.path.read_text(encoding='utf-8'),original)

    def test_new_config_alias_capabilities_and_idempotency(self):
        result=self.connector.connect('fake-key')
        self.assertTrue(result['changed']);entry=self.load()['models'][0]
        self.assertEqual(entry['model'],MODEL_ALIAS)
        self.assertEqual(entry['capabilities'],['tool_use','image_input'])
        self.assertEqual(entry['apiKey'],'fake-key')
        before=self.connector.path.read_bytes()
        self.assertFalse(self.connector.connect('fake-key')['changed'])
        self.assertEqual(self.connector.path.read_bytes(),before)
        self.assertTrue(self.connector.status()['configured'])
        self.connector.undo();self.assertFalse(self.connector.path.exists())

    def test_other_models_comments_and_rules_preserved_exact_undo(self):
        original='# user comment\r\nname: "Main Config"\r\nversion: 1.0.0\r\nschema: v1\r\nmodels:\r\n  - name: Other\r\n    provider: openai\r\n    model: other\r\n    apiKey: original-secret\r\nrules:\r\n  - Never delete files\r\n'
        self.existing(original)
        self.connector.connect('new-key')
        text=self.connector.path.read_text(encoding='utf-8');doc=self.load()
        self.assertIn('# user comment',text)
        self.assertEqual(doc['models'][1]['apiKey'],'original-secret')
        self.assertEqual(doc['rules'],['Never delete files'])
        self.assertNotIn('new-key',str(self.connector.status()))
        self.connector.undo();self.assertEqual(self.connector.path.read_bytes(),original.encode())

    def test_undo_preserves_edits_made_after_connection(self):
        self.connector.connect('key')
        text=self.connector.path.read_text(encoding='utf-8')+'\nrules:\n  - My new rule\n'
        self.connector.path.write_text(text,encoding='utf-8')
        self.connector.undo();doc=self.load()
        self.assertEqual(doc['rules'],['My new rule']);self.assertEqual(doc['models'],[])

    def test_reconnect_after_external_edits_never_loses_them_on_undo(self):
        self.connector.connect('key')
        text=self.connector.path.read_text(encoding='utf-8')+'\nrules:\n  - Saved later\n'
        self.connector.path.write_text(text,encoding='utf-8')
        self.connector.connect('rotated')
        self.connector.undo()
        self.assertEqual(self.load()['rules'],['Saved later'])

    def test_invalid_yaml_and_name_collision_are_not_overwritten(self):
        for original in ('models: [oops',f'name: Test\nschema: v1\nmodels:\n  - name: "{MODEL_NAME}"\n    model: user-model\n'):
            self.existing(original)
            with self.assertRaises(APIError):self.connector.connect('key')
            self.assertEqual(self.connector.path.read_text(),original)

    def test_changed_managed_entry_not_deleted_by_undo(self):
        self.connector.connect('key')
        text=self.connector.path.read_text(encoding='utf-8').replace('local-plan-default','user-choice')
        self.connector.path.write_text(text,encoding='utf-8')
        with self.assertRaises(APIError):self.connector.undo()
        self.assertIn('user-choice',self.connector.path.read_text())

    def test_external_change_detected_before_replace(self):
        self.existing('name: Original\n')
        original=self.connector.path.read_bytes()
        self.connector.path.write_text('name: Changed\n')
        with self.assertRaises(APIError):self.connector.replace(original,b'name: New\n',True)
        self.assertEqual(self.connector.path.read_text(),'name: Changed\n')

    def test_key_rotation_updates_only_our_entry(self):
        self.existing('name: Original\nmodels:\n  - name: Other\n    apiKey: other-key\n')
        self.connector.connect('first');self.connector.connect('second')
        self.assertEqual(self.load()['models'][0]['apiKey'],'second')
        self.assertEqual(self.load()['models'][1]['apiKey'],'other-key')

    def test_configured_entry_relocated_preserves_user_name(self):
        self.connector.connect('key')
        state=self.store.load('continue-connection',{})
        self.assertTrue(state['restore_exact'])
        self.connector.undo()
        self.assertFalse(self.connector.status()['can_undo'])
