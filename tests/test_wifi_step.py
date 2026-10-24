"""First boot's internet step (installer plan I3), with a fake nmcli: no network is touched."""
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems import first_boot as fb
from systems import wifi as wf
from systems.first_boot import DONE, FAILED

LIST = "HomeNet:82:WPA2\nCafe\\:Guest:40:\n:30:WPA2\nHomeNet:60:WPA2\nNeighbour 5G:55:WPA1 WPA2\n"


class FakeNmcli:
    """Answers nmcli like NetworkManager would; connect results come from a list."""
    def __init__(self, connects=((0, ""),), devices="enp1s0:ethernet\nwlp3s0:wifi\n", applet=True):
        self.connects, self.devices, self.applet, self.calls = list(connects), devices, applet, []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        out, code, err = "", 0, ""
        if argv[0] == "pgrep":
            code = 0 if self.applet else 1
        elif argv[-1] == "device" and "DEVICE,TYPE" in argv:
            out = self.devices
        elif argv[-1] == "device":
            out = "wlp3s0:connected\n"
        elif "list" in argv:
            out = LIST
        elif "connect" in argv:
            code, err = self.connects.pop(0) if self.connects else (0, "")
        return subprocess.CompletedProcess(argv, code, out, err)


class StepIO:
    def __init__(self, nmcli, answers=(), online=False, online_after=None):
        self.wifi = wf.Wifi(run=nmcli, which=lambda name: "/usr/bin/" + name, popen=lambda *a, **k: None)
        self.answers, self.said, self._online = list(answers), [], online
        self.online_after = online_after

    def say(self, text):
        self.said.append(text)

    def ask(self, question):
        self.said.append(question)
        return self.answers.pop(0) if self.answers else None

    def online(self):
        return self._online

    def wait_until_online(self, seconds):
        return True if self.online_after is None else self.online_after


def step(practice=False):
    s = fb.InternetStep()
    s.options = {"practice_wifi": True} if practice else {}
    return s


class WifiToolTests(unittest.TestCase):
    def test_networks_strongest_first_each_name_once(self):
        nets = wf.Wifi(run=FakeNmcli(), which=lambda n: "/x").networks()
        self.assertEqual([n.ssid for n in nets], ["HomeNet", "Neighbour 5G", "Cafe:Guest"])
        self.assertEqual(nets[0].signal, 82)
        self.assertFalse(nets[2].secured)

    def test_connect_never_puts_a_password_on_the_command_line(self):
        fake = FakeNmcli()
        ok, _ = wf.Wifi(run=fake, which=lambda n: "/x").connect("HomeNet")
        self.assertTrue(ok)
        connect = next(c for c in fake.calls if "connect" in c)
        self.assertEqual(connect[-4:], ["device", "wifi", "connect", "HomeNet"])
        self.assertNotIn("password", connect)
        self.assertNotIn("--ask", connect)

    def test_the_tray_is_started_when_it_is_not_running(self):
        started = []
        wifi = wf.Wifi(run=FakeNmcli(applet=False), which=lambda n: "/x", popen=lambda argv, **k: started.append(argv))
        wf.time.sleep, original = (lambda s: None), wf.time.sleep
        try:
            wifi.ensure_password_window()
        finally:
            wf.time.sleep = original
        self.assertEqual(started, [["nm-applet"]])

    def test_picking_a_network_by_number_or_name(self):
        nets = [wf.Network("HomeNet", 80, True), wf.Network("Home 2", 70, True), wf.Network("Cafe", 40, False)]
        self.assertEqual(wf.pick_network("number one", nets).ssid, "HomeNet")
        self.assertEqual(wf.pick_network("The third one.", nets).ssid, "Cafe")
        self.assertEqual(wf.pick_network("3", nets).ssid, "Cafe")
        self.assertEqual(wf.pick_network("Home 2", nets).ssid, "Home 2")  # a name, not "number two"
        self.assertEqual(wf.pick_network("home net", nets).ssid, "HomeNet")
        self.assertIsNone(wf.pick_network("number nine", nets))
        self.assertIsNone(wf.pick_network("what's the weather like today in town", nets))

    def test_reasons_are_said_simply(self):
        self.assertIn("password", wf.spoken_reason("Error: Connection activation failed: Secrets were required, but not provided."))
        self.assertEqual(wf.spoken_reason("weird"), "It didn't connect.")

    def test_online_check(self):
        def refuse(address, timeout):
            raise OSError
        self.assertFalse(wf.online(connect=refuse))
        self.assertTrue(wf.online(connect=lambda a, timeout: types.SimpleNamespace(close=lambda: None)))


class InternetStepTests(unittest.TestCase):
    def test_already_online_needs_nothing(self):
        io = StepIO(FakeNmcli(), online=True)
        self.assertEqual(step().run(io).result, {"online": True, "how": "already"})

    def test_wifi_number_then_the_password_window(self):
        io = StepIO(FakeNmcli(), answers=["number one"])
        outcome = step().run(io)
        self.assertEqual((outcome.status, outcome.result["ssid"]), (DONE, "HomeNet"))
        self.assertTrue(any("1: HomeNet. 2: Neighbour 5G. 3: Cafe:Guest" in s for s in io.said))
        self.assertTrue(any("Type it with the keyboard" in s for s in io.said))
        self.assertEqual(io.said[-1], "I'm online, through HomeNet.")

    def test_a_wrong_password_gets_another_try(self):
        nmcli = FakeNmcli(connects=[(4, "Error: Connection activation failed: Secrets were required, but not provided."),
                                    (0, "")])
        io = StepIO(nmcli, answers=["one", "one"])
        outcome = step().run(io)
        self.assertEqual(outcome.status, DONE)
        self.assertTrue(any("wasn't right. Let's try again." in s for s in io.said))

    def test_none_of_them_or_no_answer_waits_for_the_next_start(self):
        io = StepIO(FakeNmcli(), answers=["none of those"])
        self.assertEqual(step().run(io).status, FAILED)
        io = StepIO(FakeNmcli(), answers=[None, "banana"])
        self.assertEqual(step().run(io).result, {"reason": "no network chosen"})

    def test_no_wifi_adapter_asks_for_a_cable(self):
        io = StepIO(FakeNmcli(devices="enp1s0:ethernet\n"), online_after=True)
        self.assertEqual(step().run(io).result, {"online": True, "how": "cable"})
        self.assertIn("network cable", io.said[0])

    def test_practice_runs_the_wifi_part_on_a_wired_pc(self):
        io = StepIO(FakeNmcli(), answers=["two"], online=True)
        outcome = step(practice=True).run(io)
        self.assertEqual(outcome.result["ssid"], "Neighbour 5G")
        self.assertFalse(any("not connected" in s for s in io.said))

    def test_runner_passes_the_rehearsal_option(self):
        import tempfile
        state = fb.FirstBootState(Path(tempfile.mkdtemp()) / "fb.json")
        state.begin(("internet",), {"internet": {"practice_wifi": True}})
        io = StepIO(FakeNmcli(), answers=["one"], online=True)
        fb.FirstBoot(state, io).run("listening")
        self.assertEqual(state.steps()[0]["result"]["ssid"], "HomeNet")


if __name__ == "__main__":
    unittest.main()
