import copy
import json
from pathlib import Path
import tempfile
import unittest

from plan_api.cline_connector import ClineConnector, PROVIDER_ID, FILES
from plan_api.errors import APIError
from test_gateway import MemoryStore


class ClineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name);self.store=MemoryStore(self.folder)
        self.original={
            'providers.json':{'version':1,'lastUsedProvider':'other','modes':{},'providers':{
                'other':{'settings':{'provider':'other','auth':{'accessToken':'private-oauth'}},
                         'updatedAt':'2026-10-21T00:00:00.000Z','tokenSource':'oauth'}}},
            'models.json':{'version':1,'providers':{'other':{'models':{'m':{'id':'m'}}}}}}
        for n,d in self.original.items():self.write(n,d)
        self.c=ClineConnector(self.store,'http://127.0.0.1:24664',self.folder)
    def write(self,n,d): (self.folder/n).write_text(json.dumps(d),encoding='utf-8')
    def read(self,n):return json.loads((self.folder/n).read_text(encoding='utf-8'))
    def test_connect_repeat_and_undo_preserve_accounts(self):
        self.assertTrue(self.c.connect('key')['changed'])
        self.assertTrue(self.c.status()['configured'])
        self.assertFalse(self.c.connect('key')['changed'])
        self.assertNotIn('private-oauth',str(self.c.status()))
        self.c.undo()
        for n in FILES:self.assertEqual(self.read(n),self.original[n])
    def test_undo_keeps_concurrent_oauth_refresh_and_new_provider(self):
        self.c.connect('key');doc=self.read('providers.json')
        doc['providers']['other']['settings']['auth']['accessToken']='refreshed'
        doc['providers']['new']={'settings':{'provider':'new'}}
        self.write('providers.json',doc);self.c.undo()
        doc['providers'].pop(PROVIDER_ID)
        self.assertEqual(self.read('providers.json'),doc)
    def test_modified_owned_entry_refused(self):
        self.c.connect('key');doc=self.read('models.json')
        doc['providers'][PROVIDER_ID]['models']['manual']={'id':'manual'}
        self.write('models.json',doc)
        for fn in [lambda:self.c.connect('key'),self.c.undo]:
            with self.assertRaises(APIError):fn()
        self.assertEqual(self.read('models.json'),doc)
    def test_second_file_failure_rolls_back_and_can_retry(self):
        replace=self.c.replace
        def fail(name,raw,new):
            if name=='providers.json':raise OSError('locked')
            replace(name,raw,new)
        self.c.replace=fail
        with self.assertRaises(OSError):self.c.connect('key')
        for n in FILES:self.assertEqual(self.read(n),self.original[n])
        self.c.replace=replace;self.c.connect('key')
        self.assertTrue(self.c.status()['configured'])
    def test_unknown_schema_and_existing_name_not_overwritten(self):
        doc=copy.deepcopy(self.original['models.json']);doc['version']=2;self.write('models.json',doc)
        with self.assertRaises(APIError):self.c.connect('key')
        doc['version']=1;doc['providers'][PROVIDER_ID]={'models':{}};self.write('models.json',doc)
        with self.assertRaises(APIError):self.c.connect('key')
        self.assertEqual(self.read('models.json'),doc)
