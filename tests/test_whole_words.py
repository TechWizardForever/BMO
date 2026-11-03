"""BMO never finds a command inside a longer word (owner 2026-11-03, after "show me Miss Congeniality" opened the space
station screen because "iss" is in "Miss"), and older commands keep their sentences.

The attack below takes every regular expression in BMO's code, writes a sentence for each word it listens for, and glues
letters onto that word. Nothing should still match. A new command with a missing word boundary fails here.
"""
import ast
import os
from pathlib import Path
import re
try:
    import re._parser as sre_parse
except ImportError:
    import sre_parse
import sys
import unittest

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import voice_commands
from systems import desktop_settings, games, gui_control, library, part_requests, personal_memory, projects, sky
from systems.chat import Chat
from systems.grounding import live_data_topic

# These read logs, the brain's code, file names or BMO's own words, never what was said.
NOT_SPEECH = {"code_blocks.py", "engineering.py", "hardware_report.py", "saver_guard.py", "sort_downloads.py"}
ALLOWED = {
    "Right now in": "rewrites BMO's own saved weather answer",
    "That's from Open-Meteo": "rewrites BMO's own saved weather answer",
    "generat|creat|mak": "reads the brain's answer, for word stems",
    "and a half": "only runs after re.fullmatch has checked the whole sentence",
    "h[a-z']{0,4}": "any short first word before his name is Whisper's Hey: a whole word, on purpose",
    "[a-z]{0,2}[ei]+e*mo": "a clipped Hey BMO when answering his question: a whole word, on purpose",
}


def _literal(node, known):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left, known), _literal(node.right, known)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        text = ""
        for value in node.values:
            if isinstance(value, ast.Constant):
                text += value.value
            elif isinstance(value, ast.FormattedValue) and isinstance(value.value, ast.Name) and value.value.id in known:
                text += known[value.value.id]
            else:
                text += "(?:zzq)"
        return text
    if isinstance(node, ast.Name):
        return known.get(node.id)
    return None


def patterns_in(path):
    """(line, how it's called, pattern) for each regular expression in a file."""
    tree = ast.parse(path.read_text())
    known = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = _literal(node.value, known)
            if value is not None:
                known[node.targets[0].id] = value
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "re" and node.args
                and node.func.attr in ("compile", "search", "match", "fullmatch", "sub", "findall", "finditer", "split")):
            text = _literal(node.args[0], known)
            if text and re.search("[a-zA-Z]{2}", text):
                found.append((node.lineno, node.func.attr, text))
    return found


def _sentences(parsed):
    """Sentences for a parsed pattern, each alternative word in at least one of them."""
    parts = []
    for op, value in parsed:
        kind = str(op)
        if kind == "LITERAL":
            parts.append([chr(value)])
        elif kind == "NOT_LITERAL":
            parts.append(["q"])
        elif kind == "ANY":
            parts.append(["a"])
        elif kind == "IN":
            first = value[0]
            if str(first[0]) == "LITERAL":
                parts.append([chr(first[1])])
            elif str(first[0]) == "RANGE":
                parts.append([chr(first[1][0])])
            else:
                parts.append([" " if "SPACE" in str(first[1]) and "NOT" not in str(first[1]) else "a"])
        elif kind == "BRANCH":
            parts.append([text for branch in value[1] for text in _sentences(branch)][:300])
        elif kind == "SUBPATTERN":
            parts.append(_sentences(value[3]))
        elif kind in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
            low, _, inner = value
            if len(inner) == 1 and str(inner[0][0]) == "ANY":
                parts.append(["", " abc "])
            elif low == 0:
                parts.append([""] + _sentences(inner))
            else:
                parts.append([text * low for text in _sentences(inner)])
        else:
            parts.append([""])  # \b, ^, $ and lookarounds write nothing
    count = min(max([len(options) for options in parts] + [1]), 300)
    return ["".join(options[n % len(options)] for options in parts) for n in range(count)]


def fires_inside_a_word(pattern, how="search"):
    """The longer words a pattern still matches, such as "xiss" for r"iss\\b"."""
    compiled = re.compile(pattern, re.I)
    call = {"match": compiled.match, "fullmatch": compiled.fullmatch}.get(how, compiled.search)
    longer = []
    for sentence in set(_sentences(sre_parse.parse(pattern))):
        sentence = sentence.strip()
        if not sentence or not compiled.search(sentence):
            continue
        for word in re.finditer(r"[A-Za-z]{2,}", sentence):
            if word.group().lower() == "zzq":
                continue
            if compiled.search(sentence[:word.start()] + "zzzq" + sentence[word.end():]):
                continue  # this word isn't needed for the match at all
            for glued in ("x" + word.group(), word.group() + "x"):
                if call(sentence[:word.start()] + glued + sentence[word.end():]):
                    longer.append(glued)
                    break
    return longer


def speech_files():
    return sorted(ROOT.glob("systems/*.py")) + [ROOT / name for name in ("standby_wake.py", "voice_commands.py", "supervisor.py")]


class TheAttackWorks(unittest.TestCase):
    """A check that can't fail proves nothing."""

    def test_it_catches_the_bug_that_started_this(self):
        self.assertIn("xiss", [word.lower() for word in fires_inside_a_word(r"\bwhere\b.*(?:the iss|iss\b)")])
        self.assertTrue(fires_inside_a_word(r"\b(?:round|square) box"))
        self.assertTrue(fires_inside_a_word(r"(?:round|square) box\b"))
        self.assertTrue(fires_inside_a_word(r"^(?:how|why|what)", "match"))

    def test_it_passes_whole_words(self):
        for good in (r"\bwhere\b.*\b(?:the iss|iss)\b", r"\b(?:round|square) box\b", r"^(?:how|why|what)\b", r"^close$"):
            self.assertEqual(fires_inside_a_word(good), [], good)

    def test_it_reads_a_real_file(self):
        self.assertGreater(sum(len(patterns_in(path)) for path in speech_files()), 300)


class NeverInsideAWord(unittest.TestCase):
    def test_no_pattern_matches_inside_a_longer_word(self):
        bad = []
        for path in speech_files():
            if path.name in NOT_SPEECH:
                continue
            for line, how, pattern in patterns_in(path):
                if any(fragment in pattern for fragment in ALLOWED):
                    continue
                try:
                    longer = fires_inside_a_word(pattern, how)
                except re.error:
                    continue
                if longer:
                    bad.append(f"{path.name}:{line} still matches {sorted(set(longer))[:6]}")
        self.assertEqual(bad, [])

    def test_no_plain_text_search_of_what_was_said(self):
        """'"art" in said' finds it in "start". These names only ever hold what was said."""
        spoken = {"said", "normalized", "command", "heard", "request", "prompt", "gated_prompt", "utterance"}
        bad = []
        for path in speech_files():
            source = path.read_text()
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Compare) and isinstance(node.ops[0], (ast.In, ast.NotIn)):
                    right = node.comparators[0]
                    while isinstance(right, (ast.Call, ast.Attribute)):
                        right = right.func if isinstance(right, ast.Call) else right.value
                    if (isinstance(right, ast.Name) and right.id in spoken and isinstance(node.left, (ast.Constant, ast.Name))
                            and not isinstance(getattr(node.left, "value", ""), int)):
                        bad.append(f"{path.name}:{node.lineno} {ast.get_source_segment(source, node)}")
        self.assertEqual(bad, [])

    def test_real_words_with_a_command_inside_them(self):
        words = """miss swiss kiss bliss missouri mississippi issue tissue hiss aisle spacex spaceship spacious spacer
                   skyline skylight skydive skyrim moonlight moonshine moonstruck raiders readers leader weatherman
                   weathered forecaster eclipsed meteorite meteorology projector projection projectile showroom showers
                   howard howl whatever whoever iffy exam spam around grounded closet closer enclosure downtown upset
                   enoughs starting started smart departing earthy marshal jupiters saturnalia marsupial venusian mercurial
                   planetarium planetary starship stargate startup stardust nightly knight""".split()
        carriers = ("{}", "the {}", "show me the {}", "show me {}", "when is the {}", "where is the {}", "what is {}", "close the {}",
                    "turn off the {}", "my {}", "show my {}", "is there a {}", "any {}", "the next {}", "can i see {}", "play {}")
        for word in words:
            for carrier in carriers:
                said = carrier.format(word)
                self.assertIsNone(sky.request(said), said)
                self.assertNotEqual(live_data_topic(said), "weather", said)
                self.assertIsNone(library.open_request(said), said)
                self.assertNotIn((library.command(said) or ("play",))[0], ("close", "down", "up", "top", "bottom"), said)

    def test_a_note_can_start_with_a_longer_word(self):
        for said, note in (("remember Howard likes tea", "howard"), ("remember whatever I said", "whatever"),
                           ("remember Iffy is the dog", "iffy"), ("remember Whitney called me back", "whitney")):
            parsed = personal_memory.parse(said)
            self.assertIsNotNone(parsed, said)
            self.assertIn(note, str(parsed).lower(), said)
        for said in ("remember what I said", "remember how to do it", "remember if it works"):
            self.assertNotEqual((personal_memory.parse(said) or {}).get("action"), "add", said)

    def test_a_round_part_is_not_around_one(self):
        self.assertEqual(part_requests.read("make a round plate 20 mm across and 5 mm thick")[0], "cylinder")
        self.assertIsNone(part_requests.read("make an around plate 20 mm across and 5 mm thick"))

    def test_how_long_he_answers_is_not_read_inside_words(self):
        budget = Chat._main_response_token_budget
        self.assertEqual(budget("show me something nice")[1], "default")  # "show " has "how " in it
        self.assertEqual(budget("how does this work")[1], "careful")
        self.assertEqual(budget("tell me why")[1], "careful")
        self.assertEqual(budget("that's a brisk walk")[1], "default")  # "risk"
        self.assertEqual(budget("is that risky")[1], "careful")
        self.assertEqual(budget("explaining that again")[1], "careful")

    def test_a_game_or_window_or_project_named_inside_another_word(self):
        self.assertEqual(gui_control.resolve_window([{"title": "Smart Notes", "app": "smart"}], "art"), None)
        self.assertEqual(gui_control.resolve_window([{"title": "Art Studio", "app": "studio"}], "art")["title"], "Art Studio")
        self.assertFalse(projects._has_words("art", "start the timer"))
        self.assertTrue(projects._has_words("art", "open the art project"))
        self.assertTrue(projects._has_words("my-garden", "the-my-garden-project"))
        self.assertFalse(desktop_settings.parse_setting("stop hiddenite panel"))
        library_of_games = games.GameLibrary.__new__(games.GameLibrary)
        library_of_games.scan = lambda system=None: [games.GameEntry("Smart Racer", "gc", "/a.iso", "a.iso"),
                                                     games.GameEntry("Kart Heroes", "gc", "/b.iso", "b.iso")]
        self.assertIsNone(library_of_games.find("art")[0])
        self.assertEqual(library_of_games.find("kart heroes")[0].title, "Kart Heroes")


class OlderCommandsKeepTheirSentences(unittest.TestCase):
    def test_nothing_new_takes_a_listed_phrase(self):
        checked = 0
        for command in voice_commands.VOICE_COMMANDS:
            if command.category in ("Weather", "The sky") or command.syntax.startswith("Hey Bemo, show my projects"):
                continue
            for said in command.syntax.split(" / "):
                said = re.sub(r"^Hey Bemo,\s*", "", said).strip()
                said = re.sub(r"<[^>]+>", "garden", said)
                checked += 1
                self.assertIsNone(sky.request(said), said)
                self.assertNotEqual(live_data_topic(said), "weather", said)
        self.assertGreater(checked, 100)  # a check of nothing would pass too

    def test_the_list_only_promises_what_works(self):
        for said in ("this week's forecast", "next week's forecast", "forecast for the next three days", "seven day forecast",
                     "the forecast for Salem, Oregon", "what's the forecast for Salem", "weather in Paris"):
            self.assertEqual(live_data_topic(said), "weather", said)

    def test_an_answer_to_his_question_is_not_a_forecast(self):
        for said in ("next week", "this week", "tomorrow", "on Friday", "the next three days"):
            self.assertIsNone(live_data_topic(said), said)


if __name__ == "__main__":
    unittest.main()
