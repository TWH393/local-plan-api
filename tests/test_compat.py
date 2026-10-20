import copy
import unittest
from plan_api.compat import ChatStream,chat_to_responses,response_to_chat
from plan_api.errors import APIError


class CompatibilityTests(unittest.TestCase):
    def test_history_tools_images_and_no_mutation(self):
        source={'model':'test','messages':[{'role':'system','content':'rules'},
            {'role':'user','content':[{'type':'text','text':'look'},{'type':'image_url','image_url':{'url':'data:image/png;base64,AA','detail':'high'}}]},
            {'role':'assistant','content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':'weather','arguments':'{"city":"KL"}'}}]},
            {'role':'tool','tool_call_id':'call1','content':'rain'}],
            'tools':[{'type':'function','function':{'name':'weather','parameters':{'type':'object','properties':{}}}}],
            'tool_choice':{'type':'function','function':{'name':'weather'}},'reasoning_effort':'medium'}
        original=copy.deepcopy(source);value=chat_to_responses(source)
        self.assertEqual(source,original)
        self.assertEqual(value['input'][0]['role'],'developer')
        self.assertEqual(value['input'][1]['content'][1]['type'],'input_image')
        self.assertEqual(value['input'][2]['call_id'],'call1')
        self.assertEqual(value['input'][3]['type'],'function_call_output')
        self.assertEqual(value['tools'][0]['name'],'weather')
        self.assertEqual(value['tool_choice'],{'type':'function','name':'weather'})

    def test_unsupported_options_and_content_fail_explicitly(self):
        for body in ({'messages':[]},{'messages':[{'role':'user','content':'hi'}],'n':2},
            {'messages':[{'role':'user','content':'hi'}],'max_tokens':20},
            {'messages':[{'role':'user','content':[{'type':'input_audio','input_audio':{}}]}]}):
            with self.assertRaises(APIError):chat_to_responses(body)

    def test_structured_output(self):
        value=chat_to_responses({'messages':[{'role':'user','content':'hi'}],
            'response_format':{'type':'json_schema','json_schema':{'name':'out','strict':True,'schema':{'type':'object'}}}})
        self.assertEqual(value['text']['format']['name'],'out')

    def test_complete_text_refusal_tools_and_usage(self):
        response={'id':'resp1','output':[{'type':'message','content':[{'type':'output_text','text':'hello'}]},
            {'type':'function_call','call_id':'c1','name':'f','arguments':'{}'}],
            'usage':{'input_tokens':20,'output_tokens':5,'total_tokens':25,'input_tokens_details':{'cached_tokens':10}}}
        value=response_to_chat(response,'model')
        self.assertEqual(value['choices'][0]['finish_reason'],'tool_calls')
        self.assertEqual(value['choices'][0]['message']['content'],'hello')
        self.assertEqual(value['usage']['prompt_tokens_details']['cached_tokens'],10)

    def test_tool_stream_indices_and_argument_fragments(self):
        stream=ChatStream('model',True);chunks=[]
        chunks+=stream.feed({'type':'response.created','response':{'id':'r1'}})
        chunks+=stream.feed({'type':'response.output_item.added','output_index':3,
            'item':{'type':'function_call','call_id':'c1','name':'weather'}})
        chunks+=stream.feed({'type':'response.function_call_arguments.delta','output_index':3,'delta':'{"city":'})
        chunks+=stream.feed({'type':'response.function_call_arguments.delta','output_index':3,'delta':'"KL"}'})
        chunks+=stream.feed({'type':'response.completed','response':{'output':[
            {'type':'function_call','call_id':'c1','name':'weather','arguments':'{"city":"KL"}'}],
            'usage':{'input_tokens':5,'output_tokens':2,'total_tokens':7}}})
        calls=[tc for c in chunks for choice in c['choices'] for tc in choice['delta'].get('tool_calls',[])]
        self.assertTrue(all(c['index']==0 for c in calls))
        self.assertEqual(''.join(c['function'].get('arguments','') for c in calls),'{"city":"KL"}')
        self.assertEqual(chunks[-2]['choices'][0]['finish_reason'],'tool_calls')
        self.assertEqual(chunks[-1]['usage']['total_tokens'],7)

    def test_terminal_only_text_and_tools(self):
        stream=ChatStream('model')
        chunks=stream.feed({'type':'response.completed','response':{'output':[
            {'type':'message','content':[{'type':'output_text','text':'done'}]},
            {'type':'function_call','call_id':'c','name':'f','arguments':'{}'}]}})
        self.assertEqual(chunks[1]['choices'][0]['delta']['content'],'done')
        self.assertEqual(chunks[2]['choices'][0]['delta']['tool_calls'][0]['index'],0)

    def test_inconsistent_tool_arguments_are_not_success(self):
        stream=ChatStream('model')
        stream.feed({'type':'response.output_item.added','output_index':0,'item':{'type':'function_call','call_id':'c','name':'f'}})
        stream.feed({'type':'response.function_call_arguments.delta','output_index':0,'delta':'wrong'})
        with self.assertRaises(APIError):stream.feed({'type':'response.completed','response':{'output':[
            {'type':'function_call','call_id':'c','name':'f','arguments':'{}'}]}})
