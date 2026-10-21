import unittest
from plan_api.model_follow import follow_latest

class FollowTests(unittest.TestCase):
 def choices(self,*names):return [{'id':x} for x in names]
 def test_same_family_numeric_upgrade_only(self):
  s={'selected_model':'gpt-6.9-luna','auto_follow':True}
  self.assertTrue(follow_latest(s,self.choices('gpt-6.10-luna','gpt-9-sol','gpt-7-luna-preview','gpt-7-luna-2026-01-01')))
  self.assertEqual(s['selected_model'],'gpt-6.10-luna')
  self.assertEqual(s['model_update']['from'],'gpt-6.9-luna')
 def test_no_downgrade_cross_family_or_disabled_update(self):
  for enabled in (True,False):
   s={'selected_model':'gpt-7-luna','auto_follow':enabled}
   self.assertFalse(follow_latest(s,self.choices('gpt-6-luna','gpt-9-sol')))
  s={'selected_model':'gpt-6-luna','auto_follow':False}
  self.assertFalse(follow_latest(s,self.choices('gpt-7-luna')))
 def test_removed_model_upgrades_but_unknown_families_do_not(self):
  s={'selected_model':'gpt-6-sol','auto_follow':True}
  self.assertTrue(follow_latest(s,self.choices('gpt-6.1-sol')))
  s={'selected_model':'custom-latest','auto_follow':True}
  self.assertFalse(follow_latest(s,self.choices('gpt-8-luna')))
