import tempfile,unittest
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from plan_api.anythingllm_connector import AnythingConnector,FIELDS
from plan_api.errors import APIError
from test_gateway import MemoryStore

class AnythingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.folder=Path(self.tmp.name);self.store=MemoryStore(self.folder)
  self.c=AnythingConnector(self.store,'http://127.0.0.1:24664',self.folder)
  self.original='LLM_PROVIDER="generic-openai"\nGENERIC_OPEN_AI_BASE_PATH="https://old.example/v1"\nGENERIC_OPEN_AI_API_KEY="original-secret"\nGENERIC_OPEN_AI_MODEL_PREF="old-model"\nGENERIC_OPEN_AI_MODEL_TOKEN_LIMIT=10000\nEMBEDDING_ENGINE=native\n'
  self.c.path.write_text(self.original,encoding='utf-8')
 def apply_entry(self):
  e=self.store.data['anythingllm-connection']['entry']
  self.c.path.write_text('LLM_PROVIDER='+e['provider']+'\n'+'\n'.join(FIELDS[k]+'='+v for k,v in e['settings'].items()),encoding='utf-8')
 def test_prepare_uses_official_link_and_never_edits_app(self):
  url=self.c.connect('local-secret');args=parse_qs(urlparse(url).query,keep_blank_values=True)
  self.assertEqual(args['GenericOpenAiBasePath'],['http://127.0.0.1:24664/compat/anythingllm/v1'])
  self.assertEqual(args['GenericOpenAiKey'],['local-secret'])
  self.assertEqual(args['GenericOpenAiModelPref'],['local-plan-default'])
  self.assertEqual(self.c.path.read_text(encoding='utf-8'),self.original)
  self.assertNotIn('local-secret',str(self.c.status()))
  self.assertFalse(self.c.status()['configured'])
  self.apply_entry();self.assertTrue(self.c.status()['configured'])
  self.c.connect('local-secret');self.assertEqual(self.store.data['anythingllm-connection']['original']['settings']['GenericOpenAiKey'],'original-secret')
 def test_undo_prepares_original_without_touching_embedder(self):
  self.c.connect('local-secret');self.apply_entry();before=self.c.path.read_bytes()
  args=parse_qs(urlparse(self.c.undo()).query,keep_blank_values=True)
  self.assertEqual(args['GenericOpenAiKey'],['original-secret']);self.assertNotIn('EmbeddingEngine',args)
  self.assertEqual(self.c.path.read_bytes(),before)
  self.c.path.write_text(self.original,encoding='utf-8');self.assertTrue(self.c.status()['restored'])
 def test_modified_configuration_not_overwritten(self):
  self.c.connect('local-secret');self.c.path.write_text('LLM_PROVIDER=ollama\n',encoding='utf-8')
  with self.assertRaises(APIError):self.c.undo()
  with self.assertRaises(APIError):self.c.connect('another-secret')
 def test_missing_install_does_not_create_files(self):
  self.c.path.unlink();self.assertFalse(self.c.status()['installed'])
  with self.assertRaises(APIError):self.c.connect('secret')
  self.assertFalse(self.c.path.exists())
