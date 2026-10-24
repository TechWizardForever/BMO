import ast
import os
from pathlib import Path
import re
import sys
import types
import unittest

BASE = Path(os.environ.get('BEMO_TEST_CODE_ROOT','/mnt/data/bemo-v1285-build/testroot'))
sys.path.insert(0, str(BASE))

import config
from voice_commands import VoiceSessionGate
from systems.voice import prepare_spoken_text
from systems.stt_guard import is_runaway_repetition
from systems.context_policy import (
    tool_names_for_request, direct_specialist_route, authorize_route,
    requires_verified_computer_action,
)

CHAT = (BASE/'systems/chat.py').read_text()
STANDBY = (BASE/'standby_wake.py').read_text()
VOICE = (BASE/'systems/voice.py').read_text()

# Pull the production grounding helper into a tiny dependency-free harness.
tree = ast.parse(CHAT)
chat_cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Chat')
method = next(n for n in chat_cls.body if isinstance(n, ast.FunctionDef) and n.name == '_ground_execution_response')
mini = ast.ClassDef(name='GroundMini', bases=[], keywords=[], body=[method], decorator_list=[])
ns = {'requires_verified_computer_action': requires_verified_computer_action}
exec(compile(ast.fix_missing_locations(ast.Module(body=[mini], type_ignores=[])), 'ground-mini', 'exec'), ns)
GroundMini = ns['GroundMini']

class GroundHarness(GroundMini):
    pass


class V1285Tests(unittest.TestCase):
    def test_version_and_existing_crt_settings_preserved(self):
        self.assertRegex(config.APP_VERSION, r'v1\.(?:30\.\d+|29\.\d+|28\.(?:5|6|7|8|9|10|11|12|13|14|15|16|17|18|19|20|21|22|23|24|25|26|27|28|29|30|31|32|33|34|35|36|37|38|39|40|41|42|43|44|45|46|47|48|49|50|51|52|53|54|55|56|57|58|59|60|61|62|63|64|65|66|67|68|69|70|71|72|73|74|75|76|77|78|79|80|81|82|83|84|85|86|87|88|89|90|91|92|93|94|95|96|97|98|99|100|101|102|103|104|105|106|107|108|109|110|111|112|113|114|115|116|117|118|119|120|121|122|123|124))')
        self.assertEqual(config.FACE_BACKGROUND_COLOR_HEX.lower(), '#a3e6ca')
        self.assertEqual(float(config.CRT_SCREENSAVER_IDLE_SECONDS), 240.0)
        self.assertEqual(float(config.CRT_STATIC_REFRESH_INTERVAL_SECONDS), 90.0)
        self.assertEqual(float(config.CRT_STATIC_REFRESH_DURATION_SECONDS), 7.0)

    def test_spoken_text_strips_asterisks_and_hyphens(self):
        text = '**PETG** - water-resistant - good. 11 * 12. Range 5-10. Temp -8 C.'
        out = prepare_spoken_text(text)
        self.assertNotIn('*', out)
        self.assertNotIn('-', out)
        self.assertIn('water resistant', out)
        self.assertIn('11 times 12', out)
        self.assertIn('5 to 10', out)
        self.assertIn('minus 8', out)

    def test_direct_hey_bemo_command_still_works(self):
        gate = VoiceSessionGate(
            wake_aliases=config.VOICE_WAKE_ALIASES,
            timeout_seconds=config.VOICE_SESSION_TIMEOUT_SECONDS,
            followup_seconds=config.VOICE_WAKE_FOLLOWUP_SECONDS,
        )
        self.assertEqual(gate.apply('Hey Bemo, run the clock', now=1.0), 'run the clock')

    def test_bare_wake_arms_exactly_one_followup(self):
        gate = VoiceSessionGate(
            wake_aliases=config.VOICE_WAKE_ALIASES,
            timeout_seconds=90,
            followup_seconds=12,
        )
        self.assertEqual(gate.apply('Hey Bemo', now=1.0), '')
        self.assertEqual(gate.apply('run the clock', now=5.0), 'run the clock')
        self.assertIsNone(gate.apply('another command', now=6.0))

    def test_bare_wake_followup_expires(self):
        gate = VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES, followup_seconds=12)
        self.assertEqual(gate.apply('Hey Bemo', now=1.0), '')
        self.assertIsNone(gate.apply('run the clock', now=14.0))

    def test_wake_recognition_bias_is_stronger_but_still_local(self):
        self.assertGreaterEqual(int(config.STT_BEAM_SIZE), 2)
        # v1.28.10: one name only; a repeated-name hint primed repeated-name loops.
        self.assertIn('Bemo', config.STT_HOTWORDS)
        self.assertTrue((not config.STT_INITIAL_PROMPT) or ('Bemo' in config.STT_INITIAL_PROMPT))
        self.assertLessEqual(float(config.MIC_ENERGY_THRESHOLD_MAX), 450.0)
        self.assertIn('hotwords', CHAT)
        self.assertIn('MIC_ENERGY_THRESHOLD_MAX', CHAT)
        self.assertIn('hotwords', STANDBY)

    def test_short_okay_whisper_loop_is_rejected(self):
        self.assertTrue(is_runaway_repetition('Okay. Okay. Okay.'))
        self.assertTrue(is_runaway_repetition('Okay okay okay okay okay'))
        self.assertFalse(is_runaway_repetition('Okay, I understand the request.'))
        self.assertFalse(is_runaway_repetition('No no no, do not do that.'))

    def test_boot_and_run_requests_get_computer_control(self):
        for q in [
            'Can you boot that program now in its own window?',
            'Run /tmp/time_teller.py for me',
            'Launch the clock program',
        ]:
            names = tool_names_for_request(q, 'normal')
            self.assertIn('computer_control', names, q)
            self.assertTrue(requires_verified_computer_action(q), q)

    def test_capability_question_is_not_execution_request(self):
        self.assertFalse(requires_verified_computer_action('Can you run programs?'))

    def test_coding_followup_routes_back_to_coder(self):
        ctx = 'Coding wrote a Python program /tmp/time_teller.py to display the time.'
        q = 'Can you make it an animated clock please?'
        self.assertEqual(direct_specialist_route(q, 'normal', recent_context=ctx), 'HEAVY_CODING')
        self.assertTrue(authorize_route(q, 'HEAVY_CODING', recent_context=ctx)[0])

    def test_same_phrase_without_code_context_does_not_force_coder(self):
        self.assertIsNone(direct_specialist_route('Can you make it an animated clock please?', 'normal', recent_context='We were discussing wall clocks.'))

    def test_execution_claim_blocked_without_tool_evidence(self):
        h = GroundHarness()
        h._computer_execution_succeeded = False
        h._computer_action_pending = False
        h._computer_execution_attempted = False
        out = h._ground_execution_response('Boot that program now', "It's running right now!")
        self.assertIn('did not actually run', out)
        self.assertNotIn("It's running", out)

    def test_pending_execution_returns_confirmation_not_fake_success(self):
        h = GroundHarness()
        h._computer_execution_succeeded = False
        h._computer_action_pending = True
        h._computer_action_pending_summary = 'Run python3 /tmp/time_teller.py'
        h._computer_execution_attempted = True
        out = h._ground_execution_response('Run that program', 'It is running.')
        self.assertIn("haven't run it yet", out)
        self.assertIn('confirm computer action', out)

    def test_successful_execution_preserves_model_answer(self):
        h = GroundHarness()
        h._computer_execution_succeeded = True
        h._computer_action_pending = False
        h._computer_execution_attempted = True
        self.assertEqual(h._ground_execution_response('Run that program', 'It is running.'), 'It is running.')

    def test_action_requests_are_not_streamed_before_tool_result(self):
        self.assertIn('and not execution_required', CHAT)
        self.assertIn('You have not executed the requested program/app yet', CHAT)
        self.assertIn('EXECUTION_REQUEST_NUM_PREDICT', CHAT)
        self.assertLessEqual(int(config.EXECUTION_REQUEST_NUM_PREDICT), 256)

    def test_pending_action_keeps_specialist_loaded(self):
        self.assertIn('Computer action is waiting for explicit confirmation; keeping profile', CHAT)
        self.assertIn('if getattr(self, "_computer_action_pending", False):', CHAT)

    def test_confirmed_action_can_then_offer_return_to_main(self):
        block = CHAT[CHAT.index('if normalized in {"confirm computer action"'):CHAT.index('if normalized in {"cancel computer action"')]
        self.assertIn('_return_to_main_after_specialist', block)

    def test_history_budget_reduced_but_archive_retained(self):
        self.assertLessEqual(int(config.HISTORY_CONTEXT_CHAR_BUDGET), 5000)
        self.assertIn('archive preserved', CHAT)

    def test_tts_sanitization_occurs_at_final_tts_boundary(self):
        self.assertIn('spoken = prepare_spoken_text(str(item))', CHAT)
        self.assertIn('Markdown/emphasis/list markers should never be pronounced literally', VOICE)

if __name__ == '__main__':
    unittest.main(verbosity=2)
