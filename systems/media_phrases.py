"""Many ways to say BMO's movie, show and game commands (owner 2026-10-28: "can we add a substantial amount of
similar phrases so I don't have to say just one exact one").

clean() turns what Whisper heard into the plain words the commands match on:
  - "Hey BMO" and Whisper's versions of it go ("Heed Bemo", "Hibemo", "He be my");
  - so do fillers and politeness: "um", "well", "okay so", "can you", "could you please", "I want to", "I'd like
    to", "let's", "go ahead and" at the start, "for me", "please", "right now" at the end;
  - as a second try, without one stray word in front ("Remonk show me my movies", live 2026-10-27): variants();
  - Whisper's usual mishearings are put right: "Clothes" for close, "post the game" and "close the cane" for close
    the game, "Say number one" for play number one, "paws" for pause.
A title still has to match something BMO has (the media, game and library commands check), so the broader verbs
("put on", "pull up", "open", "start") can't start a movie or game by accident.
"""
from __future__ import annotations

import re

_NAME = r"(?:bemo|beemo|bmo|be mo|bima|beema|beemow|be my|bemos)"
_WAKE = re.compile(rf"^(?:(?:\w{{1,6}})\s+)?{_NAME}\b\s*|^h[a-z]{{0,4}}(?:bemo|beemo|bmo)\b\s*")
_START_FILLER = re.compile(
    r"^(?:um|uh|er|erm|hmm|well|so|okay|ok|alright|all right|now|just|yeah|yes|oh|hey|and|then|please|actually|"
    r"maybe|right|alrighty|okay so|ok so)\s+")
_POLITE = re.compile(
    r"^(?:would you mind|do you mind|(?:can|could|would|will) you(?: please)?(?: just)?|(?:can|could|shall) we|"
    r"i want(?: you)? to|i wanna|i want|id like(?: you)? to|i would like(?: you)? to|i need(?: you)? to|you need to|"
    r"you can|lets|let us|go ahead and|how about we|we want to|we wanna|i feel like|im gonna|im going to|"
    r"i am going to|time to|try to|please|"
    r"(?:we|i|you) (?:will|shall|can|could|should|would|must|wanna|want to|gotta|got to|have to|need to|"
    r"are going to|am going to|are gonna|might as well)|well|ill|were gonna|were going to)\s+")
_END = re.compile(
    r"\s+(?:please|now|thanks|thank you|for me|for us|right now|real quick|if you can|if you could|okay|ok|"
    r"would you|will you|bemo|bmo|beemo)$")
# The first word as Whisper writes it -> what was said (live tests 2026-10-20 to 10-28).
_FIRST_WORD = {"clothes": "close", "cloze": "close", "clothe": "close", "closed": "close", "closes": "close",
               "claws": "close", "cloths": "close", "plays": "play", "played": "play", "playing": "play",
               "stops": "stop", "stopped": "stop", "stopping": "stop", "pauses": "pause", "paused": "pause",
               "paws": "pause", "skips": "skip", "skipped": "skip", "skipping": "skip", "rewinds": "rewind",
               "resumes": "resume", "restarts": "restart", "launches": "launch", "opens": "open",
               "starts": "start", "watches": "watch", "scrolls": "scroll", "shows": "show", "quits": "quit"}
# Words that start a command, so a stray word in front of one can go.
_COMMAND_START = {"play", "watch", "put", "throw", "turn", "pull", "bring", "load", "queue", "start", "restart",
                  "resume", "continue", "keep", "pause", "stop", "close", "quit", "exit", "skip", "rewind", "next",
                  "previous", "show", "open", "launch", "boot", "fire", "scroll", "number", "go", "fast", "jump",
                  "back", "sort", "list", "what", "hold", "wait", "pick", "choose"}
# Other words a command can start with: never taken for a stray one ("jump back half a minute").
_VERBS = {"jump", "move", "seek", "hang", "freeze", "give", "press", "hit", "kill", "end", "take", "begin", "carry",
          "replay", "run", "another", "let", "get", "unpause", "fire", "throw", "queue", "hide", "leave", "dismiss",
          "browse", "display", "check", "look", "select", "try", "ill", "give", "say", "pay", "enough", "nothing",
          "never", "forget", "none", "game", "more", "keep", "rewind", "forward", "up", "down"}
# A first word that changes the meaning: never dropped ("don't play it").
_KEEP_FIRST = {"dont", "do", "not", "never", "no", "why", "how", "what", "whats", "when", "where", "who", "is",
               "are", "can", "could", "would", "should", "did", "does", "will", "i", "we", "you", "it", "the",
               "play", "watch", "show", "stop", "close", "open", "start", "go", "turn", "put", "next", "skip"}


def _first_word(said: str) -> str:
    words = said.split()
    if words:
        if words[0] in ("say", "pay") and len(words) > 1 and words[1] == "number":
            words[0] = "play"  # "Say number one." (live 2026-10-27)
        words[0] = _FIRST_WORD.get(words[0], words[0])
    return " ".join(words)


def variants(text: str) -> list[str]:
    """clean(text), then, as a second try, the same without a stray first word ("Remonk show me my movies",
    "Buddy play Zelda") when the next word starts a command."""
    said = clean(text)
    words = said.split()
    out = [said]
    if len(words) > 2 and words[0] not in _KEEP_FIRST | _COMMAND_START | _VERBS \
            and (words[1] in _COMMAND_START or words[1] in _FIRST_WORD):
        out.append(_first_word(" ".join(words[1:])))
    return out


def first(text: str, parse):
    """The first thing parse() makes of variants(text), or None."""
    for said in variants(text):
        found = parse(said)
        if found:
            return found
    return None


def _words(text: str) -> str:
    text = str(text or "").lower().replace("’", "'").replace("'", "")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def clean(text: str) -> str:
    said = _words(text)
    previous = None
    while said != previous:  # wake leftovers, fillers and politeness in any order
        previous = said
        said = _WAKE.sub("", said, count=1)
        said = _START_FILLER.sub("", said)
        said = _POLITE.sub("", said)
        said = _END.sub("", said)
    said = _first_word(said)
    # "post the game!" (2026-10-20), "close the cane" (2026-10-22): the game said over the game's own sound
    said = re.sub(r"^(?:post|pause|close|quit|exit|stop|end)(?: the| this| my)? (?:cane|gain|came|gate|gang|gave|gay|"
                  r"games|gam)$", "close the game", said)
    said = re.sub(r"^post the game$", "close the game", said)
    return said


# --- Games ---------------------------------------------------------------------------------------------

_GAME_PLAY = re.compile(
    r"^(?P<verb>play|launch|start up|start|open up|open|boot up|boot|load up|load|fire up|run|put on|go play)\s+"
    r"(?:(?:the )?game |a game of |a round of |some )?(?P<what>.+?)"
    r"(?: (?:on|in) (?:the )?(?:gamecube|game cube|dolphin))?$")
_NOT_A_GAME = {"games", "game", "a game", "the game", "game library", "the game library", "gamecube", "game cube",
               "dolphin", "the games", "my games", "some games", "video games", "emulator", "the emulator",
               "library", "the library", "game library", "games library", "menu", "the menu"}


def game_request(text: str) -> tuple[str, bool] | None:
    """(name, sure) for "play Zelda", "let's play Smash Bros", "fire up Custom Robo", "launch the game Zelda".
    sure: said with play / launch, so a game was meant even if none matches; with the other verbs only a game BMO
    has counts (so "open Firefox" stays an app)."""
    def parse(said):
        found = _GAME_PLAY.match(said)
        if not found or found.group("what") in _NOT_A_GAME:
            return None
        return found.group("what"), found.group("verb") in ("play", "launch", "go play")
    return first(text, parse)


_CLOSE_GAME = re.compile(
    r"^(?:close|quit|exit|stop|end|turn off|shut off|leave|get out of|kill)(?: (?:the|this|my|current|that))? "
    r"(?:game|games|dolphin|gamecube|game cube)(?: again)?$|"
    r"^(?:stop|quit|done|finished|finish|im done|were done|i am done) (?:playing|with the game|gaming)$|"
    r"^(?:turn|shut) (?:the game|it) off$|^no more games?$|^game over$")
_CLOSE_WHILE_PLAYING = re.compile(
    r"^(?:close|quit|exit|stop|end|kill)(?: (?:it|this|that))?(?: again)?$|^(?:turn|shut) it off$|"
    r"^(?:im|were|i am|we are) done$|^(?:thats|that is) enough$|^enough$|^close it all$")


def close_game(text: str, game_on: bool = False) -> bool:
    """'close the game' / 'quit the game' / 'I'm done playing'; while a game is on also a plain 'close',
    'close again', 'quit', 'that's enough' (live 2026-10-22 and 10-28: those got "I'll answer that once the game
    or video is off")."""
    return bool(first(text, lambda said: _CLOSE_GAME.match(said) or (game_on and _CLOSE_WHILE_PLAYING.match(said))))


# --- Carrying on with what was on ----------------------------------------------------------------------

_CONTINUE = re.compile(
    r"^(?:(?:continue|keep|resume|go back to|get back to|carry on|finish|start) watching|"
    r"pick up where (?:i|we) left off|(?:play|put on|start) (?:what|whatever) (?:i|we) (?:was|were) watching|"
    r"(?:continue|resume|finish) (?:my|the|our) (?:movie|show|episode|video|film)|"
    r"(?:play|continue|resume) where (?:i|we) left off|where (?:was i|were we))$")


def continue_watching(text: str) -> bool:
    """'continue watching', 'pick up where I left off', 'resume my show': the last thing that was on."""
    return bool(first(text, _CONTINUE.match))
