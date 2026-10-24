"""Signed updates from Git, with backup and switch-back (installer plan I5).

Real git and real SSH signing keys in a temporary folder: a 'public' repository, an installed copy, and
tags signed with the release key, another key, or none. Nothing leaves the folder; pip is never run.
"""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems import updates as up

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@x", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")


def sh(*argv, cwd=None):
    done = subprocess.run(argv, cwd=cwd, env=GIT_ENV, capture_output=True, text=True)
    if done.returncode:
        raise AssertionError(f"{argv}: {done.stderr}")
    return done.stdout


@unittest.skipUnless(shutil.which("git") and shutil.which("ssh-keygen"), "git and ssh-keygen needed")
class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.release_key, self.other_key = self.key("release"), self.key("other")
        self.origin, self.pub, self.app = self.dir / "origin.git", self.dir / "published", self.dir / "app"
        self.data = self.dir / "data"
        sh("git", "init", "-q", "--bare", str(self.origin))
        sh("git", "init", "-q", "-b", "main", str(self.pub))
        self.write("1.0.0", signers=True)
        sh("git", "add", "-A", cwd=self.pub)
        sh("git", "commit", "-qm", "1.0.0", cwd=self.pub)
        sh("git", "remote", "add", "origin", str(self.origin), cwd=self.pub)
        sh("git", "push", "-q", "origin", "main", cwd=self.pub)
        sh("git", "clone", "-q", "-b", "main", str(self.origin), str(self.app))
        (self.data / "memory").mkdir(parents=True)
        (self.data / "models").mkdir()
        (self.data / "models" / "big.bin").write_bytes(b"x" * 1000)
        with sqlite3.connect(self.data / "memory" / "conversation.sqlite3") as db:
            db.execute("create table m (t text)")
            db.execute("insert into m values ('hello')")
        self.pip_calls = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def key(self, name):
        path = self.dir / name
        sh("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path))
        return path

    def write(self, version, *, signers=False, self_test_ok=True, lock="numpy==1.26.4\n"):
        (self.pub / "installer").mkdir(exist_ok=True)
        (self.pub / "config.py").write_text(f'APP_VERSION = "supervisor-v{version}-test"\n')
        (self.pub / "self_test.py").write_text("raise SystemExit(0)\n" if self_test_ok else "raise SystemExit(1)\n")
        (self.pub / "installer" / "requirements.lock").write_text(lock)
        if signers:
            public = (self.release_key.with_suffix(".pub")).read_text().strip()
            (self.pub / "installer" / "allowed_signers").write_text(f'bmo-release namespaces="git" {public}\n')

    def release(self, version, *, key="release", subject="Faster wake", **kwargs):
        self.write(version, **kwargs)
        sh("git", "commit", "-qam", version, cwd=self.pub)
        if key is None:
            sh("git", "tag", "-a", f"v{version}", "-m", subject, cwd=self.pub)
        else:
            signing = {"release": self.release_key, "other": self.other_key}[key]
            sh("git", "-c", "gpg.format=ssh", "-c", f"user.signingkey={signing}", "tag", "-s", f"v{version}",
               "-m", subject, cwd=self.pub)
        sh("git", "push", "-q", "origin", "main", "--tags", cwd=self.pub)

    def run_fake_pip(self, argv, **kwargs):
        if "pip" in argv:
            self.pip_calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        kwargs.setdefault("env", None)
        return subprocess.run(argv, **kwargs)

    def updater(self, clock=None):
        kwargs = {"clock": clock} if clock else {}
        return up.Updater(self.app, self.data, run=self.run_fake_pip, python=sys.executable, **kwargs)

    def head(self):
        return sh("git", "rev-parse", "HEAD", cwd=self.app).strip()

    # --- checking ---
    def test_a_signed_newer_release_is_offered(self):
        self.release("1.1.0")
        found = self.updater().check()
        self.assertEqual((found.status, found.update.tag, found.update.subject), ("available", "v1.1.0", "Faster wake"))
        self.assertIn("Version 1.1.0 is available. Faster wake. Say, Hey BMO, yes", up.spoken_check(found))

    def test_wrong_key_or_no_signature_is_refused(self):
        self.release("1.1.0", key="other")
        self.assertEqual(self.updater().check().status, "unsigned")
        self.release("1.2.0", key=None)
        found = self.updater().check()
        self.assertEqual((found.status, found.detail), ("unsigned", "v1.2.0"))
        self.assertIn("isn't signed", up.spoken_check(found))

    def test_the_newest_signed_one_wins_over_a_newer_unsigned_one(self):
        self.release("1.1.0")
        self.release("1.2.0", key="other")
        self.assertEqual(self.updater().check().update.tag, "v1.1.0")

    def test_up_to_date_local_changes_no_key(self):
        self.assertEqual(self.updater().check().status, "current")
        (self.app / "config.py").write_text('APP_VERSION = "supervisor-v1.0.0-edited"\n')
        self.assertEqual(self.updater().check().status, "local_changes")
        sh("git", "checkout", "-q", "config.py", cwd=self.app)
        (self.app / "installer" / "allowed_signers").write_text("# none yet\n")
        sh("git", "commit", "-qam", "no key", cwd=self.app)
        self.assertEqual(self.updater().check().status, "no_release_key")

    def test_a_new_key_in_the_update_does_not_count_yet(self):
        # The keys come from the running version: an update can't vouch for itself.
        self.write("1.1.0")
        public = self.other_key.with_suffix(".pub").read_text().strip()
        (self.pub / "installer" / "allowed_signers").write_text(f'evil namespaces="git" {public}\n')
        sh("git", "commit", "-qam", "1.1.0", cwd=self.pub)
        sh("git", "-c", "gpg.format=ssh", "-c", f"user.signingkey={self.other_key}", "tag", "-s", "v1.1.0", "-m",
           "x", cwd=self.pub)
        sh("git", "push", "-q", "origin", "main", "--tags", cwd=self.pub)
        self.assertEqual(self.updater().check().status, "unsigned")

    # --- installing ---
    def test_install_backs_up_checks_out_and_waits_for_the_first_start(self):
        before = self.head()
        self.release("1.1.0")
        updater = self.updater()
        result = updater.install(updater.check().update)
        self.assertTrue(result["ok"], result)
        self.assertEqual(sh("git", "describe", "--tags", cwd=self.app).strip(), "v1.1.0")
        state = updater.state()
        self.assertEqual((state["status"], state["from"]), (up.VERIFYING, before))
        backup = Path(state["backup"])
        self.assertFalse((backup / "models").exists())
        with sqlite3.connect(backup / "memory" / "conversation.sqlite3") as db:
            self.assertEqual(db.execute("select t from m").fetchall(), [("hello",)])
        self.assertEqual(self.pip_calls, [])  # the package list didn't change
        self.assertEqual(updater.mark_ready(), "I'm updated to version 1.1.0.")
        self.assertIsNone(updater.mark_ready())

    def test_missing_linux_packages_are_named_after_an_update(self):
        # Audit 2026-10-24: an update can't apt-install (that needs the owner's password), so OpenSCAD, added
        # after the first public version, would just be missing. BMO now says what to type.
        updater = self.updater()
        (self.app / "installer").mkdir(exist_ok=True)
        (self.app / "installer" / "apt-packages.txt").write_text(
            "# comment\ngit coreutils\nbmo-test-not-a-real-package  # never installed\n")
        updater.save({"status": up.VERIFYING, "to_version": [1, 1, 0], "started_at": updater.clock()})
        self.assertEqual(updater.missing_system_packages(), ["bmo-test-not-a-real-package"])
        self.assertEqual(updater.mark_ready(), "I'm updated to version 1.1.0. This version also needs "
                         "bmo-test-not-a-real-package, which I can't install myself. Open the Terminal and type: "
                         "sudo apt install bmo-test-not-a-real-package.")

    def test_a_changed_package_list_is_installed(self):
        self.release("1.1.0", lock="numpy==1.26.4\nrequests==2.34.2\n")
        updater = self.updater()
        self.assertTrue(updater.install(updater.check().update)["ok"])
        self.assertEqual(len(self.pip_calls), 2)
        self.assertIn("--no-deps", self.pip_calls[1])

    def test_a_failing_self_test_goes_back_at_once(self):
        before = self.head()
        self.release("1.1.0", self_test_ok=False)
        updater = self.updater()
        result = updater.install(updater.check().update)
        self.assertEqual(result, {"ok": False, "reason": "self-test"})
        self.assertEqual(self.head(), before)
        self.assertIn("didn't work, because the new version failed its self-test", updater.mark_ready())
        self.assertIsNone(updater.mark_ready())  # said once

    def test_the_supervisor_puts_back_a_crashing_or_slow_version(self):
        before = self.head()
        self.release("1.1.0")
        now = [1000.0]
        updater = self.updater(clock=lambda: now[0])
        updater.install(updater.check().update)
        self.assertFalse(updater.roll_back_if_unhealthy(crashed=False))  # still within 15 minutes
        now[0] += up.VERIFY_SECONDS + 1
        self.assertTrue(updater.roll_back_if_unhealthy(crashed=False))
        self.assertEqual(self.head(), before)
        self.assertFalse(updater.roll_back_if_unhealthy(crashed=True))  # nothing left to undo
        self.release("1.2.0")
        updater.install(updater.check().update)
        self.assertTrue(updater.roll_back_if_unhealthy(crashed=True))
        self.assertIn("crashed", updater.mark_ready())

    def test_only_three_backups_are_kept(self):
        updater = self.updater()
        for n in range(5):
            updater.backup(f"b{n}")
        self.assertEqual(len(list((self.data / "update-backups").iterdir())), up.KEEP_BACKUPS)


class WordsTests(unittest.TestCase):
    def test_update_requests(self):
        for text in ("check for updates", "Check for any updates.", "are there any updates", "update yourself",
                     "can you check for updates"):
            self.assertTrue(up.is_update_request(text), text)
        for text in ("update my resume", "what's the update on the weather", "check the weather"):
            self.assertFalse(up.is_update_request(text), text)

    def test_wiring(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn("if self._handle_update_command(command):", chat)
        self.assertIn('tag="UPDATE"', chat)  # installing needs "Hey BMO, yes"
        self.assertIn("self._announce_update_result()", chat)
        sup = (ROOT / "supervisor.py").read_text()
        self.assertIn("_update_rolled_back(crashed=True)", sup)
        self.assertIn("_update_rolled_back(crashed=False)", sup)


if __name__ == "__main__":
    unittest.main()
