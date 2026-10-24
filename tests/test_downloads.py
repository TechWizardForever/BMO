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
        fake = FakeOllama()
        ok = dl.run_all(self.state, fake, benchmark=lambda: {"ok": True, "threads": 8}, already=lambda a: False)
        self.assertTrue(ok)
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

    def test_a_broken_connection_is_retried_then_left_for_the_next_start(self):
        waits = []
        ok = dl.run_all(self.state, FakeOllama(failures=2), benchmark=None, already=lambda a: False, sleep=waits.append)
        self.assertTrue(ok)
        self.assertEqual(waits, [10, 20])
        stubborn = dl.DownloadState(self.dir / "other.json")
        self.assertFalse(dl.run_all(stubborn, FakeOllama(failures=99), retries=3, sleep=lambda s: None,
                                    already=lambda a: False))
        self.assertEqual(stubborn.model("qwen3-main:30b")["status"], dl.FAILED)
        self.assertFalse(stubborn.finished())

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
    """BMO's side of the downloads step; each listen moves the fake download along."""
    def __init__(self, state, *, free=500.0, installed=(), heard=(), progress=(), downloader=True):
        self.state, self.free, self.installed = state, free, set(installed)
        self.heard, self.progress, self.downloader = list(heard), list(progress), downloader
        self.said, self.starts, self.prepared, self.clock = [], 0, 0, 0.0

    def say(self, text): self.said.append(text)
    def downloads_state(self): return self.state
    def model_installed(self, alias): return alias in self.installed
    def free_gb(self): return self.free
    def start_downloads(self): self.starts += 1
    def downloader_running(self): return self.downloader
    def now(self): return self.clock
    def pause(self, seconds): self.clock += seconds
    def prepare_main(self): self.prepared += 1

    def hear(self):
        self.clock += 10
        if self.progress:
            self.state.set("qwen3-main:30b", **self.progress.pop(0))
        return self.heard.pop(0) if self.heard else None


class DownloadsStepTests(Temp):
    def test_waits_for_main_answers_questions_then_loads_it(self):
        io = StepIO(self.state, heard=["how much longer?", None, "what's on TV"],
                    progress=[dict(status=dl.DOWNLOADING, total=19e9, completed=9.5e9, rate=20e6), {},
                              dict(status=dl.DONE)])
        outcome = fb.DownloadsStep().run(io)
        self.assertEqual((outcome.status, outcome.result), (fb.DONE, {"main": "downloaded"}))
        self.assertIn("50 percent", io.said[1])
        self.assertEqual(len(io.said), 3)  # the TV talk got no answer
        self.assertEqual(io.prepared, 1)
        self.assertIn("we can talk now", io.said[-1])

    def test_not_enough_disk_says_how_much(self):
        io = StepIO(self.state, free=40)
        outcome = fb.DownloadsStep().run(io)
        self.assertEqual(outcome.status, fb.FAILED)
        self.assertIn("free disk space", io.said[0])
        self.assertEqual(io.starts, 0)

    def test_main_already_here_goes_on_at_once(self):
        io = StepIO(self.state, installed={"qwen3-main:30b"})
        outcome = fb.DownloadsStep().run(io)
        self.assertEqual(outcome.result["main"], "already there")
        self.assertEqual(io.starts, 1)
        self.assertIn("in the background", io.said[0])
        everything = StepIO(dl.DownloadState(self.dir / "x.json"), installed={m.alias for m in dl.roster()})
        fb.DownloadsStep().run(everything)
        self.assertEqual(everything.said, [])  # nothing to say on a PC that has every brain

    def test_a_stopped_downloader_is_restarted_then_given_up(self):
        io = StepIO(self.state, downloader=False)
        outcome = fb.DownloadsStep().run(io)
        self.assertEqual(outcome.status, fb.FAILED)
        self.assertEqual(io.starts, 4)
        self.assertIn("next time I start", io.said[-1])


class WiringTests(unittest.TestCase):
    def test_bmo_and_the_supervisor_use_it(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn("if self._handle_downloads_question(command):", chat)
        self.assertLess(chat.index('self._first_boot("listening")'), chat.index("self._announce_downloads_finished()"))
        self.assertIn("resume_if_unfinished()", (ROOT / "supervisor.py").read_text())
        self.assertEqual(fb.STEP_ORDER[-1], "downloads")


if __name__ == "__main__":
    unittest.main()
