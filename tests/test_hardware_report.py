"""Hardware report (portability plan P3): GPUs, Ollama GPU use, disk, wording, and the prompt's capability line."""
import ast
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from systems import hardware_report as hr

GIB = 1024 ** 3


def fake_drm(cards):
    """sysfs like /sys/class/drm: cardN/device/{vendor, mem_info_vram_total} (device is a link to the PCI slot)."""
    root = Path(tempfile.mkdtemp())
    for i, (vendor, vram, slot) in enumerate(cards):
        pci = root / "pci" / slot
        pci.mkdir(parents=True)
        (pci / "vendor").write_text(vendor + "\n")
        if vram is not None:
            (pci / "mem_info_vram_total").write_text(str(vram))
        (root / f"card{i}").mkdir()
        (root / f"card{i}" / "device").symlink_to(pci)
    return root


def runner(outputs):
    return lambda argv, **kw: types.SimpleNamespace(stdout=outputs.get(argv[0], ""), returncode=0)


class GpuTests(unittest.TestCase):
    def test_this_pcs_integrated_amd(self):
        drm = fake_drm([("0x1002", 3 * GIB, "0000:05:00.0")])
        lspci = "05:00.0 VGA compatible controller: Advanced Micro Devices, Inc. [AMD/ATI] Lucienne (rev c1)\n"
        gpus = hr.detect_gpus(drm, runner({"lspci": lspci}), which=lambda t: t == "lspci")
        self.assertEqual([(g.vendor, g.model, g.memory_gib, g.integrated) for g in gpus], [("AMD", "Lucienne", 3.0, True)])

    def test_nvidia_card_uses_nvidia_smi(self):
        drm = fake_drm([("0x10de", None, "0000:01:00.0")])
        gpus = hr.detect_gpus(drm, runner({"nvidia-smi": "NVIDIA GeForce RTX 3060, 12288\n"}),
                              which=lambda t: t == "nvidia-smi")
        self.assertEqual((gpus[0].vendor, gpus[0].model, gpus[0].memory_gib, gpus[0].integrated),
                         ("NVIDIA", "NVIDIA GeForce RTX 3060", 12.0, False))

    def test_ollama_gpu_use(self):
        def opener(payload):
            return lambda url, timeout: io.BytesIO(json.dumps(payload).encode())
        self.assertIsNone(hr.models_use_gpu(opener=opener({"models": []})))
        self.assertFalse(hr.models_use_gpu(opener=opener({"models": [{"name": "qwen3-main:30b", "size_vram": 0}]})))
        self.assertTrue(hr.models_use_gpu(opener=opener({"models": [{"size_vram": 9 * GIB}]})))

        def broken(url, timeout):
            raise OSError("ollama is off")
        self.assertIsNone(hr.models_use_gpu(opener=broken))


class WordingTests(unittest.TestCase):
    def test_this_pc(self):
        r = hr.HardwareReport(cpu="AMD Ryzen 7 5700U with Radeon Graphics", threads=16, ram_gib=28.2, meets_minimum=True,
                              gpus=[hr.Gpu("AMD", "Lucienne", 3.0, True)], models_use_gpu=False,
                              disk_free_gib={"BMO's data": 751.6, "the models": 751.6}, sound_output="Scarlett Solo",
                              microphone="ATR4650-USB", screen="1920 by 1080 at 143.98 hertz")
        self.assertEqual(hr.spoken(r), "This computer has an AMD Ryzen 7 5700U with 16 threads and 28 gigabytes of memory, "
                         "enough for my models. It has built-in AMD graphics with 3 gigabytes, but my models run on "
                         "the CPU. There are 752 gigabytes free on the disk. Sound plays through Scarlett Solo and I "
                         "listen through ATR4650-USB. The screen is 1920 by 1080 at 144 hertz.")

    def test_small_pc_with_a_gpu(self):
        r = hr.HardwareReport(cpu="Intel(R) Core(TM) i5-8400 CPU @ 2.80GHz", threads=6, ram_gib=15.6,
                              gpus=[hr.Gpu("NVIDIA", "GeForce GTX 1060", 6.0, False)], models_use_gpu=True)
        said = hr.spoken(r)
        self.assertTrue(said.startswith("This computer has an Intel Core i5-8400 with 6 threads and 16 gigabytes of "
                                        "memory, but my models need 28."), said)
        self.assertIn("My models use its separate NVIDIA graphics with 6 gigabytes.", said)
        self.assertIn("BELOW BMO's 28 GB minimum", hr.printed(r)[1])

    def test_disk_free_walks_up_to_an_existing_folder(self):
        free = hr.disk_free({"BMO's data": Path(tempfile.mkdtemp()) / "not" / "made" / "yet"})
        self.assertGreater(free["BMO's data"], 0)


class PromptTests(unittest.TestCase):
    def test_every_model_gets_the_capability_line(self):
        self.assertTrue(config.CAPABILITY_FACTS.startswith("Real abilities here (mostly BMO's voice commands): chat"))
        self.assertTrue(config.CAPABILITY_FACTS.endswith("Claim no others."))
        self.assertLess(len(config.CAPABILITY_FACTS), 240)  # prompt budget (tests/legacy/test_orchestration.py)
        for name, profile in config.MODEL_PROFILES.items():
            self.assertIn(config.CAPABILITY_FACTS, profile["system_prompt"], name)

    def test_prompt_does_not_deny_enabled_weather(self):
        # Live 2026-10-19: Main said "I can't access live weather" while weather was on.
        if config.ENABLE_WEATHER:
            self.assertNotIn("GPS, weather", config.GROUNDING_RULE)
            self.assertIn("weather", config.CAPABILITY_FACTS)

    def test_hardware_questions(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        node = next(n for n in chat.body if isinstance(n, ast.Assign) and n.targets[0].id == "_HARDWARE_QUESTION")
        ns = {"re": re}
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[ast.Assign(targets=[ast.Name("q", ast.Store())], value=node.value)], type_ignores=[])), "q", "exec"), ns)
        for asked in ("what hardware am i on", "what's in this computer", "what are your specs", "hardware report",
                      "how much memory does this computer have"):
            self.assertTrue(ns["q"].search(asked), asked)
        for other in ("what hardware should i buy", "what can you do on this computer", "tell me about gpus"):
            self.assertFalse(ns["q"].search(other), other)


if __name__ == "__main__":
    unittest.main()
