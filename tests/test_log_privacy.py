"""BMO's replies stay out of the logs (2026-10-20): print(full_response) put every spoken reply in the
systemd journal, e.g. "Screen's dirty? Grab a microfiber cloth — I'll pause the game while you clean it."
Logs hold commands BMO understood, timings and numbers."""
import os
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))


class ReplyLoggingTests(unittest.TestCase):
    def test_the_chat_loop_prints_nothing(self):
        # Under the service, stdout is the journal: any print() in the turn loop lands there.
        source = (ROOT / "systems" / "chat.py").read_text(encoding="utf-8")
        printed = [line.strip() for line in source.splitlines() if re.match(r"\s*print\(", line)]
        self.assertEqual(printed, [])


class HeardSpeechTests(unittest.TestCase):
    def test_room_speech_is_logged_by_size_only(self):
        # Audit 2026-10-24: awake BMO wrote every capture's words into the journal ("Speech timing ...
        # candidate=..."), room conversation included: 616 lines in a week. What is said TO him ("Heard accepted
        # speech") is kept; anything else only as a word count, like standby.
        source = (ROOT / "systems" / "chat.py").read_text(encoding="utf-8")
        for leak in ("candidate={!r}", "compression={:.2f} text={!r}", "Whisper had {!r}",
                     "ignored unrelated speech: {!r}"):
            self.assertNotIn(leak, source)
        self.assertIn('"Speech timing: capture={:.2f}s stt={:.2f}s total={:.2f}s words={} wake={}"', source)

    def test_rejected_captures_are_not_kept_as_audio(self):
        sys.path.insert(0, str(ROOT))
        import config
        self.assertEqual(config.STT_SAVE_REJECTED_AUDIO, 0)


class SupervisorLogTests(unittest.TestCase):
    def test_the_log_is_capped_and_without_filler(self):
        # Audit 2026-10-24: supervisor.log grew without limit; 87% was "faster_whisper: Processing audio".
        import logging
        import tempfile
        from logging.handlers import RotatingFileHandler
        from unittest import mock
        sys.path.insert(0, str(ROOT))
        import supervisor
        saved = logging.root.handlers[:]
        logging.root.handlers.clear()
        try:
            with mock.patch.object(supervisor.config, "SUPERVISOR_LOG_DIR", tempfile.mkdtemp()):
                supervisor._configure_logging()
            files = [h for h in logging.root.handlers if isinstance(h, logging.FileHandler)]
            self.assertTrue(files and all(isinstance(h, RotatingFileHandler) and h.maxBytes == 5_000_000
                                          for h in files))
            self.assertEqual(logging.getLogger("faster_whisper").level, logging.WARNING)
        finally:
            for handler in logging.root.handlers:
                handler.close()
            logging.root.handlers[:] = saved


if __name__ == "__main__":
    unittest.main()
