"""Brain downloads and the simple benchmark (installer plan I4), with a fake Ollama: nothing is downloaded."""
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
import unittest.mock

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import model_installer
from systems import downloads as dl
from systems import first_boot as fb


class FakeOllama:
    """pull() streams progress like Ollama's API; fails the first `failures` pulls of a model."""
    def __init__(self, failures=0):
        self.failures, self.pulled, self.copied = failures, [], []

    def pull(self, source, stream=True):
        if self.failures:
            self.failures -= 1
            raise ConnectionError("connection reset by peer")
        self.pulled.append(source)
        yield types.SimpleNamespace(status="pulling manifest", digest=None, total=None, completed=None)
        for done in (0, 500, 1000):
            yield types.SimpleNamespace(status="pulling", digest="sha256:a", total=1000, completed=done)
        yield types.SimpleNamespace(status="success", digest=None, total=None, completed=None)

    def copy(self, source, alias):
        self.copied.append((source, alias))

    def show(self, alias):
        return {}


class Temp(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self._saved = (model_installer.STATE_DIR, model_installer.STATE_PATH)
        model_installer.STATE_DIR, model_installer.STATE_PATH = self.dir, self.dir / "model-roster.json"
        self.state = dl.DownloadState(self.dir / "downloads.json")

    def tearDown(self):
        model_installer.STATE_DIR, model_installer.STATE_PATH = self._saved


class OrderAndDownloadTests(Temp):
    def test_main_first_then_smallest_first(self):
        aliases = [m.alias for m in dl.roster()]
        self.assertEqual(aliases[0], "qwen3-main:30b")
        self.assertEqual(aliases[1], "qwen3-emergency:4b")
        sizes = [m.approx_gb for m in dl.roster()[1:]]
        self.assertEqual(sizes, sorted(sizes))

    def test_everything_in_order_then_the_benchmark(self):
        fake, seen = FakeOllama(), []

        def benchmark(progress):
            progress(2, 5, "testing 6 threads")
            seen.append(dict(json.loads(self.state.path.read_text())["benchmark"]))  # the screen's bar
            return {"ok": True, "threads": 8}

        ok = dl.run_all(self.state, fake, benchmark=benchmark, already=lambda a: False)
        self.assertTrue(ok)
        self.assertEqual(seen, [{"running": True, "step": 2, "steps": 5, "label": "testing 6 threads"}])
        self.assertEqual(fake.pulled, [m.source for m in dl.roster()])
        self.assertIn(("qwen3:30b-a3b-instruct-2507-q4_K_M", "qwen3-main:30b"), fake.copied)
        self.assertTrue(self.state.finished())
        self.assertEqual(self.state.data["benchmark"]["threads"], 8)
        records = json.loads(model_installer.STATE_PATH.read_text())["models"]  # model_installer agrees
        self.assertTrue(all(records[m.alias]["complete"] for m in dl.roster()))

    def test_the_game_player_and_screensavers_come_right_after_main(self):
        # 2026-10-24: the emulator must install, and no games, videos or Microsoft files ship in the image.
        fake, done = FakeOllama(), []
        order = []
        fake.pull = (lambda pull: lambda source, stream=True: (order.append(source), (yield from pull(source))))(
            fake.pull)
        extras = {dl.GAME_PLAYER: lambda: order.append("game player"),
                  dl.SCREENSAVERS: lambda: order.append("screensavers")}
        with unittest.mock.patch.dict(dl.EXTRA_INSTALLED, {dl.GAME_PLAYER: lambda: False,
                                                            dl.SCREENSAVERS: lambda: False}):
            self.assertTrue(dl.run_all(self.state, fake, already=lambda a: False, extras=extras))
        self.assertEqual(order[:3], ["qwen3:30b-a3b-instruct-2507-q4_K_M", "game player", "screensavers"])
        self.assertTrue(self.state.done(dl.GAME_PLAYER) and self.state.done(dl.SCREENSAVERS))
        image = (ROOT / "tools" / "image" / "in_chroot.sh").read_text()
        self.assertNotIn("fetch_legacy_screensavers.py fetch", image)
        self.assertIn("extras=downloads.EXTRAS", (ROOT / "downloads_cli.py").read_text())

    def test_dolphin_is_installed_for_this_account_with_its_save_folder(self):
        from systems import games
        calls = []
        run = lambda argv, **kw: calls.append(argv[1:]) or types.SimpleNamespace(returncode=0, stdout="", stderr="")
        with unittest.mock.patch.object(games.shutil, "which", lambda name: "/usr/bin/flatpak"):
            games.install_dolphin_flatpak(self.dir, run=run)
        self.assertEqual(calls[0], ["remote-add", "--user", "--if-not-exists", "flathub", games.FLATHUB_REPO])
        self.assertEqual(calls[1], ["install", "--user", "--noninteractive", "-y", "flathub", games.DOLPHIN_FLATPAK_ID])
        self.assertEqual(calls[2], ["override", "--user", f"--filesystem={self.dir / 'games' / 'dolphin-user'}",
                                    games.DOLPHIN_FLATPAK_ID])
        self.assertTrue((self.dir / "games" / "dolphin-user").is_dir())

    def test_extras_already_here_and_failing_extras(self):
        calls = []
        extras = {dl.GAME_PLAYER: lambda: calls.append("installed again")}
        with unittest.mock.patch.dict(dl.EXTRA_INSTALLED, {dl.GAME_PLAYER: lambda: True}):
            dl.run_all(self.state, FakeOllama(), already=lambda a: True, extras=extras)
        self.assertEqual(calls, [])
        state = dl.DownloadState(self.dir / "other.json")

        def broken():
            raise RuntimeError("Could not connect to flathub")
        with unittest.mock.patch.dict(dl.EXTRA_INSTALLED, {dl.GAME_PLAYER: lambda: False}):
            ok = dl.run_all(state, FakeOllama(), already=lambda a: True, extras={dl.GAME_PLAYER: broken},
                            sleep=lambda s: None, retries=2)
        self.assertFalse(ok)
        self.assertIn("Downloading my game player stopped", dl.report(state))

    def test_brains_already_here_are_not_downloaded_again(self):
        fake = FakeOllama()
        dl.run_all(self.state, fake, benchmark=None, already=lambda a: a != "qwen3-coder:30b")
        self.assertEqual(fake.pulled, ["qwen3-coder:30b-a3b-q4_K_M"])

    def test_a_failure_is_tried_twice_left_while_the_rest_carry_on_then_tried_once_more(self):
        # 2026-10-25: one stuck download used to stop everything after it.
        waits, fake = [], FakeOllama(failures=2)
        ok = dl.run_all(self.state, fake, benchmark=None, already=lambda a: False, sleep=waits.append)
        self.assertTrue(ok)
        self.assertEqual(waits, [10, 10])  # between its two tries, then before going back to it
        sources = [m.source for m in dl.roster()]
        self.assertEqual(fake.pulled, sources[1:] + sources[:1])  # Main came last, on the way back
        stubborn = dl.DownloadState(self.dir / "other.json")
        self.assertFalse(dl.run_all(stubborn, FakeOllama(failures=99), sleep=lambda s: None, already=lambda a: False))
        self.assertTrue(all(stubborn.model(m.alias)["status"] == dl.FAILED for m in dl.roster()))  # each one tried
        self.assertFalse(stubborn.finished())

    def test_no_internet_waits_instead_of_using_up_the_tries_then_stops(self):
        waits, fake = [], FakeOllama()
        answers = iter([False, False, True])
        self.assertTrue(dl.run_all(self.state, fake, already=lambda a: False, sleep=waits.append,
                                   online=lambda: next(answers, True)))
        self.assertEqual(waits, [5, 5])
        gone = dl.DownloadState(self.dir / "offline.json")
        self.assertFalse(dl.run_all(gone, fake, already=lambda a: False, sleep=lambda s: None,
                                    online=lambda: False, offline_wait=30))
        self.assertEqual(gone.model("qwen3-main:30b")["error"], dl.NO_INTERNET)
        self.assertEqual(gone.model("qwen3-coder:30b"), {})  # it stopped: nothing can download

    def test_the_benchmark_gets_a_second_go_and_runs_again_after_a_restart(self):
        results = [{"ok": False, "error": "busy"}, {"ok": True, "threads": 6}]
        dl.run_all(self.state, FakeOllama(), already=lambda a: True, benchmark=lambda progress: results.pop(0))
        self.assertEqual(self.state.data["benchmark"], {"ok": True, "threads": 6})
        cut_off = dl.DownloadState(self.dir / "cut.json")
        cut_off.data["benchmark"] = {"running": True, "step": 3, "steps": 5}  # a restart in the middle of it
        self.assertTrue(dl.benchmark_needed(cut_off))
        dl.run_all(cut_off, FakeOllama(), already=lambda a: True, benchmark=lambda progress: {"ok": True, "threads": 4})
        self.assertEqual(cut_off.data["benchmark"]["threads"], 4)
        self.assertFalse(dl.benchmark_needed(cut_off))

    def test_one_downloader_at_a_time(self):
        lock = self.dir / "downloads.lock"
        self.assertFalse(dl.running(lock))
        with open(lock, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(dl.running(lock))
        self.assertFalse(dl.running(lock))


class WordsTests(Temp):
    def test_progress_in_words(self):
        self.assertEqual(dl.report(dl.DownloadState(self.dir / "none.json")), "All my brains are downloaded.")
        self.state.set("qwen3-main:30b", status=dl.DOWNLOADING, total=19e9, completed=7.6e9, rate=10e6)
        text = dl.report(self.state)
        self.assertIn("My main brain is 40 percent downloaded, about 19 minutes left.", text)
        self.assertIn(f"{len(dl.roster()) - 1} more after that", text)  # 4 since Fast Vision was dropped
        self.state.set("qwen3-main:30b", status=dl.DONE)
        self.state.set("qwen3-emergency:4b", status=dl.FAILED, error="Temporary failure in name resolution")
        text = dl.report(self.state)
        self.assertTrue(text.startswith("My main brain is ready."))
        self.assertIn("the internet connection", text)
        self.state.data.update(finished_at=1, benchmark={"ok": True, "threads": 12})
        self.assertIn("works best with 12 threads", dl.report(self.state))

    def test_hours_for_long_waits(self):
        self.state.set("qwen3-main:30b", status=dl.DOWNLOADING, total=19e9, completed=1e9, rate=1e6)
        self.assertIn("about 5 hours left", dl.report(self.state))

    def test_progress_questions(self):
        for text in ("How are the downloads?", "how's the download going", "What's the download status?",
                     "how long until the downloads are done"):
            self.assertTrue(dl.is_progress_question(text), text)
        for text in ("download the weather report", "Can you download a picture of a cat?", "What time is it?"):
            self.assertFalse(dl.is_progress_question(text), text)
        self.assertTrue(dl.is_progress_question("how much longer", waiting=True))
        self.assertTrue(dl.is_progress_question("how's it going?", waiting=True))

    def test_none_of_the_owners_real_sentences_is_a_downloads_question(self):
        lines = (ROOT / "tests" / "fixtures" / "voice" / "heard.jsonl").read_text().splitlines()
        said = [json.loads(l).get("said") for l in lines if l.strip()]
        hits = [s for s in said if s and dl.is_progress_question(s)]
        self.assertGreater(len([s for s in said if s]), 100)
        self.assertEqual(hits, [])

    def test_space_still_needed(self):
        self.assertAlmostEqual(dl.still_needed_gb(self.state), sum(m.approx_gb for m in dl.roster()))
        self.state.set("qwen3-main:30b", status=dl.DOWNLOADING, total=19e9, completed=9e9)
        self.assertAlmostEqual(dl.still_needed_gb(self.state, already=lambda a: a != "qwen3-main:30b"), 10.0)


class BackgroundTests(Temp):
    def test_started_only_when_unfinished_and_not_running(self):
        calls = []
        original = dl.data_dir
        dl.data_dir = lambda: self.dir
        try:
            self.assertFalse(dl.resume_if_unfinished(popen=lambda *a, **k: calls.append(a)))  # never started
            dl.DownloadState().set("qwen3-main:30b", status=dl.DOWNLOADING)
            self.assertTrue(dl.resume_if_unfinished(popen=lambda *a, **k: calls.append(a)))
            self.assertTrue(calls[0][0][-2].endswith("downloads_cli.py"))
            state = dl.DownloadState()
            state.data["finished_at"] = 1
            state.save()
            self.assertFalse(dl.start_in_background(popen=lambda *a, **k: calls.append(a)))
        finally:
            dl.data_dir = original


class StepIO:
    """BMO's side of the downloads step. Each pause moves the fake downloader along `script`: a dict of
    download state to set, or "stop" (the downloader exits)."""
    def __init__(self, state, *, free=500.0, installed=(), script=(), online=True):
        self.state, self.free, self.installed = state, free, set(installed)
        self.script, self._online = list(script), online
        self.said, self.starts, self.clock, self.running = [], 0, 0.0, False
        self.view = fb.SetupView(blocking=False)
        self.view.open()

    def say(self, text): self.said.append(text)
    def downloads_state(self): return self.state
    def model_installed(self, alias): return alias in self.installed
    def free_gb(self): return self.free
    def downloader_running(self): return self.running
    def now(self): return self.clock
    def online(self): return self._online

    def start_downloads(self):
        self.starts += 1
        self.running = True

    def pause(self, seconds):
        self.clock += seconds
        if self.script:
            step = self.script.pop(0)
            if not step:
                pass
            elif step == "stop":
                self.running = False
            elif step == "online":
                self._online = True
            elif "finished_at" in step or "benchmark" in step:
                self.state.data.update(step)
                self.state.save()
            else:
                alias, fields = step
                self.state.set(alias, **fields)


class FakeInternet:
    def __init__(self, io):
        self.io, self.waited = io, 0

    def wait(self, io, lost=False):
        self.waited += 1
        io._online = True
        return fb.StepResult(fb.DONE, {"online": True})


class DownloadsStepTests(Temp):
    def step(self, io=None):
        step = fb.DownloadsStep()
        step.internet = FakeInternet(io)
        return step

    def test_follows_the_downloader_with_the_bars_until_everything_is_in(self):
        io = StepIO(self.state, script=[("qwen3-main:30b", dict(status=dl.DOWNLOADING, total=19e9, completed=9.5e9,
                                                                 rate=20e6)),
                                        ("qwen3-main:30b", dict(status=dl.DONE)),
                                        {"benchmark": {"ok": True, "threads": 6}, "finished_at": 1}])
        shown = []
        original = io.view.row
        io.view.row = lambda key, *a: (shown.append((key,) + a), original(key, *a))
        outcome = fb.DownloadsStep().run(io)
        self.assertEqual((outcome.status, outcome.result), (fb.DONE, {"threads": 6}))
        self.assertIn(("qwen3-main:30b", fb.RUNNING, "50%  8 min left", 0.5), shown)
        self.assertIn("you don't need to stay", io.said[0])
        self.assertTrue(self.state.data["announced"])  # no spoken report at the next start

    def test_not_enough_disk_says_how_much(self):
        io = StepIO(self.state, free=40)
        outcome = fb.DownloadsStep().run(io)
        self.assertEqual((outcome.status, outcome.result["reason"]), (fb.FAILED, "disk"))
        self.assertIn("more gigabytes of free disk space", io.said[0])
        self.assertEqual(io.starts, 0)
        self.assertEqual(fb.problem_lines("downloads", outcome.result),
                         [f"Brains: I need about {outcome.result['need_gb'] - 40} GB more free disk space."])

    def test_everything_already_here_goes_on_at_once(self):
        self.state.data.update(finished_at=1, benchmark={"ok": True, "threads": 6})
        self.state.save()
        io = StepIO(self.state)
        outcome = fb.DownloadsStep().run(io)
        self.assertEqual(outcome.status, fb.DONE)
        self.assertEqual((io.starts, io.said), (0, []))

    def test_a_download_that_kept_failing_is_a_problem_not_a_hold_up(self):
        io = StepIO(self.state, script=[("qwen3-coder:30b", dict(status=dl.FAILED, error="connection reset by peer"))]
                    + ["stop"] + [{}] * 40)
        outcome = fb.DownloadsStep().run(io)
        self.assertEqual(outcome.status, fb.FAILED)
        self.assertEqual(outcome.result, {"reason": "download", "failed": {"qwen3-coder:30b": "the internet connection"}})
        self.assertEqual(fb.problem_lines("downloads", outcome.result), ["Coding brain: the internet connection."])

    def test_lost_internet_waits_for_it_then_starts_the_downloader_again(self):
        io = StepIO(self.state, online=False, script=["stop"] + [{}] * 35 +
                    [{"benchmark": {"ok": True, "threads": 4}, "finished_at": 1}])
        step = self.step(io)
        outcome = step.run(io)
        self.assertEqual(outcome.status, fb.DONE)
        self.assertEqual((step.internet.waited, io.starts), (1, 2))
        self.assertIn("I lost the internet. I'll carry on as soon as it's back.", io.said)

    def test_a_speed_test_that_failed_is_run_again(self):
        self.state.data.update(finished_at=1, benchmark={"ok": False, "error": "busy"})
        self.state.save()
        io = StepIO(self.state, installed={m.alias for m in dl.roster()},
                    script=[{"benchmark": {"ok": True, "threads": 6}, "finished_at": 2}])
        outcome = fb.DownloadsStep().run(io)
        self.assertEqual((outcome.status, io.starts), (fb.DONE, 1))
        failed = StepIO(dl.DownloadState(self.dir / "f.json"),
                        script=[{"benchmark": {"ok": False, "error": "busy"}, "finished_at": 1}])
        outcome = fb.DownloadsStep().run(failed)
        self.assertEqual((outcome.status, outcome.result["reason"]), (fb.FAILED, "speed test"))


class WiringTests(unittest.TestCase):
    def test_bmo_and_the_supervisor_use_it(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn("if self._handle_downloads_question(command):", chat)
        self.assertLess(chat.index('self._first_boot("listening")'), chat.index("self._announce_downloads_finished()"))
        self.assertIn("resume_if_unfinished()", (ROOT / "supervisor.py").read_text())
        self.assertEqual(fb.STEP_ORDER[:3], ("internet", "updates", "downloads"))  # downloads before the person
        self.assertIn("online=online", (ROOT / "downloads_cli.py").read_text())


if __name__ == "__main__":
    unittest.main()
