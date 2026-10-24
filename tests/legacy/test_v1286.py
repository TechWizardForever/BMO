import ast
import os
from pathlib import Path
import sys
import unittest

BASE = Path(os.environ.get('BEMO_TEST_CODE_ROOT', '/mnt/data/bemo-v1286-full'))
sys.path.insert(0, str(BASE))

import config
from voice_commands import VoiceSessionGate
from systems.stt_guard import is_runaway_repetition

CHAT_TEXT = (BASE/'systems/chat.py').read_text(encoding='utf-8')

# Extract two production helpers into a tiny harness without constructing Chat.
tree = ast.parse(CHAT_TEXT)
chat_cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Chat')
method_names = {'_main_response_token_budget', '_pop_speech_chunks'}
methods = [n for n in chat_cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in method_names]
mini = ast.ClassDef(name='MiniChat', bases=[], keywords=[], body=methods, decorator_list=[])
ns = {'config': config, 're': __import__('re')}
exec(compile(ast.fix_missing_locations(ast.Module(body=[mini], type_ignores=[])), 'mini-chat', 'exec'), ns)
MiniChat = ns['MiniChat']


class V1286Tests(unittest.TestCase):
    def test_version(self):
        self.assertRegex(config.APP_VERSION, r'v1\.(?:28|29|30)\.\d')

    def test_main_identity_and_model_awareness_are_compact(self):
        prompt = config.MODEL_PROFILES['normal']['system_prompt']
        # v1.28.8: "real-world BMO" invited fictional senses; identity kept.
        self.assertIn("BMO from Adventure Time", prompt)
        self.assertIn('Engineering', prompt)
        self.assertIn('Coding', prompt)
        self.assertIn('Vision', prompt)  # one vision brain since 2026-10-24
        self.assertIn('user approval', prompt)
        self.assertIn('generic AI', prompt)
        # Prevent a repeat of the old 8.9k static-prompt regression.
        self.assertLess(len(prompt), 2200)  # v1.28.8 grounding rule + host facts; v1.28.42 capability line
        self.assertNotIn('BEMO VOICE COMMAND CATALOG', prompt)

    def test_main_short_answer_policy_present(self):
        prompt = config.MODEL_PROFILES['normal']['system_prompt']
        self.assertIn('2-4 short sentences', prompt)
        self.assertIn('expand only when needed or asked', prompt)

    def test_main_runtime_budgets_are_small_by_default_but_expandable(self):
        budget = MiniChat._main_response_token_budget
        self.assertEqual(budget('What is your favorite color?')[0], int(config.MAIN_RESPONSE_DEFAULT_NUM_PREDICT))
        self.assertEqual(budget('Give me a quick answer: what is PETG?')[0], int(config.MAIN_RESPONSE_BRIEF_NUM_PREDICT))
        self.assertEqual(budget('Is staring at a laser safe for my eyes?')[0], int(config.MAIN_RESPONSE_CAREFUL_NUM_PREDICT))
        self.assertEqual(budget('Walk me through this in detail step by step.')[0], int(config.MAIN_RESPONSE_DETAILED_NUM_PREDICT))
        self.assertLess(int(config.MAIN_RESPONSE_DEFAULT_NUM_PREDICT), int(config.MAIN_RESPONSE_DETAILED_NUM_PREDICT))
        self.assertGreaterEqual(int(config.MODEL_PROFILES['normal']['num_predict']), int(config.MAIN_RESPONSE_DETAILED_NUM_PREDICT))

    def test_specialist_profiles_keep_their_existing_large_budgets(self):
        self.assertEqual(config.MODEL_PROFILES['coding']['num_predict'], 4096)
        self.assertEqual(config.MODEL_PROFILES['engineering']['num_predict'], 4096)
        self.assertEqual(config.MODEL_PROFILES['deep-vision']['num_predict'], 4096)
        self.assertNotIn('fast-vision', config.MODEL_PROFILES)
        # Runtime cap application is explicitly restricted to Main.
        self.assertIn('if not summary and self.active_profile_name == "normal":', CHAT_TEXT)

    def test_bare_name_wakes_and_can_prefix_a_command(self):
        gate = VoiceSessionGate(
            wake_aliases=config.VOICE_WAKE_ALIASES,
            timeout_seconds=90,
            followup_seconds=12,
        )
        self.assertEqual(gate.apply('Bemo, what time is it?', now=1.0), 'what time is it?')
        self.assertEqual(gate.apply('BMO, current model', now=2.0), 'current model')
        self.assertEqual(gate.apply('Beemo', now=3.0), '')
        self.assertEqual(gate.apply('what model are you?', now=4.0), 'what model are you?')

    def test_bare_common_phrase_be_more_is_not_a_wake_alias(self):
        gate = VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES, followup_seconds=12)
        self.assertIsNone(gate.apply('be more careful with that', now=1.0))
        self.assertNotIn('be more', config.VOICE_WAKE_ALIASES)


    def test_whisper_bias_does_not_seed_hallucinated_sentence(self):
        self.assertEqual(config.STT_INITIAL_PROMPT, '')
        self.assertIn('Bemo', config.STT_HOTWORDS)
        words = config.STT_HOTWORDS.lower().split()
        self.assertEqual(len(words), len(set(words)))  # v1.28.10: no repeated names
        loop = ('The name Bemo is pronounced Bee-mo. ' * 4).strip()
        self.assertTrue(is_runaway_repetition(loop))

    def test_piper_stream_chunks_are_small(self):
        self.assertLessEqual(int(config.PIPER_FIRST_CHUNK_MAX_WORDS), 10)
        self.assertLessEqual(int(config.PIPER_CHUNK_MAX_WORDS), 14)
        self.assertLess(int(config.PIPER_CHUNK_MAX_WORDS), 20)

    def test_chunker_releases_short_sentence_units(self):
        obj = MiniChat()
        text = 'This is a short sentence. Here is another useful sentence. And this one keeps going with several more words for testing.'
        ready, tail = obj._pop_speech_chunks(text, min_words=4, max_words=14)
        self.assertGreaterEqual(len(ready), 2)
        for chunk in ready:
            self.assertLessEqual(len(chunk.split()), 14)
        self.assertTrue(isinstance(tail, str))

    def test_long_sentence_is_split_before_old_45_word_size(self):
        obj = MiniChat()
        text = ' '.join(['word'] * 40) + '.'
        ready, tail = obj._pop_speech_chunks(text, min_words=6, max_words=14, max_chunks=1)
        self.assertEqual(len(ready), 1)
        self.assertLessEqual(len(ready[0].split()), 14)
        self.assertGreater(len(tail.split()), 0)

    def test_existing_grounding_and_crt_settings_preserved(self):
        self.assertEqual(config.FACE_BACKGROUND_COLOR_HEX.lower(), '#a3e6ca')
        self.assertEqual(float(config.CRT_SCREENSAVER_IDLE_SECONDS), 240.0)
        self.assertEqual(float(config.CRT_STATIC_REFRESH_INTERVAL_SECONDS), 90.0)
        self.assertIn('requires_verified_computer_action', CHAT_TEXT)


if __name__ == '__main__':
    unittest.main(verbosity=2)
