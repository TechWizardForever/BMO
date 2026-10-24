"""Only one Supervisor at a time (untouched-systems check, 2026-10-19).

A second Supervisor used to delete the first one's socket and take over; with login autostart
fixed, START_BEMO_NOW.sh would have started a second one beside the standby one.
"""
import ast
import os
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))


def load(client):
    tree = ast.parse((ROOT / "supervisor.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_hand_over_to_running_supervisor")
    fake_module = types.ModuleType("systems.supervisor")
    fake_module.SupervisorClient = lambda path, timeout: client
    ns = {"config": types.SimpleNamespace(SUPERVISOR_SOCKET_PATH="/x.sock"), "time": __import__("time"),
          "LOG": types.SimpleNamespace(info=lambda *a, **k: None)}
    saved = sys.modules.get("systems.supervisor")
    sys.modules["systems.supervisor"] = fake_module
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "supervisor", "exec"), ns)
    return ns["_hand_over_to_running_supervisor"], saved


class FakeClient:
    def __init__(self, up, running=False):
        self.up, self.running, self.started = up, running, []

    def available(self):
        return self.up

    def status(self):
        return {"ok": True, "bemo_running": self.running}

    def start_bemo(self, reason):
        self.started.append(reason)
        return {"ok": True}


class OneSupervisorTests(unittest.TestCase):
    def run_case(self, client, standby):
        hand_over, saved = load(client)
        try:
            return hand_over(standby=standby)
        finally:
            if saved is not None:
                sys.modules["systems.supervisor"] = saved
            else:
                sys.modules.pop("systems.supervisor", None)

    def test_no_supervisor_running_starts_normally(self):
        self.assertFalse(self.run_case(FakeClient(up=False), standby=False))

    def test_start_bemo_now_asks_the_running_one(self):
        client = FakeClient(up=True)
        self.assertTrue(self.run_case(client, standby=False))
        self.assertEqual(client.started, ["second-start"])

    def test_no_second_start_when_bemo_is_already_up(self):
        client = FakeClient(up=True, running=True)
        self.assertTrue(self.run_case(client, standby=False))
        self.assertEqual(client.started, [])

    def test_a_second_standby_start_waits_then_takes_over(self):
        # Exiting made systemd restart the service every 3 s forever (review 2026-10-19).
        answers = iter([True, True, True, False])  # up at first; gone after two waits
        client = FakeClient(up=True)
        client.available = lambda: next(answers)
        hand_over, saved = load(client)
        waits = []
        try:
            self.assertFalse(hand_over(standby=True, wait=waits.append))  # then it starts normally
        finally:
            if saved is not None:
                sys.modules["systems.supervisor"] = saved
            else:
                sys.modules.pop("systems.supervisor", None)
        self.assertEqual(client.started, [])
        self.assertEqual(len(waits), 3)

    def test_called_before_the_socket_is_taken(self):
        src = (ROOT / "supervisor.py").read_text()
        body = src[src.index("def run_supervised"):]
        self.assertLess(body.index("_hand_over_to_running_supervisor("), body.index("service.start()"))


if __name__ == "__main__":
    unittest.main()
