from pathlib import Path
import tempfile
import unittest
from ruamel.yaml import YAML

from plan_api.harness_connector import HarnessConnector, ROUTE, KEY_REF
from plan_api.errors import APIError
from test_gateway import MemoryStore


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name);self.profile=self.home/'profiles/desktop';self.profile.mkdir(parents=True)
        (self.profile/'cordis.yml').write_text('[]\n')
        (self.profile/'package.json').write_text('{}')
        (self.profile/'cordis.patch.yml').write_text('# Keep plugin settings\n- id: other\n  config:\n    enabled: true\n')
        (self.home/'.credentials.yaml').write_text('version: 1\nrecords:\n  account/default:\n    token: keep-private\n')
        self.store=MemoryStore(self.home);self.c=HarnessConnector(self.store,'http://127.0.0.1:24664',self.home)
    def read(self,path):return YAML().load(path.read_text())
    def test_connect_undo_keep_account_and_plugin(self):
        self.assertTrue(self.c.connect('secret')['changed'])
        self.assertTrue(self.c.status()['configured'])
        self.assertFalse(self.c.connect('secret')['changed'])
        self.assertNotIn('keep-private',str(self.c.status()))
        self.c.undo()
        self.assertEqual(self.read(self.c.patch),[{'id':'other','config':{'enabled':True}}])
        self.assertEqual(self.read(self.c.credentials),{'version':1,'records':{'account/default':{'token':'keep-private'}}})
        self.assertIn('# Keep plugin settings',self.c.patch.read_text())
    def test_undo_keeps_new_providers_and_refreshed_account(self):
        self.c.connect('secret');raw,d,y=self.c.read(self.c.patch,[])
        d[-1]['config']['providers']['other-route']={'api':'openai-completions','models':[{'id':'m'}]}
        self.c.replace(self.c.patch,raw,d,y)
        raw,d,y=self.c.read(self.c.credentials,{})
        d['records']['account/default']['token']='refreshed'
        self.c.replace(self.c.credentials,raw,d,y);self.c.undo()
        self.assertIn('other-route',self.read(self.c.patch)[-1]['config']['providers'])
        self.assertEqual(self.read(self.c.credentials)['records']['account/default']['token'],'refreshed')
    def test_manual_edit_refused_and_writer_lock_respected(self):
        lock=Path(str(self.profile/'package.json')+'.lock');lock.write_text('1\n')
        with self.assertRaises(APIError):self.c.connect('secret')
        lock.unlink();self.c.connect('secret')
        raw,d,y=self.c.read(self.c.credentials,{});d['refs'][KEY_REF]='changed'
        self.c.replace(self.c.credentials,raw,d,y)
        with self.assertRaises(APIError):self.c.undo()
        with self.assertRaises(APIError):self.c.connect('secret')
    def test_partial_profile_write_can_retry(self):
        replace=self.c.replace
        def fail(path,*args):
            if path==self.c.patch:raise OSError('locked')
            return replace(path,*args)
        self.c.replace=fail
        with self.assertRaises(OSError):self.c.connect('secret')
        self.assertFalse(self.c.status()['configured'])
        self.c.replace=replace;self.c.connect('secret')
        self.assertTrue(self.c.status()['configured'])
