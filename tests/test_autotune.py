"""The speed test scores the wait for the first audio, at BMO's real context, in random order with a warm repeat
(handoff 2026-10-31 #2; measured on the 5700U 2026-11-01)."""
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
import hardware
import hardware_autotune as at


class ChoiceTests(unittest.TestCase):
    def test_the_rows_that_chose_12_now_choose_8(self):
        # The 2026-10-27 run: 8 threads read and wrote faster; one sample of the voice while the brain read chose 12.
        rows = [{"threads": 8, "generation_tps": 13.45, "prompt_tps": 67.3, "tts_overlap_seconds": 0.336},
                {"threads": 12, "generation_tps": 12.64, "prompt_tps": 62.3, "tts_overlap_seconds": 0.231},
                {"threads": 16, "generation_tps": 10.46, "prompt_tps": 62.5, "tts_overlap_seconds": 0.2}]
        self.assertEqual(at.choose_efficient_result(rows)["threads"], 8)

    def test_a_near_tie_keeps_the_fewer_threads(self):
        rows = [{"threads": 6, "generation_tps": 15.5, "prompt_tps": 62.0},
                {"threads": 8, "generation_tps": 15.8, "prompt_tps": 63.0}]
        self.assertEqual(at.choose_efficient_result(rows)["threads"], 6)

    def test_a_failed_candidate_is_left_out(self):
        rows = [{"threads": 4, "generation_tps": 0.0, "prompt_tps": 0.0, "error": "x"},
                {"threads": 8, "generation_tps": 15.0, "prompt_tps": 60.0}]
        self.assertEqual(at.choose_efficient_result(rows)["threads"], 8)

    def test_the_voice_counts_in_the_wait(self):
        quiet = at.first_audio_seconds({"prompt_tps": 50.0, "generation_tps": 10.0})
        loud = at.first_audio_seconds({"prompt_tps": 50.0, "generation_tps": 10.0, "tts_overlap_seconds": 0.5})
        self.assertAlmostEqual(quiet, 12.0)
        self.assertAlmostEqual(loud, 12.5)


class OrderTests(unittest.TestCase):
    def test_candidates(self):
        self.assertEqual(at.candidate_thread_counts(8, 16), [4, 6, 8, 12])
        self.assertEqual(at.candidate_thread_counts(4, 8), [2, 3, 4, 6])
        self.assertEqual(at.candidate_thread_counts(8, 8), [4, 6, 8])
        self.assertEqual(at.candidate_thread_counts(1, 1), [1])

    def test_random_order_and_the_first_again_at_the_end(self):
        order = at.run_order([4, 6, 8, 12], shuffle=lambda items: items.reverse())
        self.assertEqual(order, [12, 8, 6, 4, 12])

    def test_the_slower_numbers_stand(self):
        kept = at.keep_slower({"threads": 8, "generation_tps": 16.0, "prompt_tps": 60.0},
                              {"threads": 8, "generation_tps": 15.0, "prompt_tps": 62.0})
        self.assertEqual((kept["generation_tps"], kept["prompt_tps"]), (15.0, 60.0))
        self.assertEqual(kept["warm_repeat"]["generation_tps"], 15.0)

    def test_a_failed_repeat_changes_nothing(self):
        first = {"threads": 8, "generation_tps": 16.0, "prompt_tps": 60.0}
        self.assertEqual(at.keep_slower(first, {"threads": 8, "generation_tps": 0.0, "error": "x"}), first)


class RealUseTests(unittest.TestCase):
    def test_the_old_choice_is_dropped_for_the_cores(self):
        record = {"algorithm_version": 3, "cpu_name": "X", "physical_cores": 8, "logical_cores": 16,
                  "chosen_ollama_threads": 12}
        self.assertFalse(hardware.autotune_record_matches(record, cpu_name="X", physical_cores=8, logical_cores=16))
        self.assertEqual(hardware.profile_from_counts(cpu_name="X", physical_cores=8, logical_cores=16,
                                                      ram_gb=28).ollama_threads, 8)

    def test_the_benchmark_loads_the_brain_as_bmo_does(self):
        seen = {}
        fake = types.SimpleNamespace(chat=lambda **kw: seen.update(kw) or {"message": {"content": "x"}})
        with mock.patch.dict(sys.modules, {"ollama": fake}):
            at._run_ollama_once("m", 8, "n")
        self.assertEqual(seen["options"]["num_ctx"], int(config.OLLAMA_NUM_CTX))
        self.assertEqual(seen["options"]["use_mmap"], bool(config.OLLAMA_USE_MMAP))


if __name__ == "__main__":
    unittest.main()
