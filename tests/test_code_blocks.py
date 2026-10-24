"""Coding writes code blocks; BMO saves them (roadmap item, after the 2026-10-18 snake-game failure)."""
import ast
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
from systems.code_blocks import alternative_name, extract_files, name_from_request, safe_relative, wants_files

F = "`" * 3
SNAKE = f"Here's the game.\n\nFile: snake.py\n{F}python\nimport pygame\nprint('snake')\n{F}\nRun it with python snake.py."


class ExtractTests(unittest.TestCase):
    def test_naming_styles(self):
        def names(text, request=""):
            return [f.path for f in extract_files(text, request)]
        self.assertEqual(names(SNAKE), ["snake.py"])
        self.assertEqual(names(f"**`src/app.js`**:\n{F}javascript\nconsole.log(1)\n{F}\n"), ["src/app.js"])
        self.assertEqual(names(f"{F}python title=tools/run.py\nprint(2)\n{F}\n"), ["tools/run.py"])
        self.assertEqual(names(f"{F}python\n# game.py\nprint(3)\n{F}\n"), ["game.py"])
        program = f"{F}python\nimport random\n\n\ndef main():\n    print(4)\n\nmain()\n{F}\n"
        self.assertEqual(names(program, "write a python program for a snake game"), ["snake_game.py"])
        self.assertEqual(names(program, "explain this code"), [])  # not asked to write a program
        self.assertEqual(names(f"Use this: {F}python\nx = 1\n{F}"), [])  # a snippet, not a file

    def test_live_answer_with_junk_after_the_closing_fence(self):
        # Live 2026-10-19: Coding wrote "File: snake_game.py", the block, then "```<tool_call>";
        # nothing was saved because the closing fence had text after it.
        text = (ROOT / "tests" / "fixtures" / "coding" / "snake-answer-2026-10-19.txt").read_text()
        files = extract_files(text, "write a Python program for a snake game")
        self.assertEqual([f.path for f in files], ["snake_game.py"])
        self.assertTrue(files[0].content.startswith("import pygame"))
        self.assertTrue(files[0].content.rstrip().endswith("main()"))
        from systems.code_blocks import STRAY_TAGS
        self.assertNotIn("<tool_call>", STRAY_TAGS.sub("", text))

    def test_unsafe_names_are_never_saved(self):
        for bad in ("../../etc/passwd", "/home/bemo/x.py", "~/x.py", ".bashrc", "a/../../b.py", "C:x.py"):
            self.assertIsNone(safe_relative(bad), bad)
        self.assertEqual(safe_relative("src/app.js"), "src/app.js")

    def test_helpers(self):
        self.assertEqual(name_from_request("Hey BMO, write me a python script that renames my photos", "python"),
                         "renames_photos.py")
        taken = {"snake.py", "snake_2.py"}
        self.assertEqual(alternative_name("snake.py", lambda p: p in taken), "snake_3.py")


class SaveTests(unittest.TestCase):
    def setUp(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        body = [n for n in chat.body if isinstance(n, ast.FunctionDef)
                and n.name in ("_save_code_blocks", "_remember_saved_code", "_offer_just_accepted")
                or isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in ("_AGREEMENT", "_OFFER_TO_WRITE")]
        self.dir = Path(tempfile.mkdtemp()) / "BMO-code"
        cfg = types.SimpleNamespace(CODE_SAVE_BLOCKS=True, CODE_OUTPUT_DIR=self.dir)
        ns = {"config": cfg, "os": os, "Path": Path, "time": __import__("time"), "re": re,
              "logger": types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None,
                                              exception=lambda *a, **k: None)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=body, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        self.Mini = ns["Mini"]

    def chat(self, answers=(), project=None):
        c = self.Mini()
        c.project_manager = project or types.SimpleNamespace(active_slug=None)
        c.questions, answers = [], list(answers)
        c._confirm_by_voice = lambda q, **kw: c.questions.append(q) or (answers.pop(0) if answers else False)
        return c

    def test_saved_to_the_code_folder_and_said(self):
        c = self.chat()
        note = c._save_code_blocks("write a snake game", SNAKE)
        self.assertEqual(note, "I saved snake.py in the BMO-code folder in your home folder.")
        self.assertEqual((self.dir / "snake.py").read_text(), "import pygame\nprint('snake')\n")
        # Remembered with the full path, for "where's the file?" and for Coding's next turn.
        self.assertEqual(c._last_saved_code["names"], ["snake.py"])
        self.assertEqual(c._last_saved_code["paths"], [str((self.dir / "snake.py").resolve())])

    def test_a_question_never_saves_or_asks(self):
        # Headless bench 2026-10-19: on "where's the file for the game?" Coding wrote the game
        # again, and BMO asked to replace the file in answer to a question.
        self.dir.mkdir(parents=True)
        (self.dir / "snake.py").write_text("old\n")
        c = self.chat(answers=[True])
        self.assertEqual(c._save_code_blocks("where's the file for the game?", SNAKE), "")
        self.assertEqual(c.questions, [])
        self.assertEqual((self.dir / "snake.py").read_text(), "old\n")

    def test_which_requests_save(self):
        for asked in ("write a Python program for a snake game", "Bright a Python Program for a Snake Game.",
                      "add a pause key", "make it faster", "can you fix the bug in it", "Hey BMO, write me a script"):
            self.assertTrue(wants_files(asked), asked)
        from systems.context_policy import coding_work_request
        # Live 2026-10-19: the misheard request was answered as chat, so nothing was written.
        self.assertTrue(coding_work_request("Bright a Python Program for a Snake Game."))
        self.assertFalse(coding_work_request("is python a good language for a game"))
        for asked in ("where's the file for the game?", "did you save it?", "what did you call the file",
                      "Hey BMO, where is the file?", "tell me a joke", "is it done"):
            self.assertFalse(wants_files(asked), asked)

    def test_a_new_program_never_replaces_an_earlier_one(self):
        # Live 2026-10-19: asking for the snake game again asked to overwrite the earlier game.
        self.dir.mkdir(parents=True)
        (self.dir / "snake.py").write_text("old\n")
        c = self.chat(answers=[True])
        note = c._save_code_blocks("write a snake game", SNAKE)
        self.assertEqual(c.questions, [])
        self.assertEqual((self.dir / "snake.py").read_text(), "old\n")
        self.assertTrue((self.dir / "snake_2.py").exists())
        self.assertIn("snake_2.py", note)

    def test_existing_file_asks_and_keeps_both_without_a_yes(self):
        self.dir.mkdir(parents=True)
        (self.dir / "snake.py").write_text("old\n")
        c = self.chat(answers=[False])
        note = c._save_code_blocks("fix the snake game", SNAKE)
        self.assertEqual(c.questions, ["snake.py already exists. Replace it? Say Hey BMO, yes, or just say no."])
        self.assertEqual((self.dir / "snake.py").read_text(), "old\n")
        self.assertTrue((self.dir / "snake_2.py").exists())
        self.assertIn("snake_2.py", note)
        c = self.chat(answers=[True])
        c._save_code_blocks("fix the snake game", SNAKE)
        self.assertEqual((self.dir / "snake.py").read_text(), "import pygame\nprint('snake')\n")

    def test_open_project_gets_the_file_and_a_snapshot(self):
        written, snaps = [], []
        root = Path(tempfile.mkdtemp())
        pm = types.SimpleNamespace(
            active_slug="snake", _safe_workspace_path=lambda rel, must_exist=False: root / rel,
            write_workspace_file=lambda rel, content: written.append((rel, content)),
            index_project=lambda force=False: None, snapshot=lambda msg: snaps.append(msg))
        note = self.chat(project=pm)._save_code_blocks("write a snake game", SNAKE)
        self.assertEqual(note, "I saved snake.py in the project.")
        self.assertEqual(written, [("snake.py", "import pygame\nprint('snake')\n")])
        self.assertEqual(snaps, ["Bemo: saved snake.py"])

    def test_nothing_to_save(self):
        self.assertEqual(self.chat()._save_code_blocks("what can you code?", "I can write Python, games and tools."), "")


class WriteDestinationTests(unittest.TestCase):
    def test_temporary_writes_go_to_the_code_folder(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        body = [n for n in chat.body if isinstance(n, ast.FunctionDef) and n.name == "_code_file_destination"]
        ns = {"config": types.SimpleNamespace(CODE_SAVE_BLOCKS=True, CODE_OUTPUT_DIR="/home/u/BMO-code"),
              "Path": Path, "logger": types.SimpleNamespace(info=lambda *a, **k: None)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=body, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        dest = ns["Mini"]()._code_file_destination
        # Headless bench 2026-10-19: Coding wrote the game to /tmp/snake_game.py.
        self.assertEqual(dest({"path": "/tmp/snake_game.py", "content": "x"})["path"], "/home/u/BMO-code/snake_game.py")
        self.assertEqual(dest({"path": "snake_game.py"})["path"], "/home/u/BMO-code/snake_game.py")
        # Live 2026-10-19: aimed at ~/snake_game.py, where an older test's file was.
        self.assertEqual(dest({"path": str(Path.home() / "snake_game.py")})["path"], "/home/u/BMO-code/snake_game.py")
        for kept in (str(Path.home() / "Desktop/game.py"), "/srv/u/game.py", "/tmp/.hidden"):
            self.assertEqual(dest({"path": kept})["path"], kept)

    def test_later_actions_follow_the_redirect_and_open_needs_asking(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        keep = [n for n in chat.body if isinstance(n, ast.FunctionDef)
                and n.name in ("_code_file_destination", "_follow_code_redirects")
                or isinstance(n, ast.Assign) and n.targets[0].id == "_OPEN_ASKED"]
        ns = {"config": types.SimpleNamespace(CODE_SAVE_BLOCKS=True, CODE_OUTPUT_DIR="/home/u/BMO-code"),
              "Path": Path, "re": re, "logger": types.SimpleNamespace(info=lambda *a, **k: None)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        c = ns["Mini"]()
        old = str(Path.home() / "snake_game.py")
        c._code_file_destination({"path": old, "content": "x"})
        # Live 2026-10-19: Coding then opened its old path.
        self.assertEqual(c._follow_code_redirects({"path": old})["path"], "/home/u/BMO-code/snake_game.py")
        self.assertEqual(c._follow_code_redirects({"argv": ["python3", old]})["argv"],
                         ["python3", "/home/u/BMO-code/snake_game.py"])
        self.assertEqual(c._follow_code_redirects({"path": "/etc/hosts"})["path"], "/etc/hosts")
        self.assertFalse(c._OPEN_ASKED.search("write a Python program for a snake game"))
        self.assertTrue(c._OPEN_ASKED.search("write a snake game and run it"))
        self.assertTrue(c._OPEN_ASKED.search("open the snake game"))
        self.assertIn('if action in {"open", "launch"} and not self._OPEN_ASKED.search(', src)

    def test_tool_writes_keep_earlier_programs(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        keep = [n for n in chat.body if isinstance(n, ast.FunctionDef) and n.name == "_keep_earlier_program"]
        ns = {"Path": Path, "logger": types.SimpleNamespace(info=lambda *a, **k: None)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        folder = Path(tempfile.mkdtemp())
        (folder / "snake_game.py").write_text("old\n")
        c = ns["Mini"]()
        c._agent_request = "write a Python program for a snake game."
        self.assertEqual(c._keep_earlier_program({"path": str(folder / "snake_game.py")})["path"],
                         str(folder / "snake_game_2.py"))
        c._agent_request = "fix the bug in the snake game"
        self.assertEqual(c._keep_earlier_program({"path": str(folder / "snake_game.py")})["path"],
                         str(folder / "snake_game.py"))  # the Supervisor then asks before replacing

    def test_a_correction_in_the_same_turn_updates_the_same_new_file(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        keep = [n for n in chat.body if isinstance(n, ast.FunctionDef)
                and n.name in ("_keep_earlier_program", "_code_file_destination")]
        code_dir = Path(tempfile.mkdtemp())
        ns = {"Path": Path, "logger": types.SimpleNamespace(info=lambda *a, **k: None),
              "config": types.SimpleNamespace(CODE_SAVE_BLOCKS=True, CODE_OUTPUT_DIR=str(code_dir))}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        (code_dir / "snake_game.py").write_text("old\n")
        c = ns["Mini"]()
        c._agent_request, c._code_path_redirects = "write a snake game", {}
        aimed = str(Path.home() / "snake_game.py")

        def write(path):
            target = c._keep_earlier_program(c._code_file_destination({"path": path}))["path"]
            Path(target).write_text("new\n")
            return Path(target).name
        self.assertEqual(write(aimed), "snake_game_2.py")
        self.assertEqual(write(aimed), "snake_game_2.py")  # the correction: same file, not _3
        self.assertEqual(sorted(p.name for p in code_dir.iterdir()), ["snake_game.py", "snake_game_2.py"])

    def test_a_write_finished_after_the_owners_ok_is_remembered_and_said(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        body = [n for n in chat.body if isinstance(n, ast.FunctionDef)
                and n.name in ("_note_written_file", "_remember_saved_code")]
        code_dir = Path.home() / "BMO-code"
        ns = {"config": types.SimpleNamespace(CODE_OUTPUT_DIR=str(code_dir)), "Path": Path, "time": __import__("time")}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=body, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        c = ns["Mini"]()
        # The Supervisor's answer to "confirm computer action" for a write_text (live 2026-10-19).
        said = c._note_written_file({"ok": True, "path": str(code_dir / "snake_game.py"), "bytes": 4664,
                                     "confirmed_action": "Overwrite existing file"})
        self.assertEqual(said, "Done. I saved snake_game.py in the BMO-code folder in your home folder.")
        self.assertEqual(c._last_saved_code["paths"], [str(code_dir / "snake_game.py")])
        self.assertEqual(c._note_written_file({"ok": True, "returncode": 0, "stdout": ""}), "")  # not a write
        self.assertEqual(c._note_written_file({"ok": False, "error": "x"}), "")

    def test_tool_saves_are_said_first(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        # Live 2026-10-19: a computer-action save went unsaid; the note now leads the spoken summary.
        self.assertIn('self._turn_written_note = f"I saved {written.name} {folder}."', src)
        self.assertIn("if not saved_note and getattr(self, \"_turn_written_note\", \"\"):", src)
        self.assertIn('self._turn_written_note = ""', src)

    def test_both_confirm_handlers_use_it(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        self.assertEqual(src.count("(self._note_written_file(result) or \"Confirmed."), 2)


class ApprovalWordingTests(unittest.TestCase):
    def test_long_commands_are_said_by_program_name(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        fn = next(n for n in chat.body if isinstance(n, ast.FunctionDef) and n.name == "_speakable_action_summary")
        fn.decorator_list = []
        ns = {"re": re, "Path": Path}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), "f", "exec"), ns)
        say = ns["_speakable_action_summary"]
        self.assertEqual(say("Run interpreter/script code: python3 -c 'import pygame; print(1)'"),
                         "Run interpreter/script code: a python3 command")
        self.assertEqual(say("Run interpreter/script code: python3 snake_game.py"),
                         "Run interpreter/script code: python3 snake_game.py")
        self.assertEqual(say("Overwrite existing file: /home/u/BMO-code/snake_game.py"),
                         "Overwrite existing file: /home/u/BMO-code/snake_game.py")


class SwitchWordingTests(unittest.TestCase):
    def test_switched_to_coding_is_a_switch(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        keep = [n for n in chat.body if isinstance(n, ast.FunctionDef)
                and n.name in ("_extract_switch_target", "_match_profile_name", "_normalize_command_text")]
        ns = {"config": config, "re": re, "logger": types.SimpleNamespace(info=lambda *a, **k: None)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        c = ns["Mini"]()
        # Live 2026-10-19: "switched to coding." went to Main twice, which played along.
        for heard, target in (("switched to coding.", "coding"), ("switching to main", config.MAIN_MODEL_PROFILE),
                              ("switch to code aim", "coding"), ("switch to coding brain.", "coding")):
            self.assertEqual(c._match_profile_name(c._extract_switch_target(heard)), target, heard)
        self.assertIsNone(c._extract_switch_target("I switched to decaf coffee"))
        # Live 2026-10-19 (second run): name in front, a sentence around it, "Maine".
        for heard, target in (("He Bemo switched to coding.", "coding"),
                              ("you need to switch to coding brain.", "coding"),
                              ("Bemo switched to Maine.", config.MAIN_MODEL_PROFILE),
                              ("can we go back to main please", config.MAIN_MODEL_PROFILE)):
            self.assertEqual(c._match_profile_name(c._extract_switch_target(heard)), target, heard)
        for chat_line in ("I'm going to go to main street", "I'm going to go to coding class"):
            self.assertIsNone(c._extract_switch_target(chat_line), chat_line)

    def test_naming_the_asked_brain_answers_the_switch_question(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        body = src[src.index("    def _confirm_by_voice"):src.index("    def _switch_profile")]
        self.assertIn('if tag == "MODEL_APPROVAL" and answer not in affirmative:', body)
        self.assertIn("if named == target:", body)

    def test_plain_yes_only_for_brain_switches(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        body = src[src.index("    def _confirm_by_voice"):src.index("    def _switch_profile")]
        self.assertIn('if not woke and tag != "MODEL_APPROVAL":', body)
        self.assertIn('"Would you like me to switch to {label}? Say yes or no."', src)
        # Replacing a file still needs the wake prefix.
        self.assertIn('tag="CODE_OVERWRITE"', src)

    def test_he_bemo_is_a_wake_prefix(self):
        from voice_commands import VoiceSessionGate
        gate = VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES)
        self.assertEqual(gate.parse("He Bemo switched to coding."), (True, "switched to coding."))

    def test_program_requests_go_to_coding(self):
        from systems.context_policy import direct_specialist_route
        self.assertEqual(direct_specialist_route("can you program a Python game that is like a snake game?", "normal"),
                         "HEAVY_CODING")
        for chat_line in ("make me a game plan for the weekend", "what's a good game to play",
                          "tell me about python snakes"):
            self.assertIsNone(direct_specialist_route(chat_line, "normal"), chat_line)

    def test_spoken_switch_to_main_needs_no_yes_but_specialists_and_offers_do(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        # Only the spoken switch command passes ask=False, and only for Main.
        self.assertEqual(src.count("ask=profile_name != config.MAIN_MODEL_PROFILE"), 1)
        self.assertIn("if ask or profile_name != config.MAIN_MODEL_PROFILE:", src)
        # The offer after delegated specialist work still asks.
        offer = src[src.index("    def _return_to_main_after_specialist"):]
        self.assertIn("self._switch_profile(main, preload=True)", offer[:2500])

    def test_no_screen_claims_in_face_mode(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        self.assertNotIn('"All done. The details are on my screen."', src)
        self.assertNotIn("it's on my screen.", src)


class ReviewFixTests(unittest.TestCase):
    """Code review of v1.28.46-57 (2026-10-19)."""

    def mini(self, names, extra=None):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        keep = [n for n in chat.body if isinstance(n, ast.FunctionDef) and n.name in names
                or isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in names]
        ns = {"re": re, "Path": Path, "logger": types.SimpleNamespace(warning=lambda *a, **k: None), **(extra or {})}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        return ns["Mini"]()

    def test_yes_to_an_offer_saves_what_follows(self):
        c = self.mini(("_offer_just_accepted", "_AGREEMENT", "_OFFER_TO_WRITE"))
        c.chat_history = [{"role": "user", "content": "I'd like a little game"},
                          {"role": "assistant", "content": "Fun! Want me to write a Python snake game?"},
                          {"role": "user", "content": "yes please"}]
        offer = c._offer_just_accepted("yes please")
        self.assertEqual(offer, "write a Python snake game")
        self.assertTrue(wants_files(offer))
        self.assertEqual(name_from_request(offer, "python"), "snake_game.py")
        self.assertEqual(c._offer_just_accepted("tell me a joke"), "")
        c.chat_history[1]["content"] = "Here you go."
        self.assertEqual(c._offer_just_accepted("yes"), "")

    def test_only_the_newest_screenshots_are_kept(self):
        c = self.mini(("_prune_screenshots",))
        folder = Path(tempfile.mkdtemp())
        for i in range(25):
            f = folder / f"bemo-screen-{i:03d}.png"
            f.write_bytes(b"x")
            os.utime(f, (1000 + i, 1000 + i))
        (folder / "owner-photo.png").write_bytes(b"keep")
        c._prune_screenshots(folder)
        left = sorted(p.name for p in folder.iterdir())
        self.assertEqual(len(left), 21)
        self.assertIn("owner-photo.png", left)
        self.assertIn("bemo-screen-024.png", left)
        self.assertNotIn("bemo-screen-000.png", left)

    def test_stories_about_games_stay_with_main(self):
        from systems.context_policy import direct_specialist_route
        self.assertIsNone(direct_specialist_route("write me a story about a game", "normal"))
        self.assertEqual(direct_specialist_route("write me a python game", "normal"), "HEAVY_CODING")


class SavedFileQuestionTests(unittest.TestCase):
    def test_where_is_the_file_is_answered_locally(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        node = next(n for n in chat.body if isinstance(n, ast.Assign) and n.targets[0].id == "_SAVED_FILE_QUESTION")
        q = eval(compile(ast.Expression(node.value), "q", "eval"), {"re": re})

        def norm(text):  # Chat._normalize_command_text
            return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9.\- ]+", " ", text.lower().strip())).strip()
        for asked in ("where's the file for the game?", "Where did you save it?", "what's the file called?",
                      "did you save it", "where can I find the program",
                      "Demon where's the file for the game?",  # misheard "Hey BMO" (live 2026-10-19)
                      "what is the latest snake game file call?",  # live 2026-10-19
                      "what's the name of the snake game file"):
            self.assertTrue(q.search(norm(asked)), asked)
        for other in ("where is the calculator", "where are we", "write a snake game", "save the file",
                      "what is the best game engine", "what game should I play",
                      "where is it raining", "where can I buy it"):  # review 2026-10-19
            self.assertFalse(q.search(norm(other)), other)

    def test_coding_does_not_say_work_it_already_did(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        node = next(n for n in chat.body if isinstance(n, ast.Assign) and n.targets[0].id == "_UNSPOKEN_SENTENCE")
        q = eval(compile(ast.Expression(node.value), "q", "eval"), {"re": re})
        for said in ("Let me create the Snake game implementation directly.", "I'll write a complete Python program.",
                     "Save it as snake_game.py in your home directory.", "Copy all the text above."):
            self.assertTrue(q.search(said), said)
        self.assertFalse(q.search("Here's a Python snake game using pygame."))

    def test_spoken_summary_of_a_saved_program(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        keep = [n for n in chat.body if isinstance(n, ast.FunctionDef) and n.name == "_coding_spoken_text"
                or isinstance(n, ast.Assign) and n.targets[0].id == "_UNSPOKEN_SENTENCE"]
        from systems.turn_speech import complete_sentences
        ns = {"re": re, "complete_sentences": complete_sentences,
              "config": types.SimpleNamespace(CODING_SPOKEN_MAX_WORDS=40)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        mini = ns["Mini"]()
        mini._normalize_spaces = lambda t: re.sub(r"\s+", " ", t).strip()
        # Shape of the headless-bench answer, 2026-10-19 (spoken then: "File: snake_game.py This
        # Snake game implementation includes: To run this game, ...").
        answer = (f"Let me create the Snake game.\n\nFile: snake_game.py\n{F}python\nprint(1)\n{F}\n\n"
                  "This Snake game implementation includes:\n- Arrow keys\n- Score\n\n"
                  "To run this game, you'll need to have Pygame installed.\n\nTo use this code:\n"
                  "1. Copy all the text above\n2. Save it as snake_game.py\n")
        self.assertEqual(mini._coding_spoken_text(answer), "To run this game, you'll need to have Pygame installed.")


class PromptTests(unittest.TestCase):
    def test_coding_is_told_to_use_code_blocks(self):
        prompt = config.MODEL_PROFILES["coding"]["system_prompt"]
        self.assertIn("File: relative/path.ext", prompt)
        self.assertIn("Never put a whole file inside a tool call", prompt)


if __name__ == "__main__":
    unittest.main()
