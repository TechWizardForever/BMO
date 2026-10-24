"""Project voice commands (project test, 2026-10-23): "create project garden" said twice made "garden-2",
and the name was saved as "Garden." with Whisper's full stop."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from systems.projects import ProjectManager


def branch(chat, start_marker):
    """The branch of _handle_local_command that starts at start_marker, as a function of the spoken words."""
    import ast
    import re
    import textwrap
    source = (ROOT / "systems" / "chat.py").read_text()
    start = source.index(start_marker)
    end = source.index("            return True", start) + len("            return True")
    body = textwrap.dedent(source[start:end])
    code = ("def branch(self, command):\n    normalized = self._normalize_command_text(command)\n"
            + textwrap.indent(body, "    ") + "\n    return False\n")
    from systems.chat import State
    from loguru import logger
    namespace = {"re": re, "State": State, "logger": logger}
    exec(compile(ast.parse(code), "branch", "exec"), namespace)
    return lambda said: namespace["branch"](chat, said)


class CreateProjectTests(unittest.TestCase):
    def setUp(self):
        from systems.chat import Chat
        root = Path(tempfile.mkdtemp())
        self.pm = ProjectManager(
            data_root=root, projects_root=root / "projects", index_extensions=config.PROJECT_INDEX_EXTENSIONS,
            exclude_dirs=config.PROJECT_EXCLUDE_DIRS, max_file_bytes=config.PROJECT_FILE_MAX_BYTES,
            context_char_budget=config.PROJECT_CONTEXT_CHAR_BUDGET, retrieval_chunks=config.PROJECT_RETRIEVAL_CHUNKS,
            reindex_interval_seconds=0, auto_git=False)
        self.chat = Chat.__new__(Chat)
        self.chat.project_manager = self.pm
        self.spoken = []
        self.chat.speak = self.spoken.append
        self.chat._archive_local_exchange = lambda command, response: None

    def test_said_twice_opens_the_first(self):
        handler = self.handler()
        handler("Create Project Garden.")
        handler("Create Project Garden.")
        self.assertEqual([p["slug"] for p in self.pm.list_projects()], ["garden"])
        self.assertEqual(self.pm.list_projects()[0]["name"], "Garden")
        self.assertEqual(self.spoken, ["Created and opened Garden.", "Garden is already a project, so I opened it."])
        self.assertEqual(self.pm.active_slug, "garden")

    def handler(self, start_marker='        create_match = re.match(r"^(?:create|new|make) project'):
        """One branch of _handle_local_command, run on its own."""
        return branch(self.chat, start_marker)

    def test_whats_the_current_project_is_bmos_own_answer(self):
        # Live 2026-10-24 it went to Engineering and took 1 min 40 s.
        ask = self.handler('        if normalized in {\n            "what project is active"')
        self.pm.create_project("Garden")
        for said in ("What's the current project?", "what project is open", "current project"):
            self.assertTrue(ask(said), said)
            self.assertEqual(self.spoken[-1], "The active project is Garden.")

    def test_bmo_reads_the_saved_notes_himself(self):
        # 2026-10-23: Coding answered "what is the cup for?" with a guess; BMO reads the notes word for word.
        ask = self.handler('        if normalized in {\n            "what do you remember about this project"')
        ask("what do you remember about this project")
        self.assertEqual(self.spoken[-1], "No project is open. Say Hey BMO, open project, and its name.")
        self.pm.create_project("Garden")
        ask("project notes")
        self.assertEqual(self.spoken[-1], "I don't have any notes for Garden yet. Say Hey BMO, remember for this "
                                          "project, and then the note.")
        self.pm.append_project_note("the cup is a planter for basil.")
        self.pm.append_project_note("print it in green PLA")
        ask("what do you remember about this project")
        self.assertEqual(self.spoken[-1], "For Garden, I remember: the cup is a planter for basil; print it in green PLA.")



class OpenProjectTests(unittest.TestCase):
    """project test, 2026-10-23: the project closed itself, and Coding's notes landed in ~/BMO-code."""
    def setUp(self):
        from systems.chat import Chat

        class FakeChat(Chat):
            active_profile = None
        root = Path(tempfile.mkdtemp())
        self.pm = ProjectManager(
            data_root=root, projects_root=root / "projects", index_extensions=config.PROJECT_INDEX_EXTENSIONS,
            exclude_dirs=config.PROJECT_EXCLUDE_DIRS, max_file_bytes=config.PROJECT_FILE_MAX_BYTES,
            context_char_budget=config.PROJECT_CONTEXT_CHAR_BUDGET, retrieval_chunks=config.PROJECT_RETRIEVAL_CHUNKS,
            reindex_interval_seconds=0, auto_git=False)
        self.pm.create_project("Garden")
        self.chat = FakeChat.__new__(FakeChat)
        self.chat.project_manager = self.pm
        self.chat.active_profile = {"project_tools": True}
        self.chat.active_profile_name = "coding"
        self.workspace = self.pm.active_workspace

    def test_the_project_stays_open_for_any_question(self):
        for question in ("what is the cup for?", "what's the weather like?", "tell me a joke"):
            self.assertEqual(self.chat._classify_project_for_request(question), "garden", question)
        self.pm.close_project()
        self.assertIsNone(self.chat._classify_project_for_request("what is the cup for?"))

    def test_codings_notes_go_into_the_project(self):
        code_folder = Path(config.CODE_OUTPUT_DIR).expanduser()
        said = self.chat._execute_tool("computer_control", {"action": "write_text", "args": {
            "path": str(code_folder / "Garden" / "bmo-test-notes.txt"), "content": "Cup: 60 x 60 x 50 mm"}})
        self.assertIn("in the Garden project", said)
        self.assertEqual((self.workspace / "bmo-test-notes.txt").read_text(), "Cup: 60 x 60 x 50 mm")
        self.assertFalse((code_folder / "Garden" / "bmo-test-notes.txt").exists())
        # Again: the first is kept, the new one saved beside it.
        self.chat._execute_tool("write_text", {"path": "bmo-test-notes.txt", "content": "second"})
        self.assertEqual((self.workspace / "bmo-test-notes.txt").read_text(), "Cup: 60 x 60 x 50 mm")
        self.assertEqual((self.workspace / "bmo-test-notes_2.txt").read_text(), "second")
        # "Replace nothing" in a file that isn't there yet writes it.
        self.chat._execute_tool("replace_project_text", {"path": "parts.txt", "old_text": "", "new_text": "cup"})
        self.assertEqual((self.workspace / "parts.txt").read_text(), "cup")

    def test_a_place_the_owner_named_is_left_alone(self):
        self.assertIsNone(self.chat._project_write_path(str(Path.home() / "Documents" / "notes.txt")))
        self.assertEqual(self.chat._project_write_path("/tmp/notes.txt"), "notes.txt")

    def test_the_saved_line_names_the_project(self):
        import json
        from systems.chat import Chat
        made = {"stl_path": str(self.workspace / "models" / "cup.stl"), "size_mm": [60, 60, 50], "through_holes": 0}
        self.assertIn("in the garden project's models folder", Chat._part_saved_line(json.dumps(made)))



class MainReadsProjectTests(unittest.TestCase):
    """2026-10-23: Read-only: yes."""
    def setUp(self):
        from systems.chat import Chat

        class FakeChat(Chat):
            active_profile = None
        root = Path(tempfile.mkdtemp())
        self.pm = ProjectManager(
            data_root=root, projects_root=root / "projects", index_extensions=config.PROJECT_INDEX_EXTENSIONS,
            exclude_dirs=config.PROJECT_EXCLUDE_DIRS, max_file_bytes=config.PROJECT_FILE_MAX_BYTES,
            context_char_budget=config.PROJECT_CONTEXT_CHAR_BUDGET, retrieval_chunks=config.PROJECT_RETRIEVAL_CHUNKS,
            reindex_interval_seconds=0, auto_git=False)
        self.pm.create_project("Garden")
        (self.pm.active_workspace / "parts_notes.txt").write_text("Cup: 60 x 60 x 50 mm, 2 mm walls\n")
        self.chat = FakeChat.__new__(FakeChat)
        self.chat.project_manager = self.pm
        self.chat.active_profile = {}  # Main: no project-work rights

    def test_main_is_offered_list_and_read_for_file_questions(self):
        from systems.context_policy import tool_names_for_request
        for question in ("what is in the notes file in this project?", "read me parts_notes.txt",
                         "what files are in the project?"):
            names = tool_names_for_request(question, "normal", True)
            self.assertTrue({"list_project_files", "read_project_file"} <= names, question)
            self.assertFalse(names & {"write_project_file", "replace_project_text", "run_project_check"}, question)
        self.assertFalse({"read_project_file"} & tool_names_for_request("what's the weather like?", "normal", True))
        self.assertFalse({"read_project_file"} & tool_names_for_request("what's in the notes file?", "normal", False))

    def test_main_reads_but_never_writes(self):
        self.assertIn("parts_notes.txt", self.chat._execute_tool("list_project_files", {}))
        self.assertIn("Cup: 60 x 60 x 50 mm", self.chat._execute_tool("read_project_file", {"path": "parts_notes.txt"}))
        for name, arguments in (("write_project_file", {"path": "x.txt", "content": "x"}),
                                ("replace_project_text", {"path": "parts_notes.txt", "old_text": "60", "new_text": "70"}),
                                ("write_text", {"path": "x.txt", "content": "x"}),
                                ("run_project_check", {"kind": "tests"})):
            self.assertTrue(self.chat._execute_tool(name, arguments).startswith("Not done:"), name)
        self.assertEqual((self.pm.active_workspace / "parts_notes.txt").read_text(), "Cup: 60 x 60 x 50 mm, 2 mm walls\n")
        self.assertFalse((self.pm.active_workspace / "x.txt").exists())


if __name__ == "__main__":
    unittest.main()
