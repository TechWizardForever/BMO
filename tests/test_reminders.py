"""Timers and reminders (2026-10-24): read by BMO, kept by the Supervisor, said when due, awake or asleep."""
from datetime import datetime
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems import reminders as r

NOW = datetime(2026, 10, 24, 14, 10)  # a Saturday afternoon


def due(text):
    found = r.parse(text, NOW)
    return datetime.fromtimestamp(found["due"]).strftime("%a %H:%M")


class ParseTests(unittest.TestCase):
    def test_timers(self):
        for said, seconds, name in (("Set a timer for 10 minutes.", 600, ""), ("set a pasta timer for 8 minutes", 480, "pasta"),
                                    ("set a 5 minute timer", 300, ""), ("timer for 30 seconds", 30, ""),
                                    ("set a timer for an hour and a half", 5400, ""), ("set a timer for half an hour", 1800, ""),
                                    ("set a timer for 1 hour and 20 minutes", 4800, ""),
                                    ("can you set a timer for ten minutes please", 600, "")):
            self.assertEqual(r.parse(said, NOW), {"action": "add", "kind": r.TIMER, "seconds": seconds, "name": name}, said)

    def test_reminders(self):
        self.assertEqual(due("Remind me in 20 minutes to check the oven."), "Sat 14:30")
        self.assertEqual(due("remind me at 5 p.m. to water the basil"), "Sat 17:00")
        self.assertEqual(due("remind me to call my mom tomorrow at 9"), "Sun 09:00")
        self.assertEqual(due("remind me at 5:30 that the pizza is in"), "Sat 17:30")
        self.assertEqual(due("remind me tonight at 8 to take my pills"), "Sat 20:00")
        self.assertEqual(due("remind me at 2 to stretch"), "Sun 14:00")  # 1-6 without am/pm: the afternoon
        self.assertEqual(due("remind me at 9 to read"), "Sat 21:00")
        self.assertEqual(due("remind me at noon to eat lunch"), "Sun 12:00")
        found = r.parse("remind me at 5:30 that the pizza is in", NOW)
        self.assertEqual((found["task"], found["connector"]), ("the pizza is in", "that"))

    def test_questions_and_cancelling(self):
        self.assertEqual(r.parse("how much time is left?", NOW), {"action": "list", "kind": r.TIMER})
        self.assertEqual(r.parse("what reminders do I have?", NOW), {"action": "list", "kind": r.REMINDER})
        self.assertEqual(r.parse("cancel all timers", NOW)["all"], True)
        self.assertEqual(r.parse("cancel the reminder to water the basil", NOW)["task"], "water the basil")
        self.assertEqual(r.parse("cancel the pasta timer", NOW)["name"], "pasta")

    def test_what_isnt_a_full_request(self):
        for said in ("set a timer", "remind me to water the basil", "what time is it", "set the table for dinner",
                     "remind me what we talked about", "set a timer for 3 days"):
            self.assertIsNone(r.parse(said, NOW), said)


class KeeperTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW.timestamp()
        self.said = []
        self.keeper = r.ReminderKeeper(Path(tempfile.mkdtemp()) / "reminders.json", say=self.said.append,
                                       clock=lambda: self.now)

    def ask(self, text):
        asked = r.parse(text, datetime.fromtimestamp(self.now))
        return self.keeper.handle({"op": asked.pop("action"), **asked})["say"]

    def test_a_timer_goes_off_once(self):
        self.assertEqual(self.ask("set a timer for 10 minutes"), "Timer set for 10 minutes.")
        self.now += 599
        self.assertEqual(self.keeper.check_once(), [])
        self.assertEqual(self.ask("how much time is left?"), "The 10 minute timer has 1 second left.")
        self.now += 1
        self.assertEqual(self.keeper.check_once(), ["Ding ding! Your 10 minute timer is done."])
        self.assertEqual(self.keeper.check_once(), [])
        self.assertEqual(self.said, ["Ding ding! Your 10 minute timer is done."])
        self.assertEqual(r.timer_name({"seconds": 4800}), "timer for 1 hour and 20 minutes")
        self.assertEqual(r.timer_name({"seconds": 60}), "1 minute timer")

    def test_reminders_say_it_back_in_second_person(self):
        self.assertEqual(self.ask("remind me at 5 pm to call my mom"), "Okay, at 5 PM I'll remind you to call your mom.")
        self.assertEqual(self.ask("remind me in 20 minutes that the oven is on"),
                         "Okay, in 20 minutes I'll remind you that the oven is on.")
        self.assertEqual(self.ask("what reminders do I have?"),
                         "At 2:30 PM: the oven is on; at 5 PM: call your mom.")
        self.now = datetime(2026, 10, 24, 14, 30).timestamp()
        self.assertEqual(self.keeper.check_once(), ["Reminder: the oven is on."])
        self.now = datetime(2026, 10, 24, 17, 0).timestamp()
        self.assertEqual(self.keeper.check_once(), ["Reminder: call your mom."])

    def test_cancelling(self):
        self.ask("set a pasta timer for 8 minutes")
        self.ask("set a timer for 20 minutes")
        self.assertEqual(self.ask("cancel the pasta timer"), "Cancelled the pasta timer.")
        self.assertEqual(self.ask("cancel all timers"), "Cancelled the timer.")
        self.assertEqual(self.ask("cancel the timer"), "There's no timer like that running.")
        self.ask("remind me at 5 pm to water the basil")
        self.assertEqual(self.ask("cancel the reminder to water the basil"), "Cancelled the reminder to water the basil.")

    def test_while_the_pc_was_off(self):
        self.ask("set a timer for 10 minutes")
        self.ask("remind me at 5 pm to water the basil")
        self.now = datetime(2026, 10, 24, 18, 0).timestamp()  # back on an hour after both were due
        self.assertEqual(self.keeper.check_once(), ["While I was off, you had a reminder at 5 PM: water the basil."])
        self.assertEqual(self.keeper.book.items(), [])

    def test_kept_across_restarts_and_private(self):
        self.ask("set a timer for 10 minutes")
        again = r.ReminderKeeper(self.keeper.book.path, say=self.said.append, clock=lambda: self.now)
        self.assertEqual(len(again.book.items()), 1)
        self.assertEqual(oct(self.keeper.book.path.stat().st_mode & 0o777), "0o600")


class WiringTests(unittest.TestCase):
    def test_bmo_and_the_supervisor(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn("if self._handle_reminder_command(command):", chat)
        supervisor = (ROOT / "supervisor.py").read_text()
        self.assertIn('if action == "reminders":', supervisor)
        self.assertIn("service.reminders = ReminderKeeper(config.REMINDERS_PATH)", supervisor)

    def test_a_brain_cant_claim_a_timer(self):
        from systems.grounding import fabricated_live_claim
        self.assertEqual(fabricated_live_claim("Timer set for 10 minutes!"), "untimed")
        self.assertEqual(fabricated_live_claim("I have set your alarm."), "untimed")
        self.assertIsNone(fabricated_live_claim("I can set timers if you ask."))

    def test_nothing_of_a_reminder_is_logged(self):
        source = (ROOT / "systems" / "reminders.py").read_text()
        for line in source.splitlines():
            if "self.log." in line:
                self.assertNotIn("task", line, line)


if __name__ == "__main__":
    unittest.main()
