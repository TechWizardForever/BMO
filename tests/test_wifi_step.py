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
    def __init__(self, connects=((0, ""),), devices="enp1s0:ethernet\nwlp3s0:wifi\n", applet=True,
                 links="ethernet:connected:Wired connection 1\nwifi:connected:HomeNet\nloopback:connected (externally):lo\n"):
        self.connects, self.devices, self.applet, self.calls = list(connects), devices, applet, []
        self.links = links

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        out, code, err = "", 0, ""
        if argv[0] == "pgrep":
            code = 0 if self.applet else 1
        elif argv[-1] == "device" and "DEVICE,TYPE" in argv:
            out = self.devices
        elif argv[-1] == "device" and "TYPE,STATE,CONNECTION" in argv:
            out = self.links
        elif argv[-1] == "device":
            out = "wlp3s0:connected\n"
        elif "list" in argv:
            out = LIST
        elif "connect" in argv:
            code, err = self.connects.pop(0) if self.connects else (0, "")
        return subprocess.CompletedProcess(argv, code, out, err)


class StepIO:
    """BMO's side of the internet step: keys from the install screen; online() answers from a list (the last
    answer repeats)."""
    def __init__(self, nmcli, keys=(), online=(False,), online_after=True):
        self.wifi = wf.Wifi(run=nmcli, which=lambda name: "/usr/bin/" + name, popen=lambda *a, **k: None)
        self.said, self._online, self.online_after, self.clock = [], list(online), online_after, 0.0
        self.view = fb.SetupView(blocking=False)
        self.view.open()
        for key in keys:
            self.view.press(key)

    def say(self, text):
        self.said.append(text)

    def online(self):
        return self._online.pop(0) if len(self._online) > 1 else self._online[0]

    def wait_until_online(self, seconds):
        return self.online_after

    def now(self):
        self.clock += 1
        return self.clock


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
    def test_already_online_needs_nothing_and_says_which_link(self):
        io = StepIO(FakeNmcli(), online=(True,))
        outcome = step().run(io)
        self.assertEqual(outcome.result, {"online": True, "how": "already", "link": "cable"})  # the cable wins
        self.assertEqual(fb.describe("internet", outcome), "cable")
        self.assertEqual(io.said, [])
        wifi_only = StepIO(FakeNmcli(links="ethernet:unavailable:\nwifi:connected:Cafe\\:Guest\n"), online=(True,))
        self.assertEqual(fb.describe("internet", step().run(wifi_only)), "Cafe:Guest")

    def test_wifi_picked_with_the_arrow_keys_then_the_password_window(self):
        io = StepIO(FakeNmcli(), keys=["down", "enter"])
        outcome = step().run(io)
        self.assertEqual((outcome.status, outcome.result["ssid"]), (DONE, "Neighbour 5G"))
        self.assertIn("pick your Wi-Fi on the screen", io.said[0])
        self.assertTrue(any("Type it with the keyboard" in s for s in io.said))
        self.assertEqual(io.said[-1], "I'm online, through Neighbour 5G.")
        self.assertEqual(io.view.snapshot()["choices"], [])  # the list goes away once it's done

    def test_a_number_key_picks_that_network(self):
        io = StepIO(FakeNmcli(), keys=["3"])
        outcome = step().run(io)
        self.assertEqual(outcome.result["ssid"], "Cafe:Guest")
        self.assertTrue(any(s == "Connecting to Cafe:Guest." for s in io.said))  # an open network: no password

    def test_the_networks_are_on_the_screen_until_a_cable_goes_in(self):
        io = StepIO(FakeNmcli(), keys=["down"], online=(False, False, False, True))
        shown = []
        original = io.view.show_choices
        io.view.show_choices = lambda names, selected=0: (shown.append((list(names), selected)),
                                                          original(names, selected))
        self.assertEqual(step().run(io).result["how"], "connected")
        self.assertIn((["HomeNet", "Neighbour 5G", "Cafe:Guest"], 1), shown)  # the second one picked

    def test_a_wrong_password_gets_another_try(self):
        nmcli = FakeNmcli(connects=[(4, "Error: Connection activation failed: Secrets were required, but not provided."),
                                    (0, "")])
        io = StepIO(nmcli, keys=["1", "1"])
        outcome = step().run(io)
        self.assertEqual(outcome.status, DONE)
        self.assertTrue(any("wasn't right. Pick it again to have another go." in s for s in io.said))

    def test_it_waits_for_a_cable_and_carries_on_by_itself(self):
        io = StepIO(FakeNmcli(devices="enp1s0:ethernet\n"), online=(False, False, False, True))
        outcome = step().run(io)
        self.assertEqual(outcome.result, {"online": True, "how": "cable"})
        self.assertIn("network cable", io.said[0])

    def test_losing_the_connection_says_so_and_waits(self):
        io = StepIO(FakeNmcli(), online=(False, False, True))
        outcome = step().wait(io, lost=True)
        self.assertEqual(outcome.status, DONE)
        self.assertEqual(io.said, ["I lost the internet. I'll carry on as soon as it's back.", "I'm online again."])

    def test_practice_runs_the_wifi_part_on_a_wired_pc(self):
        io = StepIO(FakeNmcli(), keys=["down", "enter"], online=(True,))
        outcome = step(practice=True).run(io)
        self.assertEqual(outcome.result["ssid"], "Neighbour 5G")
        self.assertFalse(any("need the internet" in s for s in io.said))

    def test_runner_passes_the_rehearsal_option(self):
        import tempfile
        state = fb.FirstBootState(Path(tempfile.mkdtemp()) / "fb.json")
        state.begin(("internet",), {"internet": {"practice_wifi": True}})
        io = StepIO(FakeNmcli(), keys=["1"], online=(True,))
        fb.Setup(state, io).run("before_listening")
        self.assertEqual(state.steps()[0]["result"]["ssid"], "HomeNet")


if __name__ == "__main__":
    unittest.main()
