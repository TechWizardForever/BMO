"""BMO's first boot, the install screen (rebuilt 2026-10-25): the order, going back to what failed, skipping,
carrying on after a restart, Linux updates, and the microphone and screen steps, with fake audio and screens."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest

import numpy as np

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems import downloads as dl
from systems import first_boot as fb
from systems.first_boot import DONE, FAILED, PENDING, SKIPPED


def audio(voice=0.0, noise=200.0, seconds=5.0, seed=1):
    """Room noise, with a 1.5 s 'voice' in the middle when voice > 0 (16 kHz mono int16)."""
    rng = np.random.default_rng(seed)
    n = int(fb.RATE * seconds)
    signal = rng.normal(0, noise, n)
    if voice:
        start, length = int(fb.RATE * 1.5), int(fb.RATE * 1.5)
        t = np.arange(length) / fb.RATE
        signal[start:start + length] += voice * np.sin(2 * np.pi * 220 * t)
    return np.clip(signal, -32768, 32767).astype("<i2").tobytes()


class FakeIO:
    def __init__(self, inputs=(), recordings=(), heard=None, answers=(), keys=()):
        self.inputs = list(inputs)
        self.recordings = list(recordings)   # one {name: pcm} per prompt
        self.heard = heard or {}             # pcm -> transcript
        self.answers = list(answers)
        self.said, self.default_set, self.calibrated, self.restarts = [], [], 0, 0
        self.view = fb.SetupView(blocking=False)
        self.view.active = True
        for key in keys:
            self.view.press(key)
        self.downloads = dl.DownloadState(Path(tempfile.mkdtemp()) / "downloads.json")

    def say(self, text):
        self.said.append(text)

    def list_inputs(self):
        return self.inputs

    def record_inputs(self, names, seconds):
        return self.recordings.pop(0) if self.recordings else {n: audio() for n in names}

    def transcribe(self, pcm):
        return self.heard.get(pcm, "")

    def heard_wake(self, pcm):
        return False

    def set_default_input(self, name):
        self.default_set.append(name)

    def ask(self, question):
        self.said.append(question)
        return self.answers.pop(0) if self.answers else None

    def calibrate_screen(self):
        self.calibrated += 1

    def online(self):
        return True

    def downloads_state(self):
        return self.downloads

    def restart_computer(self):
        self.restarts += 1
        return False

    def pause(self, seconds):
        pass


def mic(name, desc, default=False):
    return types.SimpleNamespace(name=name, description=desc, default=default)


SOLO = mic("alsa_input.usb-Focusrite_Scarlett_Solo", "Scarlett Solo (3rd Gen.) Analog Stereo")
CLIP = mic("alsa_input.usb-Clip-on_ATR4650", "ATR4650-USB Mono", default=True)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "install" / "first-boot.json"

    def test_saved_after_each_step_and_resumed(self):
        state = fb.FirstBootState(self.path)
        self.assertFalse(state.active())  # no file: an install from before first boot existed
        state.begin(("microphone", "screen"))
        state.mark("microphone", DONE, {"input": "x"})
        again = fb.FirstBootState(self.path)  # e.g. after a power cut
        self.assertTrue(again.active())
        self.assertEqual([s["status"] for s in again.steps()], [DONE, PENDING])
        again.mark("screen", DONE)
        self.assertFalse(fb.FirstBootState(self.path).active())
        self.assertTrue(json.loads(self.path.read_text())["completed_at"])

    def test_a_broken_file_is_no_first_boot(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json")
        self.assertFalse(fb.FirstBootState(self.path).active())


class Step:
    """A stand-in step: its outcomes in turn (the last one repeats), and the order the steps ran in."""
    def __init__(self, name, *outcomes, ran=None):
        self.name, self.ran = name, ran if ran is not None else []
        self.phase = "listening" if name == "screen" else "before_listening"
        self.outcomes = list(outcomes) or [fb.StepResult(DONE, {})]
        self.options = {}

    def run(self, io):
        self.ran.append(self.name)
        return self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]


def ok(**result):
    return fb.StepResult(DONE, result)


def bad(**result):
    return fb.StepResult(FAILED, result)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.state = fb.FirstBootState(Path(tempfile.mkdtemp()) / "first-boot.json")
        self.state.begin()
        self.ran = []

    def steps(self, **outcomes):
        return {name: Step(name, *outcomes.get(name, ()), ran=self.ran) for name in fb.STEP_ORDER}

    def status(self):
        return {s["name"]: s["status"] for s in self.state.steps()}

    def test_internet_first_then_updates_and_downloads_then_the_person_steps(self):
        io = FakeIO(keys=["enter"])
        setup = fb.Setup(self.state, io, steps=self.steps())
        self.assertTrue(setup.run("before_listening"))
        self.assertEqual(self.ran, ["internet", "updates", "downloads", "microphone", "voice"])
        self.assertEqual(io.said[0], fb.GREETING)
        self.assertTrue(any("Press Enter when you're ready" in s for s in io.said))
        self.assertEqual(io.view.status("brain"), fb.RUNNING)  # BMO loads his brain next
        self.assertTrue(io.view.active)
        setup.run("listening")
        self.assertEqual(self.ran[-1], "screen")
        self.assertEqual(io.said[-1], fb.FINISHED)
        self.assertFalse(self.state.active())
        self.assertFalse(io.view.active)  # the face takes over

    def test_a_failure_is_left_while_the_rest_go_on_then_tried_again_at_the_end(self):
        io = FakeIO(keys=["enter"])
        steps = self.steps(microphone=[bad(reason="not heard"), ok(description="ATR4650-USB Mono")],
                           downloads=[bad(reason="download", failed={"qwen3-coder:30b": "the internet connection"}),
                                      ok()])
        fb.Setup(self.state, io, steps=steps).run("before_listening")
        # The downloads failing didn't hold up the microphone; voice waited for the microphone; then back to both.
        self.assertEqual(self.ran, ["internet", "updates", "downloads", "microphone", "downloads", "microphone",
                                    "voice"])
        self.assertTrue(any("Some of my downloads need another go" in s for s in io.said))
        self.assertTrue(any(s.startswith("Let me go back to my downloads, the microphone and learning your voice")
                            for s in io.said))
        self.assertEqual(self.status()["voice"], DONE)
        self.assertEqual(io.view.snapshot()["problems"], [])

    def test_what_still_fails_waits_for_the_person_enter_tries_again(self):
        io = FakeIO(keys=["enter", "enter"])
        steps = self.steps(microphone=[bad(reason="not heard"), bad(reason="not heard"), ok()])
        setup = fb.Setup(self.state, io, steps=steps)
        shown = []
        original = io.view.show_problems
        io.view.show_problems = lambda lines: (shown.append(list(lines)), original(lines))
        setup.run("before_listening")
        self.assertEqual(self.ran.count("microphone"), 3)
        self.assertIn("Microphone: I couldn't hear you. Check it's plugged in, switched on and turned up.", shown[0])
        self.assertIn("Learn your voice: it needs the microphone first.", shown[0])
        self.assertTrue(any("Press Enter to try again, or S to skip it for now" in s for s in io.said))
        self.assertEqual(self.status()["microphone"], DONE)
        self.assertEqual(self.status()["voice"], DONE)

    def test_s_skips_it_for_now_and_the_setup_finishes(self):
        io = FakeIO(keys=["enter", "skip"])
        steps = self.steps(microphone=[bad(reason="no microphone")])
        setup = fb.Setup(self.state, io, steps=steps)
        setup.run("before_listening")
        self.assertEqual(self.status()["microphone"], SKIPPED)
        self.assertEqual(self.status()["voice"], SKIPPED)
        saved = next(s for s in self.state.steps() if s["name"] == "microphone")["result"]
        self.assertEqual(saved, {"reason": "no microphone", "skipped": "for now"})  # remembered, with why
        self.assertIn("Okay, I'll skip that for now.", io.said)
        setup.run("listening")
        self.assertFalse(self.state.active())

    def test_a_restart_carries_on_where_it_was(self):
        first = FakeIO(keys=["enter"])
        steps = self.steps(microphone=[bad(reason="not heard")])
        self.state.mark("internet", DONE, {"how": "already"})
        self.state.mark("updates", DONE, {})
        self.state.mark("downloads", DONE, {})
        later = fb.Setup(fb.FirstBootState(self.state.path), first, steps=steps)
        later.show()
        rows = {row["key"]: row for row in first.view.snapshot()["rows"]}
        self.assertEqual((rows["internet"]["status"], rows["updates"]["status"]), (DONE, DONE))  # ticked at once
        first.view.press("skip")
        later.run("before_listening")
        self.assertNotIn("internet", self.ran)
        self.assertNotIn("downloads", self.ran)
        self.assertNotIn(fb.GREETING, first.said)  # greeted the first time already

    def test_a_new_kernel_restarts_the_computer_once_the_step_is_saved(self):
        io = FakeIO(keys=["enter"])
        saved_first = []
        io.restart_computer = lambda: (saved_first.append(dict((s["name"], s["status"]) for s in
                                                               fb.FirstBootState(self.state.path).steps())), True)[1]
        fb.Setup(self.state, io, steps=self.steps(updates=[ok(reboot=True)])).run("before_listening")
        self.assertEqual(saved_first[0]["updates"], DONE)
        self.assertEqual(saved_first[0]["downloads"], PENDING)  # the downloads come after the restart
        self.assertTrue(any("restarting the computer" in s for s in io.said))

    def test_an_older_first_boot_file_still_works(self):
        self.state.begin(("microphone", "voice", "screen", "internet", "downloads", "first_start"))
        io = FakeIO(keys=["enter"])
        fb.Setup(self.state, io, steps=self.steps()).run("before_listening")
        self.assertEqual(self.ran, ["internet", "downloads", "microphone", "voice"])
        self.assertEqual(self.status()["first_start"], SKIPPED)

    def test_it_names_only_the_steps_left_for_the_person(self):
        self.state.begin(("internet", "downloads", "screen"))
        io = FakeIO(keys=["enter"])
        fb.Setup(self.state, io, steps=self.steps()).run("before_listening")
        self.assertIn("My brains are all here. The last part needs you for a couple of minutes: the screen. "
                      "Press Enter when you're ready.", io.said)

    def test_nothing_waits_for_the_person_when_their_steps_are_done(self):
        self.state.begin(("internet", "downloads"))
        io = FakeIO()  # no keys: it must not ask for Enter
        fb.Setup(self.state, io, steps=self.steps()).run("before_listening")
        self.assertFalse(any("Press Enter" in s for s in io.said))
        self.assertFalse(self.state.active())


class ViewTests(unittest.TestCase):
    def test_every_row_in_order_and_the_bar_follows_real_progress(self):
        view = fb.SetupView(blocking=False)
        view.open()
        keys = [row["key"] for row in view.snapshot()["rows"]]
        self.assertEqual(keys[:3], ["internet", "updates", "qwen3-main:30b"])
        self.assertEqual(keys[3:5], ["game-player", "screensavers"])
        self.assertEqual(keys[-5:], ["benchmark", "microphone", "voice", "brain", "screen"])
        self.assertEqual(view.overall(), 0.0)
        view.row("qwen3-main:30b", fb.RUNNING, "50%", 0.5)
        half_main = view.overall()
        view.row("internet", DONE)
        self.assertGreater(half_main, view.overall() - half_main)  # 9.5 GB of brain counts more than the internet
        for key in keys:
            view.row(key, DONE)
        self.assertEqual(view.overall(), 1.0)

    def test_downloads_shown_from_the_saved_progress(self):
        view = fb.SetupView(blocking=False)
        view.open()
        state = dl.DownloadState(Path(tempfile.mkdtemp()) / "d.json")
        state.set("qwen3-main:30b", status=dl.DONE, already=True)
        state.set("game-player", status=dl.DOWNLOADING, total=1000, completed=250, rate=5)
        state.set("screensavers", status=dl.FAILED, error="Temporary failure in name resolution")
        state.data["benchmark"] = {"running": True, "step": 2, "steps": 5, "label": "testing 4 threads"}
        fb.show_downloads(view, state)
        rows = {row["key"]: row for row in view.snapshot()["rows"]}
        self.assertEqual((rows["qwen3-main:30b"]["status"], rows["qwen3-main:30b"]["detail"]), (DONE, "already here"))
        self.assertEqual((rows["game-player"]["progress"], rows["game-player"]["detail"]), (0.25, "25%  2 min left"))
        self.assertEqual((rows["screensavers"]["status"], rows["screensavers"]["detail"]),
                         (FAILED, "the internet connection"))
        self.assertEqual(rows["qwen3-coder:30b"]["detail"], "19 GB")
        self.assertEqual((rows["benchmark"]["progress"], rows["benchmark"]["detail"]), (0.4, "testing 4 threads"))

    def test_keys_only_while_the_screen_is_up(self):
        view = fb.SetupView(blocking=False)
        view.press("enter")
        self.assertIsNone(view.key())
        view.active = True
        view.press("enter")
        self.assertEqual(view.key(), "enter")


class UpdateIO:
    """The update service, faked: progress, then done (or failed)."""
    def __init__(self, statuses, *, installed=True, starts=(True, "")):
        self.statuses, self.installed, self.start = list(statuses), installed, starts
        self.view = fb.SetupView(blocking=False)
        self.view.open()
        self.said, self.clock, self.started = [], 0.0, 0

    def update_service_installed(self): return self.installed
    def start_system_update(self): self.started += 1; return self.start
    def system_update_status(self): return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
    def system_update_running(self): return len(self.statuses) > 1
    def say(self, text): self.said.append(text)
    def now(self): return self.clock
    def pause(self, seconds): self.clock += seconds


class UpdatesStepTests(unittest.TestCase):
    def test_follows_the_service_to_the_end(self):
        io = UpdateIO([{"run": 3, "state": "done"},  # read before starting: an earlier run
                       {"run": 3, "state": "done"},
                       {"run": 4, "state": "running", "phase": "downloading", "percent": 30.0},
                       {"run": 4, "state": "done", "percent": 100.0, "reboot_needed": True}])
        outcome = fb.UpdatesStep().run(io)
        self.assertEqual((outcome.status, outcome.result), (DONE, {"reboot": True}))
        self.assertEqual(io.started, 1)
        self.assertIn("Linux's own updates", io.said[0])

    def test_without_the_service_it_is_the_owners_job(self):
        outcome = fb.UpdatesStep().run(UpdateIO([{}], installed=False))
        self.assertEqual(outcome.status, SKIPPED)
        self.assertEqual(fb.describe("updates", outcome), "you do these yourself")

    def test_not_allowed_or_failed_is_a_problem(self):
        refused = fb.UpdatesStep().run(UpdateIO([{}], starts=(False, "Interactive authentication required.")))
        self.assertEqual((refused.status, refused.result["reason"]), (FAILED, "not started"))
        failed = fb.UpdatesStep().run(UpdateIO([{}, {"run": 1, "state": "failed", "error": "E: Could not get lock"}]))
        self.assertEqual((failed.status, failed.result["error"]), (FAILED, "E: Could not get lock"))
        self.assertEqual(fb.problem_lines("updates", failed.result), ["Linux updates: E: Could not get lock"])

    def test_a_service_that_never_runs_is_not_waited_for_forever(self):
        outcome = fb.UpdatesStep().run(UpdateIO([{"run": 2}]))
        self.assertEqual((outcome.status, outcome.result["reason"]), (FAILED, "not started"))


class MicrophoneTests(unittest.TestCase):
    def test_level_tells_voice_from_room(self):
        self.assertGreater(fb.level(audio(voice=6000)).snr_db, 20)
        self.assertLess(fb.level(audio()).snr_db, 3)

    def test_a_burst_of_noise_as_an_input_opens_is_not_a_voice(self):
        # Live 2026-10-19: the motherboard input gives ~0.26 s of full-scale noise, then zeros.
        burst = np.zeros(fb.RATE * 5, dtype="<i2")
        burst[1:int(fb.RATE * 0.45):2] = 32767
        self.assertEqual(fb.level(burst.tobytes()).snr_db, 0.0)
        late = burst.copy()
        late[fb.RATE * 2:fb.RATE * 2 + 800] = 20000  # digital silence with a click: still no microphone
        self.assertEqual(fb.level(late.tobytes()).snr_db, 0.0)
        clipped = np.full(fb.RATE * 5, 32767, dtype="<i2")
        clipped[::2] = -32768
        self.assertEqual(fb.level(clipped.tobytes()).snr_db, 0.0)

    def test_picks_the_microphone_that_heard_the_phrase_best_and_makes_it_the_default(self):
        near, far = audio(voice=8000, seed=2), audio(voice=1500, seed=3)
        io = FakeIO([CLIP, SOLO], [{CLIP.name: far, SOLO.name: near}], {near: "Hey BMO, hello.", far: "hello"})
        outcome = fb.MicrophoneStep().run(io)
        self.assertEqual(outcome.status, DONE)
        self.assertEqual(outcome.result["input"], SOLO.name)
        self.assertEqual(io.default_set, [SOLO.name])
        self.assertIn("I can hear you on the Scarlett Solo.", io.said[-1])
        self.assertNotIn("noisy", io.said[-1])

    def test_room_noise_and_other_words_are_not_the_phrase(self):
        talk = audio(voice=6000, seed=4)
        io = FakeIO([CLIP], [{CLIP.name: talk}, {CLIP.name: audio()}], {talk: "what's on TV tonight"})
        outcome = fb.MicrophoneStep().run(io)
        self.assertEqual(outcome.status, FAILED)
        self.assertEqual([s for s in io.said if "Hey BMO, hello" in s], [fb.MicrophoneStep.FIRST, fb.MicrophoneStep.AGAIN])
        self.assertIn("plugged in", io.said[-1])
        self.assertNotIn("what's on TV", json.dumps(outcome.result))  # nothing heard is kept

    def test_a_quiet_voice_gets_a_tip_and_the_default_stays(self):
        quiet = audio(voice=900, noise=200)
        io = FakeIO([CLIP], [{CLIP.name: quiet}], {quiet: "hello"})
        outcome = fb.MicrophoneStep().run(io)
        self.assertEqual(outcome.status, DONE)
        self.assertEqual(io.default_set, [])
        self.assertIn("move it closer", io.said[-1])

    def test_phrase_and_names(self):
        for text in ("Hey BMO, hello.", "Hello.", "Hey Bemo.", "hi there"):
            self.assertTrue(fb.heard_phrase(text), text)
        for text in ("", "What's the weather?", "Bemo"):
            self.assertFalse(fb.heard_phrase(text), text)
        self.assertEqual(fb.spoken_device("Family 17h/19h HD Audio Controller Analog Stereo"),
                         "computer's own microphone socket")
        self.assertEqual(fb.spoken_device("ATR4650-USB Mono"), "ATR4650 USB")

    def test_recording_every_input_at_once(self):
        calls = []

        class FakeProc:
            def __init__(self, argv, **kwargs):
                calls.append(argv)
                self.stdout = io.BytesIO(b"\x01\x00" * (fb.RATE * 2))

            def terminate(self):
                pass

            def wait(self, timeout=None):
                return 0

        got = fb.record_sources(["a", "b"], 1.0, popen=FakeProc)
        self.assertEqual(sorted(got), ["a", "b"])
        self.assertEqual(len(got["a"]), fb.RATE * 2)
        self.assertIn("--device=a", calls[0] + calls[1])
        self.assertIn("--rate=16000", calls[0])


class ScreenTests(unittest.TestCase):
    def test_yes_or_no(self):
        for text in ("Yes.", "Yeah, it fits.", "I can see all of it", "Looks good", "Hey BMO, yes"):
            self.assertIs(fb.yes_or_no(text), True, text)
        for text in ("No.", "Um, no.", "The left side is cut off", "It doesn't fit", "nope"):
            self.assertIs(fb.yes_or_no(text), False, text)
        for text in ("", None, "banana bread"):
            self.assertIsNone(fb.yes_or_no(text), text)

    def test_yes_keeps_the_screen_and_no_starts_calibration(self):
        io = FakeIO(answers=["yes"])
        self.assertEqual(fb.ScreenStep().run(io).result, {"fits": True})
        self.assertEqual(io.calibrated, 0)
        io = FakeIO(answers=["no, the top is cut off"])
        self.assertEqual(fb.ScreenStep().run(io).result, {"fits": False, "calibration_offered": True})
        self.assertEqual(io.calibrated, 1)

    def test_no_answer_leaves_the_screen_and_says_how_to_fix_it_later(self):
        io = FakeIO(answers=[None, "banana"])
        outcome = fb.ScreenStep().run(io)
        self.assertEqual((outcome.status, outcome.result), (DONE, {"fits": None}))
        self.assertIn("calibrate the screen, any time", io.said[-1])


class WiringTests(unittest.TestCase):
    def test_bmo_runs_the_phases_where_they_belong(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        self.assertLess(src.index('self._first_boot("before_listening")'),
                        src.index("if config.SUPERVISOR_PRELOAD_ACTIVE_MODEL_ON_START:"))
        self.assertLess(src.index("self._calibrate_microphone(source)\n"), src.index('self._first_boot("listening")'))
        self.assertLess(src.index('self._first_boot("listening")'), src.index("config.VOICE_STARTUP_READY_ANNOUNCEMENT)"))

    def test_supervisor_starts_bmo_without_the_wake_phrase_only_during_first_boot(self):
        import supervisor
        path = Path(tempfile.mkdtemp()) / "first-boot.json"
        original = fb.FirstBootState.default
        fb.FirstBootState.default = classmethod(lambda cls: cls(path))
        try:
            self.assertFalse(supervisor._first_boot_open())
            fb.FirstBootState(path).begin()
            self.assertTrue(supervisor._first_boot_open())
        finally:
            fb.FirstBootState.default = original
        self.assertIn('service.request_start_bemo("first_boot")', (ROOT / "supervisor.py").read_text())


if __name__ == "__main__":
    unittest.main()
