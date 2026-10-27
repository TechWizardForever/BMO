# BMO

BMO from Adventure Time, living on your computer. You say "Hey BMO" and he talks back. Everything runs on your
own machine: no cloud, no account, and nothing you say ever leaves the PC.

This is a fan project. It's free and non-commercial, and it has nothing to do with Cartoon Network or anyone
behind Adventure Time. BMO's name and look belong to them.

## What he does

Mostly he talks. His main brain is a 30B model running through [Ollama](https://ollama.com), and when something
needs more than chat he can switch to a coding brain, an engineering brain, or a vision brain that can look at
pictures and your screen. He asks before he switches.

He can also:

- open programs, click and type in them, change the volume and the text size. Anything that changes something
  gets asked first, and he won't type into password boxes
- make simple 3D-printable parts if you describe them, like "make a 20 millimeter cube with an 8 millimeter hole
  through the middle". More in [docs/3D_PARTS.md](docs/3D_PARTS.md)
- play GameCube games through Dolphin, with a controller or the keyboard. No games come with him. You bring your
  own, and he can import them off a USB stick
- play your movies and shows from the Videos folder and handle them by voice: "play Adventure Time season 2
  episode 3", "pause", "skip ahead 30 seconds", "next episode", "stop". A movie picks up where you stopped it,
  and a show carries on from the last episode you watched. Like the games, no videos come with him
- show your movies or games on his screen ("show my movies", "show my games"), so you can scroll through them
  and say "play number 3"
- sort the games and movies in your Downloads folder into the right places ("sort my downloads"), after you
  say yes
- set timers and reminders, which still go off when he's asleep
- remember things you tell him ("remember that my sister's birthday is May 3rd")
- keep projects, check the weather, back himself up to a USB stick, and a bunch more. Everything he understands
  is in [VOICE_COMMANDS.md](VOICE_COMMANDS.md), a list you can print and keep next to him

He was built with an old CRT in mind, so he has a moving face, a bit of static now and then, and the old Windows
screensavers when nobody's using him. He also updates himself from this repo, but only from releases I've signed.

## What you need

- Linux Mint 22.3 Xfce, 64-bit. Other Ubuntu-based systems might work, but I haven't tried them.
- 32 GB of RAM and a CPU with AVX2 (8 threads or more is better). Everything runs on the CPU, so no graphics
  card needed.
- Around 100 GB of free space. The brains alone are about 78 GB.
- A mic, speakers, and internet for the install.

## Installing

```bash
git clone https://github.com/TechWizardForever/BMO.git
cd BMO
./install.sh --check
./install.sh
```

`--check` only looks at your computer and tells you what's missing. It doesn't change anything. The real install
asks for your password for the system packages, then downloads the brains, Dolphin and the screensavers. It takes
a while. If it gets interrupted, just run it again and it carries on.

His voice is a fan-made Piper voice (63.5 MB). It comes from the
[voice-1 release](https://github.com/TechWizardForever/BMO/releases/tag/voice-1) and gets checked before it's
installed.

A ready-made USB installer is on the way.

## Things to try first

Say "Hey BMO" to start him. The very first start takes about a minute while his brain loads. Then get him set up
for your room and your screen:

- "Hey BMO, learn my voice". You say "Hey BMO" a few times and talk for ten seconds, so he wakes up for you and
  not for the TV.
- "Hey BMO, calibrate the screen". A test pattern comes up and you just talk to it: "smaller", "bigger", "move
  left", "the left side is cut off", "lower resolution", and "keep it" when it fits. Handy on a CRT or a TV
  that cuts off the edges.
- "Hey BMO, list screen resolutions", then "Hey BMO, test screen resolution 1280 by 720". He goes back on his
  own unless you say "Hey BMO, approve display changes".
- "Hey BMO, make the text bigger" or "Hey BMO, make the mouse pointer bigger" if things are hard to read from
  the couch.

Then some of the fun stuff:

- "Hey BMO, what can you do on this computer?"
- "Hey BMO, play Adventure Time season 1 episode 1" (once you've put some episodes in your Videos folder)
- "Hey BMO, set a timer for 10 minutes"
- "Hey BMO, make a 20 millimeter cube with an 8 millimeter hole through the middle"
- "Hey BMO, check for updates"
- "Hey BMO, go to sleep" puts him in standby, and "Hey BMO" wakes him back up

## For developers

`bash tests/run_all.sh` runs the tests. You don't need a mic or any of the models for them. Python package
versions are pinned in `installer/requirements.lock`, which `tools/make_lock.py` makes.

## License

BMO's code is GPL-3.0 (see [LICENSE](LICENSE)). Everything he's built on is listed with its license in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The "Hey BMO" wake-word detector is CC BY-NC-SA 4.0, so that
part is non-commercial only.
