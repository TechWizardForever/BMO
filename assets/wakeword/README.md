# BMO's "Hey BMO" detector (installer plan I7)

| File | What | Licence |
|---|---|---|
| `hey_bmo.onnx` | the public "Hey BMO" detector, trained on this project's PC with `tools/wakeword/make_hey_bmo.py` in public mode: synthetic voices only (904 LibriTTS-R speakers through Piper), nothing of the maker's | CC BY-NC-SA 4.0 (its negative training data, ACAV100M features from openWakeWord, carry that licence) |
| `melspectrogram.onnx`, `embedding_model.onnx` | openWakeWord's audio feature models | Apache-2.0 (dscripka/openWakeWord) |

Measured 2026-10-20 on audio it never trained on: 0.37 false starts per hour (5.3 h of validation audio,
threshold 0.7); 9 of the maker's 10 recorded "Hey BMO"s heard; 0 of 37 room recordings woke it. With the
first-boot voice setup on top (6 "Hey BMO"s + 10 s of talk): 4/4 held-out "Hey BMO"s, 0/34 room sounds.

`install.sh` copies these into `~/.bemo/models/wakeword/` only where no detector is installed yet.
