"""Dependency-free structural + behavioral tests against actual patched chat methods."""
from pathlib import Path
import ast
import importlib
import sys
import os
import types
import time
import json
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
BASE=Path(os.environ.get('BEMO_TEST_CODE_ROOT', Path(__file__).resolve().parents[2]))  # the repo root
sys.path.insert(0,str(BASE))
import config
from systems.context_policy import (
    needs_archive_memory, direct_specialist_route, tool_names_for_request,
    compact_history, authorize_route, requires_verified_computer_action,
)
from systems.routing import parse_route_signal

class QuietLogger:
    def info(self,*args,**kwargs): pass
    def debug(self,*args,**kwargs): pass
    def warning(self,*args,**kwargs): pass
    def error(self,*args,**kwargs): pass
    def exception(self,*args,**kwargs): raise AssertionError('Unexpected exception')
logger=QuietLogger()

source=(BASE/'systems/chat.py').read_text()
tree=ast.parse(source)
original_chat=next(c for c in tree.body if isinstance(c,ast.ClassDef) and c.name=='Chat')
# Extract the actual production functions to test without GPU/ALSA/Whisper.
method_names={'_shared_tool_schemas','_tool_schemas_for_active_profile','_ollama_messages','_execute_shared_tool','_recent_routing_context','_ground_execution_response','_main_response_token_budget','respond','_turn_cancelled','_save_code_blocks'}
method_names|={'_offer_just_accepted'}  # v1.28.58: _save_code_blocks asks it (review 2026-10-19)
methods=[m for m in original_chat.body if isinstance(m,ast.FunctionDef) and m.name in method_names
         or isinstance(m,ast.Assign) and getattr(m.targets[0],'id','') in {'_AGREEMENT','_OFFER_TO_WRITE'}]
classnode=ast.ClassDef(name='ChatUnderTest',bases=[],keywords=[],body=methods,decorator_list=[])
exec(compile(ast.fix_missing_locations(ast.Module(body=[classnode],type_ignores=[])),'bemo-actual-chat-methods','exec'),{
    'config':config,'logger':logger,'time':time,'json':json,'re':re,'os':os,'Path':Path,
    'tool_names_for_request':tool_names_for_request,'needs_archive_memory':needs_archive_memory,
    'direct_specialist_route':direct_specialist_route,'parse_route_signal':parse_route_signal,'authorize_route':authorize_route,'requires_verified_computer_action':requires_verified_computer_action,
    'engineering_tool_schemas':lambda:[{'function':{'name':n}} for n in ['calculate_python','solve_math','convert_units','mechanical_calculator','inspect_cad','thermal_calculator','electrical_calculator']],
    'vision_tool_schemas':lambda:[{'function':{'name':n}} for n in ['crop_zoom_image','ocr_image']],
    'diagnostics_tool_schema':lambda:{'function':{'name':'hardware_diagnostics'}},
    'reference_library_tool_schema':lambda:{'function':{'name':'technical_reference_library'}},
    'instrument_tool_schema':lambda:{'function':{'name':'instrument_telemetry'}},
    'telemetry_tool_schema':lambda:{'function':{'name':'data_logger'}},
    'State':types.SimpleNamespace(THINKING='think',RESPONDING='respond'),
    'GUI_TOOL_SCHEMA':{'type':'function','function':{'name':'gui'}},
}, namespace:={})
ChatUnderTest=namespace['ChatUnderTest']

class MockPM:
    active_slug=None
    def context_for_query(self,q):return 'PRIVATE PROJECT'

class MockLedger:
    def __init__(self): self.handoffs=[];self.complete_calls=[]
    def prompt_context(self,**kw):return ''
    def begin_task(self,*args,**kw):return {'id':'task-1'}
    def handoff(self,**kw):self.handoffs.append(kw)
    def checkpoint(self,**kw):pass
    def complete(self,**kw):self.complete_calls.append(kw)

class BaseHarness(ChatUnderTest):
    active_profile_name='normal'
    active_profile=config.MODEL_PROFILES['normal']
    active_model='qwen3-main:30b'
    _selected_image_paths=[]
    _last_agent_streamed_text=False
    _last_agent_streamed_speech=False
    stop_event=types.SimpleNamespace(is_set=lambda:False)
    cancel_turn_event=None
    project_manager=MockPM()
    memory_store=types.SimpleNamespace(maybe_backup=lambda **kw:None)
    task_ledger=MockLedger()
    chat_history=[]
    chat_history_ids=[]
    conversation_summary='OLD MEMORY SUMMARY'
    def _effective_context(self):return 8192
    def _older_memory_context(self,q):return 'OLD SEARCH RESULT'
    def _project_tool_schemas(self):return []
    def _apply_project_classification(self,q):return None
    def _record_message(self,role,content):pass
    def _set_live_assistant_text(self,content):pass
    def speak(self,*args,**kw):pass
    def _coding_spoken_text(self,text):return 'Coding done.'
    def _return_to_main_after_specialist(self,name):pass
    def _attach_screenshot_for_vision(self,user_input):return None  # v1.28.57 (tested in test_ready_and_vision)
    def _switch_profile(self,profile,**kw):
        self.switch_calls.append(profile)
        self.active_profile_name=profile
        self.active_profile=config.MODEL_PROFILES[profile]
        self.active_model=self.active_profile['model']
        return True,'done'
    def _run_model_agent(self,q,*,suppress_handoffs=False):
        self.role_calls.append((self.active_profile_name, suppress_handoffs))
        return 'Done with request.'

class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.c=BaseHarness();self.c.task_ledger=MockLedger();self.c.chat_history=[];self.c.chat_history_ids=[];self.c.switch_calls=[];self.c.role_calls=[]
    def test_static_profiles_compact(self):
        for key, profile in config.MODEL_PROFILES.items():
            # v1.28.8: +~220 chars for the shared grounding rule. v1.28.42: +~200 for the
            # detected capability line (Main had improvised "I can fix broken apps").
            self.assertLess(len(profile['system_prompt']),2200,(key,len(profile['system_prompt'])))
            self.assertNotIn('Canonical wake phrase:',profile['system_prompt'])
            self.assertNotIn('Persistent task/handoff',profile['system_prompt'])
        self.assertIn('Supervisor',config.MODEL_PROFILES['normal']['system_prompt'])
    def test_greeting_only_handoff_tool(self):
        self.assertEqual([x['function']['name'] for x in self.c._tool_schemas_for_active_profile('How are you today?')],[])
    def test_tools_on_computer_request(self):
        names=[x['function']['name'] for x in self.c._tool_schemas_for_active_profile('Open my browser')]
        self.assertIn('computer_control',names);self.assertNotIn('hardware_diagnostics',names)
    def test_main_cannot_inline_engineering(self):
        self.assertFalse(config.MODEL_PROFILES['normal']['diagnostic_tools'])
        self.assertFalse(config.MODEL_PROFILES['normal']['reference_tools'])
    def test_tool_request_recovery(self):
        self.c._requested_local_tool_categories={'computer'}
        names=[x['function']['name'] for x in self.c._tool_schemas_for_active_profile('Do the thing.') ]
        self.assertIn('computer_control',names)
    def test_engineering_tools_selected(self):
        self.c.active_profile_name='engineering';self.c.active_profile=config.MODEL_PROFILES['engineering']
        names=[x['function']['name'] for x in self.c._tool_schemas_for_active_profile('calculate the shaft torque')]
        self.assertIn('mechanical_calculator',names)
    def test_unrelated_engineering_tools_not_always_selected(self):
        self.c.active_profile_name='engineering';self.c.active_profile=config.MODEL_PROFILES['engineering']
        names=[x['function']['name'] for x in self.c._tool_schemas_for_active_profile('calculate the shaft torque')]
        self.assertNotIn('technical_reference_library',names)
    def test_coding_without_project_still_has_computer_control(self):
        self.c.active_profile_name='coding';self.c.active_profile=config.MODEL_PROFILES['coding']
        names=[x['function']['name'] for x in self.c._tool_schemas_for_active_profile('Refactor my Python source code')]
        self.assertIn('computer_control',names)
    def test_vision_tools_selected(self):
        # One vision brain since 2026-10-24 (Fast Vision dropped); Ollama refuses tools for it (checklist B3).
        self.c.active_profile_name='deep-vision';self.c.active_profile=config.MODEL_PROFILES['deep-vision']
        self.assertFalse(config.MODEL_PROFILES['deep-vision'].get('tools_supported', True))
    def test_prompt_history_no_retrieval_on_greeting(self):
        messages=self.c._ollama_messages('How are you today?')
        txt=' '.join(str(x['content']) for x in messages)
        self.assertNotIn('OLD MEMORY SUMMARY',txt)
        self.assertNotIn('OLD SEARCH RESULT',txt)
        self.assertLess(len(txt),2350)  # v1.28.8 grounding rule + host facts; v1.28.42 capability line
    def test_history_archive_when_asked(self):
        messages=self.c._ollama_messages('Do you remember what we talked about last time?')
        txt=' '.join(str(x['content']) for x in messages)
        self.assertIn('OLD MEMORY SUMMARY',txt)
        self.assertIn('OLD SEARCH RESULT',txt)
    def test_schemas_reserve_context_budget(self):
        self.c.chat_history=[{'role':'user' if n%2 else 'assistant','content':str(n)+'x'*1800} for n in range(8)]
        full=self.c._ollama_messages('Hello',tool_schema_chars=0)
        reduced=self.c._ollama_messages('Hello',tool_schema_chars=16000)
        full_chars=sum(len(str(m['content'])) for m in full)
        reduced_chars=sum(len(str(m['content'])) for m in reduced)
        self.assertLess(reduced_chars,full_chars)
    def test_history_append_prefix_stable(self):
        self.c.chat_history=[{'role':'user','content':'Hi'},{'role':'assistant','content':'Hello'}]
        a=self.c._ollama_messages('Next question')
        self.c.chat_history.append({'role':'user','content':'Next question'})
        self.c.chat_history.append({'role':'assistant','content':'A response'})
        b=self.c._ollama_messages('Another question')
        self.assertEqual(a[:3],b[:3])
    def test_prior_session_loaded_to_display_only(self):
        self.assertIn('self.chat_history = []',source)
        self.assertIn('recent = self.memory_store.load_recent(10)',source)
    def test_greeting_no_switch(self):
        self.c.respond('How are you?')
        self.assertEqual(self.c.switch_calls,[])
        self.assertEqual(self.c.role_calls,[('normal',False)])
    def test_automatic_engineering_handoff(self):
        self.c.respond('Calculate the shaft torque and bearing load')
        self.assertEqual(self.c.switch_calls,['engineering'])
        self.assertEqual(self.c.role_calls,[('engineering',False)])
        self.assertEqual(self.c.task_ledger.handoffs[0]['to_role'],'ENGINEERING')
    def test_automatic_coding_handoff(self):
        self.c.respond('Refactor my Python source code')
        self.assertEqual(self.c.switch_calls,['coding'])
        self.assertEqual(self.c.role_calls,[('coding',False)])
    def test_automatic_vision_handoff(self):
        self.c.task_ledger.begin_task=lambda *a,**k:{'id':'t','selected_assets':[{'kind':'image','path':'/tmp/screenshot.png'}]}
        self.c.respond('What is shown here?')
        self.assertEqual(self.c.switch_calls,['deep-vision'])
    def test_main_model_route_signal_still_works(self):
        self.c._run_model_agent=lambda q, **kw:'ROUTE: ENGINEERING' if not kw.get('suppress_handoffs') else 'Direct answer.'
        self.c.respond('Can you help with this?')
        self.assertEqual(self.c.switch_calls,[])
    def test_missing_model_switch_fails_without_fake_success(self):
        self.c._switch_profile=lambda profile:(False,'Could not load specialist')
        self.c.respond('Calculate the shaft torque')
        self.assertEqual(self.c.task_ledger.complete_calls,[])

    def test_execution_claim_is_grounded_without_tool_evidence(self):
        self.c._run_model_agent=lambda q, **kw: "It's running right now."
        self.c.respond('Boot that program now')
        result=self.c.task_ledger.complete_calls[-1]['final_result']
        self.assertIn('did not actually run', result)
        self.assertNotIn("It's running", result)

    def test_code_followup_context_routes_back_to_coding(self):
        self.c.chat_history=[
            {'role':'user','content':'Make me a Python time program'},
            {'role':'assistant','content':'I wrote /tmp/time_teller.py with Python.'},
        ]
        self.c.respond('Can you make it an animated clock please?')
        self.assertEqual(self.c.switch_calls,['coding'])
        self.assertEqual(self.c.role_calls,[('coding',False)])

    def test_compaction_batch_preserves_recent(self):
        msgs=[{'role':'user','content':str(i)} for i in range(33)]
        dropped=compact_history(msgs,max_messages=32,char_budget=999999,batch=8)
        self.assertEqual(dropped,8);self.assertEqual(len(msgs),25)
        self.assertEqual(msgs[-1]['content'],'32')
    def test_context_feature_flags(self):
        self.assertFalse(config.AUTO_LLM_MEMORY_MAINTENANCE)
        self.assertFalse(config.AUTO_PROJECT_CLASSIFICATION_USE_LLM)
        # v1.28.57: on, so "ready" is only said after the warm-up (2026-10-19: "ready" too early).
        self.assertTrue(config.RUNTIME_WARM_ACTIVE_PROMPT_ON_START)
    def test_project_context_if_active(self):
        self.c.project_manager=MockPM();self.c.project_manager.active_slug='test-project'
        self.c._ollama_messages('Please edit the project source')
    def test_data_retention_call_remains(self):
        self.assertIn('self.memory_store.add_message',source)
        self.assertIn('self.memory_store.maybe_backup',source)
    def test_piper_streaming_unmodified(self):
        self.assertIn('PiperSpeechEngine',source)
        self.assertIn('Live PCM audio stream',source)


class HandoffRegressionTests(unittest.TestCase):
    def setUp(self):
        self.c = BaseHarness()
        self.c.task_ledger = MockLedger()
        self.c.switch_calls = []
        self.c.role_calls = []
        self.c._selected_image_paths = []
        self.c._requested_route_label = None
        self.c._routing_hops_in_turn = 0
        self.c._active_user_text = ""
    def test_california_cannot_start_handoff(self):
        self.assertEqual(self.c._tool_schemas_for_active_profile("I'm in California."), [])
        self.assertEqual(self.c._tool_schemas_for_active_profile("What's the strongest laser I can legally buy?"), [])
        for target in ['ENGINEERING', 'HEAVY_CODING', 'FAST_VISION', 'DEEP_TECHNICAL_VISION']:
            self.assertFalse(authorize_route("I'm in California.",target)[0])
    def test_no_fake_image_from_model_reason(self):
        self.c._active_user_text = "I'm in California."
        out = self.c._execute_shared_tool('request_capability_handoff', {
            'capability':'GENERAL_IMAGE_READ',
            'reason':'Need to verify legal paperwork and official documents.'})
        self.assertIn('declined', out)
        self.assertIsNone(self.c._requested_route_label)
        self.assertEqual(self.c.task_ledger.handoffs,[])
    def test_no_doc_search_to_engineering(self):
        self.c._active_user_text = "What's the strongest laser I can legally buy?"
        out = self.c._execute_shared_tool('request_capability_handoff', {
            'capability':'DOCUMENT_SEARCH', 'reason':'Need up to date laws.'})
        self.assertIn('declined',out)
        self.assertIsNone(self.c._requested_route_label)
    def test_normal_laser_followup_not_switched_despite_route_text(self):
        self.c._run_model_agent=lambda q, **kw: 'ROUTE:FAST_VISION' if not kw.get('suppress_handoffs') else 'I cannot check live regulations.'
        self.c.respond("I'm in California.")
        self.assertEqual(self.c.switch_calls, [])
        self.assertEqual(self.c.task_ledger.complete_calls[-1]['final_result'], 'I cannot check live regulations.')
    def test_engineer_handoff_guard_accepts_real_task(self):
        self.c._active_user_text='Calculate shaft torque at 16 hp and 3600 RPM'
        out=self.c._execute_shared_tool('request_capability_handoff',{'capability':'CAD_MEASURE','reason':'need calculation'})
        self.assertEqual(self.c._requested_route_label,'ENGINEERING')
        self.assertIn('prepared', out)
    def test_image_selection_permits_one_vision_route(self):
        self.c._selected_image_paths=['/tmp/actual_image.png']
        self.c._active_user_text='Analyze the attached picture'
        out=self.c._execute_shared_tool('request_capability_handoff',{'capability':'GENERAL_IMAGE_READ','reason':'describe visual evidence'})
        self.assertEqual(self.c._requested_route_label,'DEEP_TECHNICAL_VISION')  # one vision brain since 2026-10-24
    def test_image_explicit_but_not_selected_is_denied(self):
        self.assertFalse(authorize_route('Analyze a picture', 'FAST_VISION', images_selected=False)[0])
    def test_specialists_cannot_chain(self):
        for profile in ['engineering', 'coding', 'deep-vision']:
            self.assertFalse(authorize_route('Calculate shaft torque', 'ENGINEERING', current_profile=profile, hops=1)[0])
    def test_specialist_real_second_route_blocked_and_fallback(self):
        self.c._run_model_agent=lambda q, **kw: 'ROUTE:FAST_VISION' if self.c.active_profile_name=='engineering' and not kw.get('suppress_handoffs') else 'Result is 31.65 Nm.'
        self.c.respond('Calculate shaft torque from 16 hp at 3600 RPM')
        self.assertEqual(self.c.switch_calls,['engineering'])
        self.assertEqual(self.c.task_ledger.complete_calls[-1]['final_result'], 'Result is 31.65 Nm.')
    def test_one_hop_configuration(self):
        self.assertEqual(config.MODEL_ROUTE_MAX_HOPS,1)
    def test_no_handoff_tool_advertised_to_spec(self):
        self.c.active_profile_name='deep-vision'; self.c.active_profile=config.MODEL_PROFILES['deep-vision']
        names=[x['function']['name'] for x in self.c._tool_schemas_for_active_profile('Analyze this photo')]
        self.assertNotIn('request_capability_handoff',names)
    def test_legitimate_coding_not_blocked(self):
        ok, _ = authorize_route('Refactor my Python source code', 'HEAVY_CODING')
        self.assertTrue(ok)
    def test_fake_document_search_without_user_request_denied(self):
        ok, _=authorize_route('Tell me a short joke', 'ENGINEERING')
        self.assertFalse(ok)
    def test_unrelated_specialist_not_offered_on_factual_laser(self):
        names=[x['function']['name'] for x in self.c._tool_schemas_for_active_profile('What is the strongest laser I can buy legally?')]
        self.assertNotIn('request_capability_handoff',names)
        self.assertNotIn('request_local_tools',names)
    def test_no_web_tools_claimed(self):
        self.c._requested_local_tool_categories=set()
        self.c._active_user_text="I'm in California."
        out=self.c._execute_shared_tool('request_local_tools',{'category':'references'})
        self.assertIn("isn't installed",out)

if __name__=="__main__": unittest.main(verbosity=2)
