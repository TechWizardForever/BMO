import ast
import os
from pathlib import Path
import re
import sys
import types
import unittest

BASE=Path(os.environ.get('BEMO_TEST_CODE_ROOT','/mnt/data/bemo-v1284-build/testroot'))
sys.path.insert(0,str(BASE))
import config
from voice_commands import VoiceSessionGate
from systems.voice import prepare_spoken_text

chat_source=(BASE/'systems/chat.py').read_text()
chat_tree=ast.parse(chat_source)
chat_cls=next(n for n in chat_tree.body if isinstance(n,ast.ClassDef) and n.name=='Chat')
method_names={'_profile_spoken_name','_confirm_specialist_switch','_confirm_by_voice','_switch_profile','_return_to_main_after_specialist','_extract_switch_target','_match_profile_name','_normalize_command_text'}
methods=[n for n in chat_cls.body if isinstance(n,ast.FunctionDef) and n.name in method_names]
mini=ast.ClassDef(name='ChatMini',bases=[],keywords=[],body=methods,decorator_list=[])

class State:
    READY='ready'; THINKING='thinking'; RESPONDING='responding'; LISTENING='listening'
class Logger:
    def info(self,*a,**k): pass
    def warning(self,*a,**k): pass
    def error(self,*a,**k): pass
    def exception(self,*a,**k): raise AssertionError(a)
class Clock:
    def __init__(self): self.now=0
    def monotonic(self): self.now += 0.25; return self.now
clock=Clock()
ns={'config':config,'State':State,'logger':Logger(),'time':clock,'re':re,'available_ram_gb':lambda:16}
exec(compile(ast.fix_missing_locations(ast.Module(body=[mini],type_ignores=[])),'chat-mini','exec'),ns)
ChatMini=ns['ChatMini']

class Harness(ChatMini):
    def __init__(self,responses=None,active='normal'):
        self.active_profile_name=active
        self._active_microphone=object()
        self.stop_event=types.SimpleNamespace(is_set=lambda:False)
        self.voice_gate=VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES)
        self.responses=iter(responses or [])
        self.spoken=[]
        self.switch_calls=[]
        self.state=State.READY
    @property
    def active_profile(self): return config.MODEL_PROFILES[self.active_profile_name]
    @property
    def active_model(self): return self.active_profile['model']
    def listen(self,source):
        try: return next(self.responses)
        except StopIteration:
            clock.now += 25
            return None
    def _apply_command_gate(self,text): return self.voice_gate.apply(text)
    def speak(self,text,*a,**k): self.spoken.append(str(text))
    def _is_model_installed(self,model): return True
    def _begin_model_switch_visual(self,*a,**k): pass
    def _finish_model_switch_visual(self,*a,**k): pass
    def _supervisor_switch_model(self,target_model,**kw):
        self.switch_calls.append(target_model); return True
    def _unload_ollama_model(self,*a,**k): raise AssertionError('should use supervisor mock')
    def _warm_runtime_prompt(self,**k): pass  # v1.28.9 background warm-up


render_source=(BASE/'systems/render.py').read_text()
render_tree=ast.parse(render_source)
render_cls=next(n for n in render_tree.body if isinstance(n,ast.ClassDef) and n.name=='FaceRenderer')
render_methods=[n for n in render_cls.body if isinstance(n,ast.FunctionDef) and n.name in {'_reset_crt_static_refresh','_crt_static_refresh_active'}]
render_mini=ast.ClassDef(name='RenderMini',bases=[],keywords=[],body=render_methods,decorator_list=[])
rns={'config':config,'time':clock}
exec(compile(ast.fix_missing_locations(ast.Module(body=[render_mini],type_ignores=[])),'render-mini','exec'),rns)
RenderMini=rns['RenderMini']

class V1284Tests(unittest.TestCase):
    def setUp(self): clock.now=0
    def test_requested_face_color(self):
        self.assertEqual(config.FACE_BACKGROUND_COLOR_HEX.lower(),'#a3e6ca')
        self.assertEqual(tuple(config.FACE_BACKGROUND_COLOR),(163,230,202))
    def test_crt_protection_timing(self):
        self.assertTrue(config.ENABLE_CRT_SCREENSAVER)
        self.assertTrue(config.ENABLE_WINDOWS_SCR_SCREENSAVERS)
        self.assertEqual(float(config.CRT_SCREENSAVER_IDLE_SECONDS),240.0)
        self.assertTrue(config.ENABLE_CRT_STATIC_REFRESH)
        self.assertEqual(float(config.CRT_STATIC_REFRESH_INTERVAL_SECONDS),90.0)
        self.assertEqual(float(config.CRT_STATIC_REFRESH_DURATION_SECONDS),7.0)
    def test_speech_capture_is_less_eager_to_cut_off(self):
        self.assertGreaterEqual(float(config.PAUSE_THRESHOLD),1.0)
        self.assertGreaterEqual(float(config.PHRASE_TIME_LIMIT),30.0)
        self.assertGreaterEqual(float(config.NON_SPEAKING_DURATION),0.4)
    def test_observed_wake_aliases(self):
        gate=VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES)
        for phrase in ['Hey Bemo test','Hey BMO test','Hey Beemo test','Hey people test',"He'd be more test"]:
            self.assertEqual(gate.apply(phrase),'test',phrase)
    def test_stt_name_bias_is_wired_full_and_standby(self):
        self.assertIn('STT_INITIAL_PROMPT',chat_source)
        self.assertIn('initial_prompt=getattr(config, "STT_INITIAL_PROMPT", None)',chat_source)
        standby=(BASE/'standby_wake.py').read_text()
        self.assertIn('initial_prompt=getattr(config, "STT_INITIAL_PROMPT", None)',standby)
    def test_markdown_not_spoken_literally(self):
        out=prepare_spoken_text('**PETG** is good. 11 * 12 = 132. `code`')
        self.assertNotIn('*',out)
        self.assertNotIn('`',out)
        self.assertIn('11 times 12',out)
    def test_polite_switch_command_is_local_switch(self):
        c=Harness([])
        self.assertEqual(c._extract_switch_target('can you switch to the coding model? I need something coded.'),'coding')
        self.assertEqual(c._extract_switch_target('please switch to the engineering model'),'engineering')
        self.assertEqual(c._extract_switch_target('go back to the main brain'),'normal')
    def test_main_return_requires_explicit_approval(self):
        c=Harness(['Hey Bemo, no'],active='coding')
        self.assertFalse(c._confirm_specialist_switch('normal'))
        self.assertTrue(any('Main Brain' in x for x in c.spoken))
    def test_main_return_can_be_approved(self):
        c=Harness(['Hey BMO, yes please'],active='coding')
        self.assertTrue(c._confirm_specialist_switch('normal'))
    def test_refusal_never_loads_target(self):
        c=Harness(['Hey Bemo, no'])
        ok,msg=c._switch_profile('engineering')
        self.assertFalse(ok); self.assertEqual(msg,'USER_DECLINED_MODEL_SWITCH')
        self.assertEqual(c.switch_calls,[])
    def test_success_loads_then_announces_ready(self):
        c=Harness(['Hey Bemo, yes'])
        ok,msg=c._switch_profile('coding')
        self.assertTrue(ok); self.assertEqual(c.active_profile_name,'coding')
        self.assertEqual(len(c.switch_calls),1)
        self.assertTrue(any("Coding here. I'm loaded and ready." in x for x in c.spoken))
    def test_return_path_uses_same_switch_gate(self):
        self.assertIn('ok, reason = self._switch_profile(main, preload=True)',chat_source)
        # central switch method must not exempt Main from approval
        switch_block=chat_source[chat_source.index('    def _switch_profile'):chat_source.index('    def _return_to_main_after_specialist')]
        self.assertNotIn('profile_name != config.MAIN_MODEL_PROFILE and not self._confirm_specialist_switch',switch_block)
        self.assertIn('if not self._confirm_specialist_switch(profile_name):',switch_block)
    def test_return_to_main_denied_stays_on_specialist(self):
        c=Harness(['Hey Bemo, no'],active='coding')
        c._return_to_main_after_specialist('coding')
        self.assertEqual(c.active_profile_name,'coding')
        self.assertEqual(c.switch_calls,[])
    def test_return_to_main_approved_switches_and_announces(self):
        c=Harness(['Hey Bemo, yes'],active='coding')
        c._return_to_main_after_specialist('coding')
        self.assertEqual(c.active_profile_name,'normal')
        self.assertEqual(len(c.switch_calls),1)
        self.assertTrue(any("Main Brain here. I'm loaded and ready." in x for x in c.spoken))
    def test_static_scheduler_is_90_seconds_for_7_seconds(self):
        r=RenderMini(); r._crt_static_started=None; r._crt_static_last_ended=0.0
        self.assertFalse(r._crt_static_refresh_active(89.9,allowed=True))
        self.assertTrue(r._crt_static_refresh_active(90.0,allowed=True))
        self.assertTrue(r._crt_static_refresh_active(96.9,allowed=True))
        self.assertFalse(r._crt_static_refresh_active(97.0,allowed=True))
        self.assertEqual(r._crt_static_last_ended,97.0)
    def test_renderer_uses_config_color_and_periodic_static(self):
        render=(BASE/'systems/render.py').read_text()
        self.assertIn('FACE_BACKGROUND_COLOR',render)
        self.assertIn('_render_crt_static_refresh',render)
        self.assertIn('CRT_STATIC_REFRESH_INTERVAL_SECONDS',render)
        self.assertIn('CRT_STATIC_REFRESH_DURATION_SECONDS',render)
        self.assertIn('elif static_active:',render)

if __name__=='__main__': unittest.main(verbosity=2)
