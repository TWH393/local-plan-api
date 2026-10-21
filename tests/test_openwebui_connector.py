import copy,json,sqlite3,subprocess,sys,tempfile,unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from plan_api.openwebui_connector import OpenWebUIConnector,WORKER
from plan_api.errors import APIError
from test_gateway import MemoryStore

class WebUIConfigTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.db=self.root/'webui.db';self.store=MemoryStore(self.root)
  self.original={'enable':False,'api_base_urls':['https://other.example/v1'],'api_keys':['other-secret'],'api_configs':{'0':{'enable':True,'prefix_id':'other'}}}
  with closing(sqlite3.connect(self.db)) as c,c:
   c.execute('CREATE TABLE config(id INTEGER PRIMARY KEY,data TEXT,updated_at TEXT)')
   c.execute('INSERT INTO config(id,data) VALUES(1,?)',(json.dumps({'ui':{'keep':'yes'},'openai':self.original}),))
  self.restarts=0
  def runner(args,**kwargs):
   if args[1]=='inspect':return SimpleNamespace(returncode=0,stdout=json.dumps([{'Config':{'Image':'ghcr.io/open-webui/open-webui:main'},'State':{'Running':True}}]))
   if args[1]=='restart':self.restarts+=1;return SimpleNamespace(returncode=0,stdout='open-webui')
   if args[-1]==WORKER:
    code=WORKER.replace('/app/backend/data/webui.db',self.db.as_posix())
    return subprocess.run([sys.executable,'-c',code],**kwargs)
   return SimpleNamespace(returncode=0,stdout='')
  self.c=OpenWebUIConnector(self.store,runner=runner)
 def read(self):
  with closing(sqlite3.connect(self.db)) as c:return json.loads(c.execute('SELECT data FROM config').fetchone()[0])
 def test_connect_keeps_other_connections_and_undo_restores_snapshot(self):
  r=self.c.connect('local-secret');self.assertTrue(r['ready']);data=self.read()
  self.assertEqual(data['ui'],{'keep':'yes'})
  self.assertEqual(data['openai']['api_keys'],['other-secret','local-secret'])
  self.assertEqual(data['openai']['api_configs']['0'],self.original['api_configs']['0'])
  self.assertTrue(self.c.status()['configured'])
  self.assertNotIn('local-secret',str(self.c.status()))
  self.c.connect('local-secret');self.assertEqual(self.restarts,1)
  self.c.undo();self.assertEqual(self.read()['openai'],self.original)
 def test_concurrent_manual_change_refused(self):
  self.c.connect('local-secret')
  changed=self.read()['openai'];changed['api_keys'][0]='changed'
  expected=self.read()['openai'];self.c.worker({'op':'write','expected':expected,'replacement':changed})
  with self.assertRaises(APIError):self.c.undo()
  with self.assertRaises(APIError):self.c.connect('new-secret')
  self.assertEqual(self.read()['openai'],changed)
 def test_worker_rejects_stale_write(self):
  with self.assertRaises(APIError):self.c.worker({'op':'write','expected':{},'replacement':{}})
  self.assertEqual(self.read()['openai'],self.original)

 def test_reconnect_removed_connection_preserves_new_settings_and_undo(self):
  self.c.connect('local-secret')
  current=self.read()['openai']
  removed={'enable':True,'api_base_urls':[],'api_keys':[],'api_configs':{}}
  self.c.worker({'op':'write','expected':current,'replacement':removed})
  self.assertFalse(self.c.status()['configured'])
  self.c.connect('local-secret')
  self.assertTrue(self.c.status()['configured'])
  self.c.undo();self.assertEqual(self.read()['openai'],removed)
 def test_post_restart_overwrite_is_not_reported_successful(self):
  def restart():
   current=self.read()['openai']
   self.c.worker({'op':'write','expected':current,'replacement':self.original})
   return True
  self.c.restart=restart
  with self.assertRaises(APIError):self.c.connect('local-secret')
  self.assertTrue(self.store.load('openwebui-connection',{})['pending_restart'])
