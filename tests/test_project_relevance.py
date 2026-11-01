"""The open project's notes go to the main brain only when the request is about the project (live 2026-11-01: with
Garden open, "what's the capital of Australia?" got "...I *do* know the cup's wall thickness is exactly 2mm")."""
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from systems.chat import Chat
from systems.projects import ProjectManager

NOTES = """Parts List and Sizes

Part 1: Cup Body
- Type: Cylinder
- Height: 50mm
- Wall Thickness: 2mm
"""


def manager():
    root = Path(tempfile.mkdtemp())
    pm = ProjectManager(data_root=root, projects_root=root / "projects",
                        index_extensions=config.PROJECT_INDEX_EXTENSIONS, exclude_dirs=config.PROJECT_EXCLUDE_DIRS,
                        max_file_bytes=config.PROJECT_FILE_MAX_BYTES, context_char_budget=config.PROJECT_CONTEXT_CHAR_BUDGET,
                        retrieval_chunks=config.PROJECT_RETRIEVAL_CHUNKS, reindex_interval_seconds=0, auto_git=False,
                        git_name="t", git_email="t@example.invalid", export_root_override="", export_include_git=False,
                        export_create_zip=False)
    pm.create_project("Garden")
    pm.write_workspace_file("parts_notes.txt", NOTES)
    pm.index_project(force=True)
    return pm


def chat(pm, profile=None):
    c = Chat.__new__(Chat)
    c.active_profile_name = profile or config.MAIN_MODEL_PROFILE
    c.chat_history = []
    c.conversation_summary = ""
    c._older_memory_context = lambda q: ""
    c._selected_image_paths = []
    c.project_manager = pm
    c.task_ledger = types.SimpleNamespace(prompt_context=lambda **kw: "")
    c._effective_context = lambda: 8192
    return c


class AboutProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pm = manager()

    def test_questions_about_something_else(self):
        for said in ("What's the capital of Australia?", "Why is the sky blue?", "Tell me a joke.", "what's 12 times 12",
                     "explain how a bearing works", "Who wrote Pride and Prejudice?"):
            self.assertFalse(self.pm.about_project(said), said)

    def test_questions_about_the_project(self):
        for said in ("How thick is the cup wall?", "how tall is the cup", "what's in my parts notes",
                     "what's going on in the garden"):
            self.assertTrue(self.pm.about_project(said), said)

    def test_the_memory_files_headings_do_not_count(self):
        self.assertFalse(self.pm.about_project("known issues with pending important requirements"))

    def test_no_project_open(self):
        pm = manager()
        pm._active_slug = None
        self.assertFalse(pm.about_project("how thick is the cup wall"))


class PromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pm = manager()

    def has_notes(self, c, said):
        c.chat_history = c.chat_history + [{"role": "user", "content": said}]
        return any("Active project context" in str(m.get("content")) for m in c._ollama_messages(said))

    def test_your_real_question_gets_no_notes(self):
        self.assertFalse(self.has_notes(chat(self.pm), "what's the capital of Australia?"))

    def test_a_project_question_gets_them(self):
        self.assertTrue(self.has_notes(chat(self.pm), "How thick is the cup wall?"))

    def test_saying_project_or_files_gets_them(self):
        for said in ("what project are we on", "what's in the readme", "read me my notes"):
            self.assertTrue(self.has_notes(chat(self.pm), said), said)

    def test_a_follow_up_carries_on(self):
        c = chat(self.pm)
        self.assertTrue(self.has_notes(c, "How thick is the cup wall?"))
        self.assertTrue(self.has_notes(c, "and how tall is it?"))

    def test_it_alone_does_not_bring_them_back(self):
        c = chat(self.pm)
        self.assertFalse(self.has_notes(c, "what's the capital of Australia?"))
        self.assertFalse(self.has_notes(c, "how big is it?"))

    def test_a_new_subject_drops_them(self):
        c = chat(self.pm)
        self.assertTrue(self.has_notes(c, "How thick is the cup wall?"))
        self.assertFalse(self.has_notes(c, "who painted the Mona Lisa?"))

    def test_specialists_always_get_them(self):
        for profile in config.MODEL_PROFILES:
            if profile != config.MAIN_MODEL_PROFILE:
                self.assertTrue(self.has_notes(chat(self.pm, profile), "why is the sky blue?"), profile)


class ClosedProjectTests(unittest.TestCase):
    """Nothing from a project reaches him unless it's open, and only the owner opens one (2026-11-01)."""

    def test_a_question_never_opens_a_project(self):
        self.assertFalse(config.AUTO_PROJECT_CLASSIFICATION)
        pm = manager()
        pm.close_project()
        c = chat(pm)
        self.assertIsNone(c._classify_project_for_request("how's the garden cup"))
        self.assertTrue(not any("Active project" in str(m.get("content"))
                                for m in c._ollama_messages("how thick is the cup wall")))

    def store(self):
        from systems.persistence import MemoryStore
        s = MemoryStore(Path(tempfile.mkdtemp()) / "m.sqlite3")
        session = s.start_session()
        for text, slug in (("the zebra cup is green", "garden"), ("my zebra coat is warm", None)):
            s.add_message(session_id=session, role="user", content=text, project_slug=slug, model_profile="normal",
                          model_name="m")
        return s

    def test_long_term_memory_hides_closed_projects(self):
        found = [m["content"] for m in self.store().search_messages("zebra", closed_projects_hidden=True)]
        self.assertEqual(found, ["my zebra coat is warm"])

    def test_the_open_project_and_plain_talk_are_both_found(self):
        found = {m["content"] for m in self.store().search_messages("zebra", active_project_slug="garden",
                                                                    closed_projects_hidden=True)}
        self.assertEqual(found, {"the zebra cup is green", "my zebra coat is warm"})

    def test_bmo_asks_for_it(self):
        self.assertIn("closed_projects_hidden=True", (ROOT / "systems" / "chat.py").read_text())

    def test_closing_clears_the_conversation_he_reads(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        start = src.index("project_before = self.project_manager.active_slug")
        self.assertIn("self.chat_history, self.chat_history_ids = [], []", src[start:start + 400])

    def test_plain_chat_is_not_filed_under_the_project(self):
        c = chat(manager())
        self.assertEqual(c._project_for_message("user", "How thick is the cup wall?"), c.project_manager.active_slug)
        self.assertEqual(c._project_for_message("assistant", "2 mm."), c.project_manager.active_slug)
        self.assertIsNone(c._project_for_message("user", "who painted the Mona Lisa?"))
        self.assertIsNone(c._project_for_message("assistant", "Leonardo."))

    def test_starting_up_closes_the_project(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        start = src.index("self._load_models()\n\n            # Sleeping or restarting closes the project")
        self.assertIn("self.project_manager.close_project()", src[start:start + 500])
        self.assertLess(start, src.index("self._warm_runtime_prompt()\n            except Exception:"))
        self.assertNotIn("Restored active project", src)

    def test_no_project_files_nothing(self):
        pm = manager()
        pm.close_project()
        self.assertIsNone(chat(pm)._project_for_message("user", "how thick is the cup wall"))


if __name__ == "__main__":
    unittest.main()
