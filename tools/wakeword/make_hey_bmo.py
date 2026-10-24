#!/usr/bin/env python3
"""Train BMO's own "Hey BMO" wake-word detector (openWakeWord architecture), on this PC, on the CPU.

Stages (each can be rerun):
  generate  synthetic "Hey BMO" clips from Piper's 904-voice LibriTTS-R model, and near-misses that
            must NOT wake him ("hey demo", "hey Bob", a bare "BMO" ...), mixed with noise or the owner's
            recorded room sound, turned into openWakeWord features (16 x 96 per 1.28 s window)
  train     a small network against those plus 1.4 M "not the wake word" windows (ACAV100M features)
  export    ONNX for openWakeWord
  evaluate  false starts per hour on the 11 h validation set, and the owner's own recordings (kept out of
            training): 10 "Hey BMO"s and 37 room sounds

Files live in ~/BMO-models/wakeword (downloads approved on 2026-10-19). The negatives are
CC BY-NC-SA, so the model carries that licence (non-commercial, 2026-10-19).

Public mode (BMO_WAKEWORD_PUBLIC=1): nothing of the owner's goes
into training, neither the "Hey BMO" recordings nor the room recordings as background; they are used only
by `evaluate`, to measure how well the public model hears him. Separate folder and output:

    BMO_WAKEWORD_PUBLIC=1 .venv/bin/python tools/wakeword/make_hey_bmo.py generate speech train_selected export evaluate
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np

HOME = Path(os.path.expanduser("~/BMO-models/wakeword"))
PUBLIC = os.environ.get("BMO_WAKEWORD_PUBLIC") == "1"
WORK = HOME / ("work_public" if PUBLIC else "work")
MELSPEC = HOME / "models" / "melspectrogram.onnx"
EMBEDDING = HOME / "models" / "embedding_model.onnx"
VOICE = HOME / "voice" / "en_US-libritts_r-medium.onnx"
ACAV = HOME / "features" / "acav100m_first25pct.npy.part"
VALIDATION = HOME / "features" / "validation_set_features.npy"
OUTPUT = HOME / ("hey_bmo_public.onnx" if PUBLIC else "hey_bmo.onnx")
SAMPLES = sorted(glob.glob(os.path.expanduser("~/.bemo/wake-samples/*/")))
CLIP_SECONDS = 2.0
RATE = 16000

WAKE_TEXTS = ["Hey BMO.", "Hey BMO!", "Hey, BMO.", "Hey Bemo.", "Hey Beemo!", "Hey, Bemo!", "hey bmo",
              "Hey BMO?", "Hey Bee-mo."]
# Things that must not wake him: near-sounding phrases, the bare name (standby's rule), and
# everyday openers.
NEAR_MISSES = ["Hey demo.", "Hey Bob.", "Hey Bo.", "Be more.", "Hey beam.", "A demo.", "Hey Siri.", "Hey mo.",
               "Bingo!", "Hey buddy.", "Hey Ben.", "Hey Mom.", "Hey Leo.", "BMO.", "Bemo.", "Hey Google.",
               "Hey there.", "Remote.", "Hey, be more careful.", "Okay so.", "Hey you.", "Hey bud.", "They mow.",
               "Hey Emo.", "Hey Nemo.", "Hey Elmo.", "Hey Neo.", "Hey Rome.", "Gizmo.", "Hey Debo.", "Hey Beto."]


def features_model():
    from openwakeword.utils import AudioFeatures
    return AudioFeatures(melspec_model_path=str(MELSPEC), embedding_model_path=str(EMBEDDING),
                         inference_framework="onnx", ncpu=4)


def room_audio(*, for_training=True):
    """The owner's recorded room sounds (2026-10-19), as background for augmentation and as a test.
    Public mode: none for training (only for the test)."""
    if PUBLIC and for_training:
        return []
    clips = []
    for folder in SAMPLES:
        for path in sorted(glob.glob(folder + "room/*.wav")):
            clips.append(read_wav(path))
    return clips


def read_wav(path):
    with wave.open(path) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        rate = w.getframerate()
    if rate != RATE:
        from scipy.signal import resample_poly
        data = resample_poly(data, RATE, rate).astype(np.float32)
    return data


def place(speech, rng, backgrounds, *, at_end=True):
    """A 2 s clip ending shortly after the speech, with noise or room sound underneath."""
    n = int(CLIP_SECONDS * RATE)
    speech = speech[: n - 1600] * rng.uniform(0.3, 1.0) / max(1e-4, float(np.max(np.abs(speech))))
    clip = np.zeros(n, np.float32)
    tail = int(rng.uniform(0.05, 0.25) * RATE) if at_end else int(rng.uniform(0.05, 0.8) * RATE)
    start = max(0, n - tail - len(speech))
    clip[start:start + len(speech)] += speech
    if backgrounds and rng.random() < 0.6:
        bg = backgrounds[rng.integers(len(backgrounds))]
        if len(bg) < n:
            bg = np.tile(bg, int(np.ceil(n / max(1, len(bg)))))
        offset = rng.integers(0, max(1, len(bg) - n))
        clip += bg[offset:offset + n] * rng.uniform(0.05, 0.5)
    clip += rng.normal(0, rng.uniform(0.0005, 0.01), n).astype(np.float32)
    return np.clip(clip, -1, 1)


def synthesize(texts, count, rng, backgrounds, label):
    from piper import PiperVoice, SynthesisConfig
    from scipy.signal import resample_poly
    voice = PiperVoice.load(str(VOICE))
    clips, t0 = [], time.monotonic()
    for i in range(count):
        text = texts[rng.integers(len(texts))]
        config = SynthesisConfig(speaker_id=int(rng.integers(904)), length_scale=float(rng.uniform(0.75, 1.35)),
                                 noise_scale=float(rng.uniform(0.4, 0.9)), noise_w_scale=float(rng.uniform(0.5, 1.0)))
        audio = np.concatenate([np.frombuffer(c.audio_int16_bytes, dtype="<i2") for c in voice.synthesize(text, config)])
        audio = resample_poly(audio.astype(np.float32) / 32768.0, RATE, voice.config.sample_rate).astype(np.float32)
        clips.append(place(audio, rng, backgrounds))
        if (i + 1) % 500 == 0:
            print(f"  {label}: {i + 1}/{count} clips ({time.monotonic() - t0:.0f} s)", flush=True)
    return np.stack(clips)


def last_window(features):
    return features[:, -16:, :]  # the 1.28 s ending where the phrase ends


def generate(n_pos=4000, n_neg=4000):
    WORK.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(19)
    backgrounds = room_audio()
    feats = features_model()
    for texts, n, name in ((WAKE_TEXTS, n_pos, "positive"), (NEAR_MISSES, n_neg, "near_miss")):
        clips = synthesize(texts, n, rng, backgrounds, name)
        windows = last_window(feats.embed_clips((clips * 32767).astype(np.int16), batch_size=64, ncpu=4))
        np.save(WORK / f"{name}.npy", windows.astype(np.float32))
        print(f"{name}: {windows.shape}", flush=True)


def everyday_phrases(count, rng):
    """Ordinary English that never says the name: random word runs, often opened like real speech
    ("hey Sam", "okay so", "and then"). Round 1 false starts came from conversation, which the
    downloaded negatives (mostly music/noise) barely contain (2 of 1.4 M windows fooled it)."""
    words = [w.strip() for w in open("/usr/share/dict/american-english", encoding="utf-8", errors="ignore")
             if w.strip().isalpha() and w.strip().islower() and 2 <= len(w.strip()) <= 9]
    words = [w for w in words if not any(n in w for n in ("bemo", "bmo", "beemo"))]
    openers = ["hey", "okay", "so", "and", "well", "yeah", "oh", "hey there", "okay so", "but", "hi", "now"]
    phrases = []
    for _ in range(count):
        body = " ".join(words[rng.integers(len(words))] for _ in range(int(rng.integers(2, 7))))
        phrases.append(f"{openers[rng.integers(len(openers))]} {body}." if rng.random() < 0.6 else f"{body}.")
    return phrases


def generate_speech(count=8000):
    rng = np.random.default_rng(23)
    backgrounds = room_audio()
    texts = everyday_phrases(count, rng)
    clips = []
    for start in range(0, count, 1000):  # in parts, to keep memory low
        part = texts[start:start + 1000]
        clips.append(synthesize_each(part, rng, backgrounds))
    feats = features_model()
    windows = last_window(feats.embed_clips((np.concatenate(clips) * 32767).astype(np.int16), batch_size=64, ncpu=4))
    np.save(WORK / "speech.npy", windows.astype(np.float32))
    print(f"speech: {windows.shape}", flush=True)


def synthesize_each(texts, rng, backgrounds):
    from piper import PiperVoice, SynthesisConfig
    from scipy.signal import resample_poly
    voice = PiperVoice.load(str(VOICE))
    out = []
    for text in texts:
        config = SynthesisConfig(speaker_id=int(rng.integers(904)), length_scale=float(rng.uniform(0.8, 1.25)),
                                 noise_scale=float(rng.uniform(0.4, 0.9)), noise_w_scale=float(rng.uniform(0.5, 1.0)))
        audio = np.concatenate([np.frombuffer(c.audio_int16_bytes, dtype="<i2") for c in voice.synthesize(text, config)])
        audio = resample_poly(audio.astype(np.float32) / 32768.0, RATE, voice.config.sample_rate).astype(np.float32)
        out.append(place(audio[-int(1.9 * RATE):], rng, backgrounds))  # the last 1.9 s: where the window looks
    print(f"  speech: {len(out)} more clips", flush=True)
    return np.stack(out)


def acav():
    with open(ACAV, "rb") as handle:
        version = np.lib.format.read_magic(handle)
        shape, fortran, dtype = np.lib.format._read_array_header(handle, version)
        offset = handle.tell()
    rows = (ACAV.stat().st_size - offset) // (int(np.prod(shape[1:])) * dtype.itemsize)
    return np.memmap(ACAV, dtype=dtype, mode="r", offset=offset, shape=(rows, *shape[1:]))


class Net:
    @staticmethod
    def build():
        import torch
        return torch.nn.Sequential(torch.nn.Flatten(), torch.nn.Linear(16 * 96, 128), torch.nn.LayerNorm(128),
                                   torch.nn.ReLU(), torch.nn.Linear(128, 128), torch.nn.LayerNorm(128),
                                   torch.nn.ReLU(), torch.nn.Linear(128, 1), torch.nn.Sigmoid())


def mine(keep=20000):
    """Hard negatives: the general-negative windows the current model scores highest (the validation set is
    never used here, so its numbers stay honest)."""
    import onnxruntime as ort
    session = ort.InferenceSession(str(OUTPUT), providers=["CPUExecutionProvider"])
    negatives = acav()
    scores = np.concatenate([session.run(None, {"x": np.asarray(negatives[i:i + 8192], dtype=np.float32)})[0].ravel()
                             for i in range(0, len(negatives), 8192)])
    top = np.sort(np.argsort(scores)[-keep:])
    np.save(WORK / "hard_negative.npy", np.asarray(negatives[top], dtype=np.float32))
    print(f"hard negatives: {keep} windows, scores {scores[top].min():.3f}..{scores[top].max():.3f}; "
          f"{int(np.sum(scores > 0.5))} of {len(scores)} above 0.5", flush=True)


def owner_wakes():
    return [read_wav(p) for f in SAMPLES for p in sorted(glob.glob(f + "wake/*.wav"))]


def owner_positive_features(indices, copies=200, seed=31):
    """The owner's own recorded "Hey BMO"s, each in many varied copies (speed, loudness, background)."""
    from scipy.signal import resample_poly
    rng = np.random.default_rng(seed)
    backgrounds = room_audio()
    wakes = owner_wakes()
    clips = []
    for i in indices:
        voice = wakes[i]
        idx = np.where(np.abs(voice) > 0.02)[0]
        voice = voice[max(0, idx[0] - 800): idx[-1] + 800] if len(idx) else voice
        for _ in range(copies):
            speed = rng.uniform(0.92, 1.08)
            varied = resample_poly(voice, 100, int(round(100 * speed))).astype(np.float32)
            clips.append(place(varied, rng, backgrounds))
    feats = features_model()
    return last_window(feats.embed_clips((np.stack(clips) * 32767).astype(np.int16), batch_size=64, ncpu=4))


def validation_windows():
    frames = np.asarray(np.load(VALIDATION, mmap_mode="r"), dtype=np.float32)
    windows = np.lib.stride_tricks.sliding_window_view(frames, (16, 96))[:, 0]
    half = len(windows) // 2
    return windows[:half], windows[half:], half * 0.08 / 3600, (len(windows) - half) * 0.08 / 3600


def train_selected(steps=24000, max_negative_weight=1000.0, check_every=1000, owner_train=()):
    """openWakeWord's recipe: negative weight ramps to ~1000x; keep the checkpoint with <=0.5 false starts
    per hour on validation half A and the best recall on held-back synthetic positives. Half B and the
    owner's recordings are left for the final report (evaluate)."""
    import copy
    import torch
    torch.manual_seed(29)
    rng = np.random.default_rng(29)
    pos_all = np.load(WORK / "positive.npy")
    pos, pos_test = torch.tensor(pos_all[:-500]), torch.tensor(pos_all[-500:])
    if PUBLIC:
        owner_train = ()
    owner = torch.tensor(owner_positive_features(owner_train)) if owner_train else None
    near = torch.tensor(np.load(WORK / "near_miss.npy"))
    speech = torch.tensor(np.load(WORK / "speech.npy"))
    negatives = acav()
    val_a, _, hours_a, _ = validation_windows()
    val_a = torch.tensor(np.ascontiguousarray(val_a))
    model = Net.build()
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, steps)
    loss_fn = torch.nn.BCELoss(reduction="none")
    best, best_state = None, None
    for step in range(steps):
        idx = np.sort(rng.integers(0, len(negatives), 512))
        synthetic = pos[torch.randint(len(pos), (64 if owner is not None else 128,))]
        mine = [owner[torch.randint(len(owner), (64,))]] if owner is not None else []
        x = torch.cat([synthetic, *mine, near[torch.randint(len(near), (128,))],
                       speech[torch.randint(len(speech), (256,))],
                       torch.tensor(np.asarray(negatives[idx], dtype=np.float32))])
        n_neg = len(x) - 128
        y = torch.cat([torch.ones(128), torch.zeros(n_neg)])
        weight = 1.0 + (max_negative_weight - 1.0) * step / steps
        w = torch.cat([torch.ones(128), torch.full((n_neg,), weight)])
        loss = (loss_fn(model(x).squeeze(1), y) * w).mean()
        opt.zero_grad()
        loss.backward()
        opt.step()
        sched.step()
        if (step + 1) % check_every == 0:
            model.eval()
            with torch.no_grad():
                fa = torch.cat([model(val_a[i:i + 16384]).squeeze(1) for i in range(0, len(val_a), 16384)])
                above = (fa > 0.5).numpy()
                starts = int(np.sum(above[1:] & ~above[:-1]))
                recall = float((model(pos_test).squeeze(1) > 0.5).float().mean())
            model.train()
            fph = starts / hours_a
            print(f"  step {step + 1}: false starts/hour (half A) {fph:.2f}, recall {recall:.2f}", flush=True)
            if fph <= 0.5 and (best is None or recall > best[1]):
                best, best_state = (fph, recall, step + 1), copy.deepcopy(model.state_dict())
    if best_state is None:
        print("  no checkpoint reached 0.5 false starts per hour; keeping the last one", flush=True)
        best_state = model.state_dict()
    else:
        print(f"  chosen: step {best[2]}, {best[0]:.2f} false starts/hour, recall {best[1]:.2f}", flush=True)
    torch.save(best_state, WORK / "hey_bmo.pt")


def train(steps=12000):
    import torch
    torch.manual_seed(19)
    rng = np.random.default_rng(19)
    pos = torch.tensor(np.load(WORK / "positive.npy"))
    near = torch.tensor(np.load(WORK / "near_miss.npy"))
    hard_path = WORK / "hard_negative.npy"
    hard = torch.tensor(np.load(hard_path)) if hard_path.exists() else None
    speech_path = WORK / "speech.npy"
    speech = torch.tensor(np.load(speech_path)) if speech_path.exists() else None
    negatives = acav()
    print(f"positives {len(pos)}, near-misses {len(near)}, general negatives {len(negatives)}", flush=True)
    model = Net.build()
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, steps)
    loss_fn = torch.nn.BCELoss(reduction="none")
    for step in range(steps):
        p = pos[torch.randint(len(pos), (128,))]
        q = near[torch.randint(len(near), (128,))]
        idx = np.sort(rng.integers(0, len(negatives), 512))
        g = torch.tensor(np.asarray(negatives[idx], dtype=np.float32))
        parts = [p, q, g] + ([hard[torch.randint(len(hard), (256,))]] if hard is not None else []) \
            + ([speech[torch.randint(len(speech), (256,))]] if speech is not None else [])
        x = torch.cat(parts)
        n_neg = len(x) - 128
        y = torch.cat([torch.ones(128), torch.zeros(n_neg)])
        # False starts cost more than a missed "Hey BMO" (he can be asked again); weight negatives up over time.
        w = torch.cat([torch.ones(128), torch.full((n_neg,), 1.0 + (29.0 if hard is not None else 9.0) * step / steps)])
        out = model(x).squeeze(1)
        loss = (loss_fn(out, y) * w).mean()
        opt.zero_grad()
        loss.backward()
        opt.step()
        sched.step()
        if (step + 1) % 2000 == 0:
            print(f"  step {step + 1}/{steps} loss {loss.item():.4f}", flush=True)
    torch.save(model.state_dict(), WORK / "hey_bmo.pt")


def export():
    import torch
    model = Net.build()
    model.load_state_dict(torch.load(WORK / "hey_bmo.pt"))
    model.eval()
    torch.onnx.export(model, torch.zeros(1, 16, 96), str(OUTPUT), input_names=["x"], output_names=["score"],
                      opset_version=13, dynamic_axes={"x": {0: "batch"}, "score": {0: "batch"}})
    print(f"exported {OUTPUT} ({OUTPUT.stat().st_size / 1024:.0f} KB)", flush=True)


def clip_scores(clips):
    """Highest score over each clip, streamed in 80 ms frames like live audio."""
    from openwakeword.model import Model
    scores = []
    for clip in clips:
        model = Model(wakeword_models=[str(OUTPUT)], inference_framework="onnx",
                      melspec_model_path=str(MELSPEC), embedding_model_path=str(EMBEDDING))
        audio = np.concatenate([np.zeros(RATE // 2, np.float32), clip, np.zeros(RATE // 2, np.float32)])
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        best = 0.0
        for i in range(0, len(pcm) - 1280, 1280):
            best = max(best, float(list(model.predict(pcm[i:i + 1280]).values())[0]))
        scores.append(best)
    return np.array(scores)


def evaluate():
    import onnxruntime as ort
    session = ort.InferenceSession(str(OUTPUT), providers=["CPUExecutionProvider"])
    _, windows, _, hours = validation_windows()  # half B: never used for training or choosing
    scores = np.concatenate([session.run(None, {"x": np.ascontiguousarray(windows[i:i + 8192])})[0].ravel()
                             for i in range(0, len(windows), 8192)])
    report = {"validation_hours": round(hours, 1)}
    wakes = [read_wav(p) for f in SAMPLES for p in sorted(glob.glob(f + "wake/*.wav"))]
    rooms = room_audio(for_training=False)
    wake_scores, room_scores = clip_scores(wakes), clip_scores(rooms)
    for threshold in (0.3, 0.5, 0.7, 0.8, 0.9):
        above = scores > threshold
        starts = int(np.sum(above[1:] & ~above[:-1]) + int(above[0])) if len(above) else 0
        report[f"t{threshold}"] = {"false_starts_per_hour": round(starts / hours, 2),
                                   "owner_wakes_heard": f"{int(np.sum(wake_scores > threshold))}/{len(wake_scores)}",
                                   "owner_room_false_starts": f"{int(np.sum(room_scores > threshold))}/{len(room_scores)}"}
    report["owner_wake_scores"] = [round(float(s), 3) for s in wake_scores]
    print(json.dumps(report, indent=1), flush=True)
    (WORK / "report.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    stages = {"generate": generate, "train": train, "export": export, "evaluate": evaluate, "mine": mine,
              "speech": generate_speech, "train_selected": train_selected}
    for stage in sys.argv[1:]:
        print(f"== {stage}", flush=True)
        stages[stage]()
