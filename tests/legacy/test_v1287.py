"""v1.28.7 deterministic regression tests; do not require local Ollama/Piper hardware."""
import ast
import os
from pathlib import Path
import re
import sys
import threading
import time
import types
import unittest
from unittest.mock import Mock

ROOT = Path(os.environ.get('BEMO_TEST_CODE_ROOT', Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))
import config
from voice_commands import VoiceSessionGate
from systems.turn_speech import complete_sentences, ends_in_sentence, completed_prefix, speakable_units
from systems.grounding import HONEST_REPLIES, fabricated_live_claim
from systems.stt_guard import output_is_looping

SRC = (ROOT/'systems/chat.py').read_text()
tree = ast.parse(SRC)
chat = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Chat')
NAMES = {
    '_main_response_token_budget', '_microphone_stream',
    '_suspend_microphone_capture', '_resume_microphone_capture', 'listen',
    '_stream_chat_round', '_finish_main_sentence', '_chunk_content',
    '_chunk_thinking', '_tool_call_for_message', '_response_content',
    '_response_message', '_message_tool_calls', '_turn_cancelled',
    '_recover_dropped_wake', '_wakeword_rescue',  # v1.28.64
    '_movie_ducker',  # v1.28.92
}
mini = ast.ClassDef(name='MiniChat', bases=[], keywords=[], body=[
    n for n in chat.body if isinstance(n, ast.FunctionDef) and n.name in NAMES
], decorator_list=[])

class QuietLogger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None

class FakeSpeech:
    instances = []
    def __init__(self, chat):
        self.items = []
        self.cancelled = False
        self.finished = False
        self.chat = chat
        self.__class__.instances.append(self)
    @property
    def queued_any(self):
        return bool(self.items)
    def enqueue(self, text):
        self.items.append(text)
        return True
    def cancel_pending(self):
        self.cancelled = True
    def finish(self):
        self.finished = True

class FakeOllama:
    def __init__(self, words, *, continuation=''):
        self.words = words
        self.continuation = continuation
        self.chunks_consumed = 0
        self.requests = []
    def chat(self, **kwargs):
        self.requests.append(kwargs)
        if kwargs.get('stream'):
            def streaming():
                for word in self.words:
                    self.chunks_consumed += 1
                    yield {'message':{'content':word}}
                yield {'message':{'content':''}, 'done': True,
                       'done_reason':'stop','eval_count':20,'eval_duration':1000000000,
                       'prompt_eval_count':30,'prompt_eval_duration':1000000000}
            return streaming()
        return {'message':{'content':self.continuation}}

class MiniState:
    TALKING=5
    RESPONDING=4
    LISTENING=3

class FakeTimeout(Exception): pass

namespace = dict(
    config=config, re=re, time=time, json=__import__('json'),
    ollama=FakeOllama([]), logger=QuietLogger(),
    State=MiniState, _StreamingSpeechPipeline=FakeSpeech,
    complete_sentences=complete_sentences, ends_in_sentence=ends_in_sentence,
    completed_prefix=completed_prefix,
    sr=types.SimpleNamespace(WaitTimeoutError=FakeTimeout),
    # v1.28.8 stream dependencies
    fabricated_live_claim=fabricated_live_claim, HONEST_REPLIES=HONEST_REPLIES,
    output_is_looping=output_is_looping, speakable_units=speakable_units,
)
exec(compile(ast.fix_missing_locations(ast.Module(body=[mini],type_ignores=[])), '<chat-regression>', 'exec'), namespace)
MiniChat=namespace['MiniChat']

class TestTurns(unittest.TestCase):
    def setUp(self):
        FakeSpeech.instances.clear()
    def make_chat(self, model=None, mode='default', tts=True):
        obj=MiniChat()
        obj.active_profile_name='normal'
        obj.active_model='qwen3-main:30b'
        obj._runtime_response_class=mode
        obj.stop_event=threading.Event()
        obj.interface_settings=types.SimpleNamespace(tts_enabled=tts)
        obj.state=MiniState.RESPONDING
        obj.live_text=''
        obj._set_live_assistant_text=lambda text: setattr(obj,'live_text',text)
        obj._effective_context=lambda:8192
        obj._keep_alive=lambda:-1
        obj._ollama_options=lambda:{'num_predict':384}
        obj._mic_capture_suspended=True
        obj.cancel_turn_event=None  # v1.28.10
        obj.voice_gate=VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES)
        namespace['ollama']=model or FakeOllama([])
        return obj

    def test_version_and_main_prompt(self):
        self.assertRegex(config.APP_VERSION, r'v1\.(?:28|29|30)\.\d')
        prompt=config.MODEL_PROFILES['normal']['system_prompt']
        # v1.28.8 adds the shared grounding rule (no camera/GPS/weather/sensors).
        self.assertLess(len(prompt),2200)  # v1.28.42: + detected capability line (~200 chars)
        self.assertIn('1-2 sentences',prompt)
        self.assertIn('BMO from Adventure Time',prompt)
        self.assertNotIn('copilot',prompt.lower())
    def test_existing_specialist_limits(self):
        self.assertEqual(config.MODEL_PROFILES['coding']['num_predict'],4096)
        self.assertEqual(config.MODEL_PROFILES['engineering']['num_predict'],4096)
        self.assertEqual(config.MODEL_PROFILES['deep-vision']['num_predict'],4096)
        self.assertEqual(config.EXECUTION_REQUEST_NUM_PREDICT,192)
    def test_long_input_not_cut(self):
        self.assertGreaterEqual(config.PHRASE_TIME_LIMIT,30)
    def test_wake_name_variations_never_become_conversation(self):
        gate=VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES,followup_seconds=12)
        for phrase in (
            'Hey Bemo Hey Bemo Hey Beemo Hey BMO Bemo',
            'Hey Bemo Hey BMO Bemo Hey BMO',
            'BMO Bemo Beemo',
        ):
            self.assertEqual(gate.apply(phrase,now=4),'')
        self.assertEqual(gate.apply('BMO Bemo, how are you?',now=5),'how are you?')
    def test_name_only_unprefixed_followup_no_llm(self):
        gate=VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES,followup_seconds=12)
        self.assertEqual(gate.apply('Hey BMO',now=1),'')
        self.assertEqual(gate.apply('Hey Bemo BMO',now=2),'')
    def test_sentence_buffer_holds_while_streaming(self):
        self.assertEqual(complete_sentences('Hello there. This is'), (['Hello there.'],'This is'))
        self.assertEqual(complete_sentences('Hello there. This is a full sentence.'),
                         (['Hello there.','This is a full sentence.'],''))
    def test_no_arbitrary_word_splits(self):
        long=' '.join(['a']*58)+'.'
        self.assertEqual(complete_sentences(long),([long],''))
        self.assertEqual(complete_sentences(long[:-1]),([],long[:-1]))
    def test_questions_and_exclamations_are_sentences(self):
        self.assertEqual(complete_sentences('Yes! Really?'),(['Yes!','Really?'],''))
    def test_abbreviations_not_sentence_boundaries(self):
        self.assertEqual(complete_sentences('Dr. Smith is here. He lives in the U.S. now.'),
                         (['Dr. Smith is here.','He lives in the U.S. now.'],''))
    def test_decimal_not_sentence(self):
        self.assertEqual(complete_sentences('3.14 volts. Good.'),(['3.14 volts.','Good.'],''))
    def test_completed_prefix_drops_incomplete_tail(self):
        self.assertEqual(completed_prefix('Sentence one. Sentence two. If'), 'Sentence one. Sentence two.')
    def test_end_detector(self):
        self.assertTrue(ends_in_sentence('Yep!'))
        self.assertFalse(ends_in_sentence('Sentence begins If'))
    def test_short_main_ends_generation_after_two_sentences(self):
        model=FakeOllama(['Hello there. ','I can help you. ','Here is needless text.'])
        obj=self.make_chat(model)
        msg,text,tools=obj._stream_chat_round([{'role':'user','content':'How are you?'}],[])
        self.assertEqual(text,'Hello there. I can help you.')
        self.assertEqual(model.chunks_consumed,2)
        self.assertEqual(FakeSpeech.instances[-1].items,['Hello there.','I can help you.'])
        self.assertTrue(FakeSpeech.instances[-1].finished)
        self.assertEqual(obj.live_text,text)
        self.assertEqual(msg['content'],text)
    def test_brief_main_one_sentence(self):
        model=FakeOllama(['Yep! ','Anything else?'])
        obj=self.make_chat(model,'brief')
        _msg,text,_=obj._stream_chat_round([],[])
        self.assertEqual(text,'Yep!')
        self.assertEqual(model.chunks_consumed,1)
    def test_careful_main_no_two_sentence_cap(self):
        model=FakeOllama(['Check one. ','Check two. ','Check three.'])
        obj=self.make_chat(model,'careful')
        _msg,text,_=obj._stream_chat_round([],[])
        self.assertEqual(text,'Check one. Check two. Check three.')
        self.assertEqual(model.chunks_consumed,3)
    def test_stream_holds_incomplete_sentence(self):
        model=FakeOllama(['My second sentence',' is still unfinished',' but now it is done.'])
        obj=self.make_chat(model,'careful')
        _msg,text,_=obj._stream_chat_round([],[])
        self.assertEqual(FakeSpeech.instances[-1].items,['My second sentence is still unfinished but now it is done.'])
        self.assertEqual(text,'My second sentence is still unfinished but now it is done.')
    def test_suffix_generation_saves_truncated_answer(self):
        model=FakeOllama(['First sentence. ','A second sentence if'],continuation=' you need help.')
        obj=self.make_chat(model,'careful')
        _msg,text,_=obj._stream_chat_round([],[])
        self.assertEqual(text,'First sentence. A second sentence if you need help.')
        self.assertEqual(FakeSpeech.instances[-1].items,['First sentence.','A second sentence if you need help.'])
        self.assertEqual(len(model.requests),2)
    def test_bounded_suffix_failure_does_not_speak_fragment(self):
        model=FakeOllama(['This is the start. ','A dangling If'],continuation='')
        obj=self.make_chat(model,'careful')
        _msg,text,_=obj._stream_chat_round([],[])
        self.assertEqual(text,"This is the start. Sorry, I couldn't finish that last thought.")
        self.assertEqual(FakeSpeech.instances[-1].items,['This is the start.',"Sorry, I couldn't finish that last thought."])
    def test_specialist_budget_not_main_sentence_cap(self):
        model=FakeOllama(['One. ','Two. ','Three.'])
        obj=self.make_chat(model,'default')
        obj.active_profile_name='engineering'
        _msg,text,_=obj._stream_chat_round([],[])
        self.assertEqual(text,'One. Two. Three.')
    def test_real_portaudio_suspend_resume_drains_buffer(self):
        class Raw:
            def __init__(self): self.active=True; self.events=[]; self.available=128
            def stop_stream(self): self.active=False; self.events.append('stop')
            def start_stream(self): self.active=True; self.events.append('start')
            def get_read_available(self): return self.available
            def read(self,n,exception_on_overflow=False):
                self.available-=n;self.events.append(('drain',n));return b'\0'*n
        raw=Raw(); source=types.SimpleNamespace(stream=types.SimpleNamespace(pyaudio_stream=raw))
        obj=self.make_chat()
        obj._mic_capture_suspended=False
        obj._suspend_microphone_capture(source)
        self.assertFalse(raw.active)
        obj._resume_microphone_capture(source)
        self.assertTrue(raw.active)
        self.assertEqual(raw.events,['stop','start',('drain',128)])
    def test_capture_paused_before_whisper_and_after_timeout(self):
        class Raw:
            def __init__(self): self.active=False
            def stop_stream(self): self.active=False
            def start_stream(self): self.active=True
            def get_read_available(self): return 0
        raw=Raw(); source=types.SimpleNamespace(stream=types.SimpleNamespace(pyaudio_stream=raw))
        obj=self.make_chat()
        class Rec:
            def __init__(self): self.raise_timeout=False
            def listen(self,*args,**kwargs):
                self_seen=obj._mic_capture_suspended
                assert not self_seen
                if self.raise_timeout: raise FakeTimeout()
                return 'raw audio'
        obj.rec=Rec()
        obj._transcribe_local=lambda audio: 'Hey BMO' if not raw.active else 'WRONG: still recording'
        self.assertEqual(obj.listen(source),'Hey BMO')
        self.assertFalse(raw.active)
        obj.rec.raise_timeout=True
        self.assertIsNone(obj.listen(source))
        # v1.28.11: when nobody spoke, the stream stays open between 1 s checks
        # so no audio is lost; speak() pauses it before Bemo talks.
        self.assertTrue(raw.active)

if __name__=='__main__':unittest.main(verbosity=2)
