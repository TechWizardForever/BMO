"""'Hey BMO, <command>' in one breath (live 2026-10-19: only a separate "Hey BMO" ever worked)."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from voice_commands import VoiceSessionGate


class WakeWithCommandTests(unittest.TestCase):
    def setUp(self):
        self.gate = VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES)

    def test_whisper_spellings_of_the_owners_hey_bmo(self):
        # From the owner's recorded "Hey BMO"s run into commands (bench 2026-10-19, 8 of 45 were lost).
        for heard, command in (("Hey, B-mount. Detail Scan.", "Detail Scan."), ("Hey, Bima! Yes!", "Yes!"),
                               ("Hey B-mount. Read the text on the screen.", "Read the text on the screen."),
                               ("Hey DMO, what time is it", "what time is it"),
                               ("Hey, Beemow! Detail scan!", "Detail scan!")):
            self.assertEqual(self.gate.parse(heard), (True, command), heard)

    def test_ordinary_speech_still_does_not_wake(self):
        for said in ("Hey demo time", "Hey Bob", "hey mount everest", "Bima", "I like the demo"):
            self.assertFalse(self.gate.parse(said)[0], said)

    def test_short_commands_get_the_head_rescue(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        body = src[src.index("    def _recover_dropped_wake"):src.index("    def _save_rejected_stt_audio")]
        self.assertIn("seconds = min(1.6, duration - 0.4)", body)

    def test_the_quick_scan_offer_takes_a_plain_answer(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        offer = src[src.index('offer = "If you want a closer look, say detailed scan."'):][:600]
        self.assertIn("self.voice_gate.followup_armed_until = time.monotonic() + 20.0", offer)


if __name__ == "__main__":
    unittest.main()
