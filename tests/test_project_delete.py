"""Deleting a project from the project list (2026-11-03). Nothing goes without its name said back and "Hey BMO, yes", and
it goes to the Trash. Any doubt keeps it: a wrong word, silence, no Trash, a project that isn't on the list."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import pygame
import visual_smoke_test as smoke
import voice_commands
from systems import library as lib, projects as projects_module
from systems.chat import Chat
from systems.projects import ProjectManager
from systems.render import FaceRenderer


def make_manager(root):
    return ProjectManager(data_root=root, projects_root=root / "projects", index_extensions=(".txt",), exclude_dirs=(".git",), max_file_bytes=100000,
                          context_char_budget=1000, retrieval_chunks=2, reindex_interval_seconds=0.0, auto_git=False)


class Words(unittest.TestCase):
    def test_a_number(self):
        for said, number in (("delete number 2", 2), ("Delete number two.", 2), ("delete 2", 2), ("delete two", 2), ("delete the second one", 2), ("delete project number 2", 2),
                             ("Hey BMO, delete number 2", 2), ("remove number 2", 2), ("trash number 2", 2), ("get rid of number 2", 2), ("throw away number 2", 2), ("erase number 2", 2),
                             ("deleted number 2", 2), ("the lead number 2", 2), ("can you delete number 2 please", 2)):
            self.assertEqual(lib.delete_request(said)["number"], number, said)

    def test_a_name(self):
        for said, name in (("delete project garden", "garden"), ("Delete Project Garden.", "garden"), ("delete garden", "garden"), ("delete the garden project", "garden"),
                           ("delete my garden project", "garden"), ("delete the project called garden", "garden"), ("remove project garden", "garden"), ("the lead project garden", "garden"),
                           ("delete project big garden", "big garden")):
            self.assertEqual(lib.delete_request(said)["name"], name, said)

    def test_this_one(self):
        for said in ("delete it", "delete this", "delete project", "delete this project", "delete that project", "get rid of it"):
            self.assertTrue(lib.delete_request(said)["this"], said)

    def test_named_as_a_project(self):
        for said in ("delete project garden", "delete the garden project", "delete my project", "remove this project", "delete project number 2"):
            self.assertTrue(lib.delete_request(said)["says_project"], said)
        for said in ("delete number 2", "delete garden", "delete it", "delete this file from my project", "delete the project files", "remove report.txt from the project"):
            self.assertFalse((lib.delete_request(said) or {"says_project": False})["says_project"], said)

    def test_not_deleting(self):
        for said in ("don't delete number 2", "do not delete project garden", "never delete project garden", "should I delete number 2", "how do I delete a project", "what does delete do",
                     "delete", "remove", "open number 2", "number 2", "close", "deleted scenes", "the deleted scene", "trash", "the lead singer", "the lead project"):
            self.assertIsNone(lib.delete_request(said), said)


class Trash(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.manager = make_manager(self.root)
        self.manager.create_project("Garden")
        self.manager.close_project()
        self.trashed = []

    def trash(self, path):
        self.trashed.append(path)
        path.rename(self.root / "trash")
        return True

    def test_it_goes_to_the_trash(self):
        self.manager.trash_project("garden", trash=self.trash)
        self.assertEqual(self.trashed, [self.root / "projects" / "garden"])
        self.assertEqual(self.manager.list_projects(), [])
        self.assertTrue((self.root / "trash" / "project.json").exists())

    def test_an_open_project_is_closed_first(self):
        self.manager.open_project("garden")
        self.manager.trash_project("garden", trash=self.trash)
        self.assertIsNone(self.manager.active_slug)

    def test_no_trash_means_it_stays_and_stays_open(self):
        self.manager.open_project("garden")
        for failing in (lambda path: False, lambda path: True):  # refused, or said yes and left it there
            with self.assertRaises(OSError):
                self.manager.trash_project("garden", trash=failing)
            self.assertEqual(self.manager.active_slug, "garden")
            self.assertTrue((self.root / "projects" / "garden" / "project.json").exists())

    def test_only_a_real_project(self):
        (self.root / "projects" / "notes").mkdir()
        for slug in ("nothing", "", "..", "../..", "notes"):
            with self.assertRaises(OSError):
                self.manager.trash_project(slug, trash=self.trash)
        self.assertEqual(self.trashed, [])

    def test_with_no_way_to_trash_it_nothing_happens(self):
        system = mock.Mock()
        system.trash_command.return_value = None
        with mock.patch("systems.adapters.system", return_value=system):
            self.assertFalse(projects_module._to_trash(self.root / "projects" / "garden"))
        self.assertTrue((self.root / "projects" / "garden").exists())

    def test_it_never_removes_anything_itself(self):
        source = (ROOT / "systems" / "projects.py").read_text()
        body = source[source.index("def _to_trash"):source.index("def _slugify")] + source[source.index("def trash_project"):source.index("def active_metadata")]
        for forbidden in ("rmtree", "unlink", "os.remove", ".rmdir"):
            self.assertNotIn(forbidden, body)


class TheList(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.manager = make_manager(self.root)
        for name in ("Garden", "Robot Arm", "Shed"):
            self.manager.create_project(name)
        self.manager.close_project()
        chat = self.chat = Chat.__new__(Chat)
        chat.project_manager = self.manager
        chat.library_view = lib.LibraryView()
        chat.note_user_activity = lambda: None
        self.said, self.asked, self.answer = [], [], True
        chat._say_local = lambda command, text: self.said.append(text) or True

        def confirm(question, **settings):
            self.asked.append((question, settings))
            return self.answer
        chat._confirm_by_voice = confirm
        self.chat._handle_library_command("show my projects")
        self.said.clear()

    def delete(self, said):
        def fake(path):
            path.rename(self.root / f"trashed-{path.name}")
            return True
        real = ProjectManager.trash_project
        with mock.patch.object(ProjectManager, "trash_project", lambda manager, slug: real(manager, slug, trash=fake)):
            return self.chat._handle_library_command(said)

    def slugs(self):
        return [project["slug"] for project in self.manager.list_projects()]

    def test_by_number_says_its_name_and_asks(self):
        self.assertTrue(self.delete("delete number 2"))
        self.assertIn("Delete the project Robot Arm?", self.asked[0][0])
        self.assertIn("Say Hey BMO, yes, or no.", self.asked[0][0])
        self.assertEqual(self.asked[0][1]["tag"], "PROJECT_DELETE")
        self.assertEqual(self.slugs(), ["garden", "shed"])
        self.assertEqual(self.said[-1], "Robot Arm is in the Trash, if you change your mind.")

    def test_by_name(self):
        self.delete("Delete Project Garden.")  # heard live
        self.assertIn("Delete the project Garden?", self.asked[0][0])
        self.assertEqual(self.slugs(), ["robot-arm", "shed"])

    def test_its_files_are_counted(self):
        slug = "garden"
        (self.root / "projects" / slug / "workspace" / "a.txt").write_text("x")
        self.delete("delete number 1")
        self.assertIn("It has 1 file.", self.asked[0][0])

    def test_no_keeps_it(self):
        self.answer = False
        self.delete("delete number 2")
        self.assertEqual(self.slugs(), ["garden", "robot-arm", "shed"])
        self.assertEqual(self.said[-1], "Okay, I kept Robot Arm.")

    def test_the_list_updates_and_stays_up(self):
        self.delete("delete number 1")
        shot = self.chat.library_view.snapshot()
        self.assertEqual((shot["kind"], shot["total"]), ("projects", 2))

    def test_the_last_one_leaves_an_empty_list(self):
        for _ in range(3):
            self.delete("delete number 1")
        self.assertEqual(self.chat.library_view.snapshot()["total"], 0)
        self.assertEqual(self.slugs(), [])

    def test_an_open_project_is_marked_but_named_plainly(self):
        self.manager.open_project("garden")
        self.chat._handle_library_command("show my projects")
        self.delete("delete number 1")
        self.assertIn("Delete the project Garden?", self.asked[0][0])
        self.assertNotIn("(open)", self.asked[0][0])
        self.assertIsNone(self.manager.active_slug)

    def test_nothing_is_asked_about_what_isnt_there(self):
        self.delete("delete number 9")
        self.assertEqual(self.said[-1], "There's no number 9. The list goes up to 3.")
        self.delete("delete project treehouse")
        self.assertEqual(self.said[-1], "I don't see a project called treehouse. Say delete and its number.")
        self.delete("delete it")
        self.assertEqual(self.said[-1], "Which one? Say delete and its number.")
        self.assertEqual(self.asked, [])
        self.assertEqual(self.slugs(), ["garden", "robot-arm", "shed"])

    def test_a_close_name_is_not_enough(self):
        self.delete("delete project gardens")
        self.delete("delete project robot")
        self.assertEqual(self.asked, [])

    def test_if_the_trash_fails_he_says_so(self):
        with mock.patch.object(ProjectManager, "trash_project", side_effect=OSError("no")):
            self.chat._handle_library_command("delete number 1")
        self.assertEqual(self.said[-1], "I couldn't move Garden to the Trash, so it's still here.")
        self.assertIn("garden", self.slugs())

    def test_only_the_project_list(self):
        self.chat.library_view.open("movies", [lib.Item("Spirited Away", "movie", "/x.mkv")], grid=False)
        self.assertFalse(self.delete("delete number 1"))
        self.assertEqual(self.asked, [])

    def test_it_never_plays_what_it_was_told_to_delete(self):
        self.chat.library_view.open("movies", [lib.Item("Spirited Away", "movie", "/x.mkv")], grid=False)
        self.chat._library_action = mock.Mock(return_value=True)
        self.assertFalse(self.chat._handle_library_command("delete number 1"))
        self.assertFalse(self.chat._bare_library_command("delete number 1"))
        self.chat._library_action.assert_not_called()

    def test_with_the_list_closed_a_project_opens_the_list(self):
        self.chat.library_view.close()
        self.assertTrue(self.delete("delete project garden"))
        self.assertEqual(self.said[-1], "Here are your projects. Say delete and its number.")
        self.assertEqual(self.chat.library_view.kind, "projects")
        self.assertEqual(self.asked, [])
        self.assertIn("garden", self.slugs())

    def test_with_the_list_closed_the_rest_isnt_ours(self):
        self.chat.library_view.close()
        for said in ("delete number 1", "delete garden", "delete it", "delete trash-test.txt", "delete this file from my project", "delete the project files"):
            self.assertFalse(self.delete(said), said)
        self.assertEqual(self.asked, [])

    def test_without_hey_bmo_it_starts_but_still_takes_the_yes(self):
        self.assertTrue(self.chat._bare_library_command("delete number 1"))
        self.assertEqual(len(self.asked), 1)

    def test_room_talk_with_the_list_up_doesnt_start_it(self):
        for said in ("I should delete that old garden file sometime soon today", "don't delete number 1", "how do I delete a project"):
            self.assertFalse(self.chat._bare_library_command(said), said)
        self.assertEqual(self.asked, [])


class TheConfirmation(unittest.TestCase):
    """The real _confirm_by_voice, with heard speech fed in."""

    def run_with(self, *heard):
        chat = Chat.__new__(Chat)
        chat._active_microphone = object()
        chat.stop_event = mock.Mock(is_set=lambda: False)
        spoken = []
        chat.speak = spoken.append
        said = iter(heard)

        def listen(source):
            try:
                return next(said)
            except StopIteration:
                chat.stop_event.is_set = lambda: True
                return ""
        chat.listen = listen
        gate = mock.Mock()

        def clipped_wake(text):
            lowered = text.lower()
            for wake in ("hey bmo, ", "hey bmo "):
                if lowered.startswith(wake):
                    return True, text[len(wake):]
            return False, text
        gate.clipped_wake = clipped_wake
        chat.voice_gate = gate
        source = (ROOT / "systems" / "chat.py").read_text()
        no = source[source.index('extra_no={"keep it", "keep"'):]
        no = eval(no[len("extra_no="):no.index("})") + 1])  # the same words the real command passes
        approved = chat._confirm_by_voice("Delete the project Garden?", tag="PROJECT_DELETE", target="garden", extra_yes={"delete it", "yes delete it"}, extra_no=no)
        return approved, spoken

    def test_only_hey_bmo_yes_counts(self):
        self.assertTrue(self.run_with("Hey BMO, yes")[0])
        self.assertTrue(self.run_with("Hey BMO, delete it")[0])

    def test_a_plain_yes_isnt_enough(self):
        approved, spoken = self.run_with("yes")
        self.assertFalse(approved)
        self.assertIn("Say: Hey BMO, yes.", spoken)

    def test_no_and_silence_keep_it(self):
        for heard in (("no",), ("keep it",), ("never mind",), ()):
            self.assertFalse(self.run_with(*heard)[0], heard)

    def test_every_way_of_saying_no_counts_the_first_time(self):
        import systems.chat as chat_module
        for said in ("No.", "Know.", "Now.", "Nope.", "No, keep it.", "Don't.", "No, don't.", "Don't delete it.", "No, don't delete it.", "Do not delete it."):
            with self.subTest(said=said), mock.patch.object(chat_module.logger, "info") as log:
                approved, _ = self.run_with(said)
                self.assertFalse(approved)
                self.assertIn("declined", " ".join(str(call) for call in log.call_args_list))


class TheScreen(unittest.TestCase):
    def test_the_hint_fits(self):
        pygame.init()
        self.addCleanup(pygame.quit)
        for size in ((640, 480), (1024, 768)):
            app = smoke.FakeApp()
            app.SCREEN_WIDTH, app.SCREEN_HEIGHT = size
            app._screen = pygame.Surface(size)
            renderer = FaceRenderer(app)
            small = renderer._setup_fonts(size[1])[2]
            self.assertLess(small.size("Say: open number 2, delete number 2, scroll down, or close")[0], size[0] * 0.9, size)

    def test_it_is_in_the_printed_list(self):
        self.assertIn("delete number 2", (Path(voice_commands.__file__).parent / "VOICE_COMMANDS.md").read_text())


if __name__ == "__main__":
    unittest.main()
