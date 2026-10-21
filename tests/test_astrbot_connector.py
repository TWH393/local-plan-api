import json
from pathlib import Path
import tempfile
import unittest
from plan_api.astrbot_connector import AstrBotConnector,MODEL
from plan_api.errors import APIError
from test_gateway import MemoryStore

class AstrBotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'data').mkdir()
        self.path=self.root/'data/cmd_config.json'
        self.doc={'provider_sources':[{'id':'original','key':['private']}],
            'provider':[{'id':'original/model','enable':True}],
            'agent_runner':{'runner_type':'local','config':{'model':{'provider_id':'original/model'}}},
            'platform':[{'id':'qq','enable':True}],'plugins':{'keep':True}}
        self.path.write_text(json.dumps(self.doc),encoding='utf-8-sig')
        self.c=AstrBotConnector(MemoryStore(self.root),'http://127.0.0.1:24664',root=self.root)
    def read(self):return json.loads(self.path.read_text(encoding='utf-8-sig'))
    def test_connect_idempotent_and_undo_preserve_other_settings(self):
        self.assertTrue(self.c.connect('secret')['changed'])
        self.assertFalse(self.c.connect('secret')['changed'])
        self.assertTrue(self.c.status()['needs_restart'])
        self.assertNotIn('secret',str(self.c.status()))
        doc=self.read();self.assertEqual(doc['agent_runner'],self.doc['agent_runner'])
        doc['plugins']['new']=True;self.path.write_text(json.dumps(doc),encoding='utf-8-sig')
        self.c.undo();expected=self.doc.copy();expected['plugins']={'keep':True,'new':True}
        self.assertEqual(self.read(),expected)
        self.assertTrue(self.path.read_bytes().startswith(b'\xef\xbb\xbf'))
    def test_manual_changes_and_active_default_refused(self):
        self.c.connect('secret');doc=self.read()
        doc['agent_runner']['config']['model']['provider_id']=MODEL
        self.path.write_text(json.dumps(doc))
        with self.assertRaises(APIError):self.c.undo()
        doc['agent_runner']['config']['model']['provider_id']='original/model'
        doc['provider_sources'][-1]['api_base']='http://manually-changed/v1'
        self.path.write_text(json.dumps(doc))
        with self.assertRaises(APIError):self.c.undo()
        with self.assertRaises(APIError):self.c.connect('secret')
    def test_failed_write_can_retry_without_duplicates(self):
        replace=self.c.replace
        self.c.replace=lambda *args:(_ for _ in ()).throw(OSError('locked'))
        with self.assertRaises(OSError):self.c.connect('secret')
        self.c.replace=replace;self.c.connect('secret')
        self.assertEqual(len(self.read()['provider_sources']),2)

    def test_multiple_instances_require_explicit_selection(self):
        for instance in ('one','two'):
            dest=self.root/'.astrbot_launcher/instances'/instance/'core/data'
            dest.mkdir(parents=True);(dest/'cmd_config.json').write_text(json.dumps(self.doc))
        c=AstrBotConnector(MemoryStore(self.root),'http://127.0.0.1:24664',home=self.root)
        self.assertFalse(c.status()['installed'])
        self.assertEqual(c.status()['instances'],['one','two'])
        c.select('two');c.connect('secret')
        self.assertTrue(c.status()['configured'])
        untouched=json.loads((self.root/'.astrbot_launcher/instances/one/core/data/cmd_config.json').read_text())
        self.assertEqual(untouched,self.doc)
        with self.assertRaises(APIError):c.select('one')
