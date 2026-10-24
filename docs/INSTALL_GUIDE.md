# Putting BMO on a new computer

BMO comes as a USB installer: Linux Mint 22.3 with BMO already inside. You install it like ordinary Mint;
BMO then sets himself up by talking to you.

## What you need

- **A computer** with a 64-bit Intel or AMD processor, **32 GB of memory** and **at least 100 GB
  of free disk space**. Installing **erases everything on the disk you pick**.
- **A USB stick of 16 GB or more.** Writing the installer erases everything on the stick, so use a spare one.
- **A microphone** (a USB clip-on microphone works well) and **speakers**.
- **Internet**, by cable or Wi-Fi. BMO downloads his brains on the first day, about 80 GB.
- A keyboard and mouse for the install itself. After that BMO is used by voice.

## 1. Make the USB stick

On a computer with Linux Mint: right-click the file `bmo-…-mint-22.3-xfce.iso`, choose
**Make bootable USB stick**, pick the stick and click **Write**.
On Windows or a Mac, use [balenaEtcher](https://etcher.balena.io): **Flash from file**, pick the stick,
**Flash**.

## 2. Install

1. Plug the stick into the new computer and switch it on. Press its boot-menu key straight away (often
   **F12**, **F11**, **F8** or **Esc**; the screen usually says which) and pick the USB stick.
2. Choose **Start Linux Mint**. A desktop appears; this is Mint running from the stick.
3. Double-click **Install Linux Mint** on the desktop and follow it:
   - pick your language and keyboard;
   - **connect to Wi-Fi here if you use Wi-Fi** (you type the password; BMO never sees it);
   - choose **Erase disk and install Linux Mint** (this deletes what is on that disk);
   - pick your time zone, then type your name, a computer name, a user name and a password.
4. When it says the installation is finished, click **Restart Now**, and take the stick out when asked.

## 3. BMO's first start

The computer logs in by itself and BMO starts. Answer him out loud:

1. **Microphone:** he asks you to say **"Hey BMO, hello."**
2. **Your voice:** he says **"Go"** a few times; say "Hey BMO" each time, then talk normally for ten seconds.
   This helps him wake for you and not for the TV.
3. **Screen:** "Can you see all of my face?" Say **yes** or **no**; on no he helps you fit it.
4. **Internet:** if you didn't connect during the install, he lists the Wi-Fi networks; say the number of
   yours and type its password in the window that opens.
5. **Brains:** he downloads his main brain first (about 18 GB) and can chat once it's done. Then the game
   player (Dolphin) and the screensavers, then the other brains in the background. Ask
   **"Hey BMO, how are the downloads?"** any time.

If the computer is switched off part-way, BMO carries on where he stopped at the next start.

## Afterwards

- Say **"Hey BMO"** before anything you want him to do. **"Hey BMO, what can you do on this computer?"**
  tells you what works here; every command is in [VOICE_COMMANDS.md](../VOICE_COMMANDS.md).
- **"Hey BMO, check for updates"** looks for a new version of BMO; to install it, say **"Hey BMO, yes."**
  Each version is signed, and one that doesn't start properly is rolled back.
