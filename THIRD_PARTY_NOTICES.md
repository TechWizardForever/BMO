# Third-party notices

BMO's own code is licensed under the GNU General Public License, version 3 (`LICENSE`). BMO is a free,
non-commercial fan project; it is not affiliated with or endorsed by the owners of Adventure Time. BMO's
name and look belong to them.

## Brains, voice and speech (downloaded or bundled)

| Part | Where it comes from | Licence |
|---|---|---|
| Main, Engineering, Coding brains (Qwen3 30B-A3B, Qwen3-Coder 30B-A3B) | Alibaba Qwen, via Ollama | Apache-2.0 |
| Emergency brain (Qwen3 4B) | Alibaba Qwen, via Ollama | Apache-2.0 |
| Deep Vision brain (InternVL3.5 30B-A3B, GGUF by bartowski) | OpenGVLab, via Hugging Face | to confirm from the model card before release |
| Ollama 0.40.5 | ollama/ollama on GitHub | MIT |
| Whisper base.en / tiny.en (faster-whisper conversions) | OpenAI; Systran | MIT |
| Silero VAD (shipped inside faster-whisper) | Silero | MIT |
| Piper 1.8 (`piper-tts`) | OHF-Voice/piper1-gpl | GPL-3.0; source: https://github.com/OHF-Voice/piper1-gpl |
| BMO's Piper voice (`en_US-bemo-best-v3`, release voice-1) | fan-made by BMO's maker for this non-commercial project | see the note above about the character |
| openWakeWord and its feature models | dscripka/openWakeWord | Apache-2.0 |
| BMO's "Hey BMO" detector (`assets/wakeword/hey_bmo.onnx`) | trained for this project with openWakeWord from synthetic voices; the training data includes ACAV100M features | CC BY-NC-SA 4.0 (non-commercial, credit, same licence for changes) |

## Python packages (exact versions from installer/requirements.lock)

| Package | Version | Licence (from the package's metadata) |
|---|---|---|
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| av | 19.0.1 | BSD-3-Clause |
| beautifulsoup4 | 4.15.0 | MIT License |
| cadquery-ocp | 7.7.2 | Apache Software License |
| cadquery | 2.4.0 | Apache Software License |
| casadi | 3.6.7 | GNU Lesser General Public License v3 or later (LGPLv3+) |
| certifi | 2026.7.22 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| cloudpickle | 3.1.2 | BSD License |
| contourpy | 1.3.3 | BSD License |
| ctranslate2 | 4.8.3 | MIT |
| cycler | 0.12.1 | BSD License |
| et_xmlfile | 2.0.0 | MIT License |
| ezdxf | 1.3.4 | MIT License |
| faster-whisper | 1.2.1 | MIT License |
| filelock | 4.2.0 | MIT |
| flatbuffers | 25.12.19 | Apache Software License |
| fonttools | 4.67.0 | MIT |
| fsspec | 2026.9.0 | BSD-3-Clause |
| h11 | 0.16.0 | MIT License |
| hf-xet | 1.7.0 | Apache-2.0 |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD License |
| huggingface_hub | 1.33.0 | Apache Software License |
| idna | 3.20 | BSD-3-Clause |
| joblib | 1.6.0 | BSD-3-Clause |
| kiwisolver | 1.5.1 | BSD License |
| loguru | 0.7.3 | MIT License |
| matplotlib | 3.11.2 | Python Software Foundation License |
| mpmath | 1.3.0 | BSD License |
| multimethod | 1.9.1 | Apache Software License |
| narwhals | 2.27.1 | MIT |
| nlopt | 2.7.1 | MIT License |
| nptyping | 2.0.1 | MIT License |
| numpy | 1.26.4 | BSD License |
| ollama | 0.6.3 | MIT |
| onnxruntime | 1.31.0 | MIT License |
| openpyxl | 3.1.5 | MIT License |
| openwakeword | 0.6.0 | Apache Software License |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| path | 17.0.0 | MIT License |
| pathvalidate | 3.3.1 | MIT License |
| pillow | 11.3.0 | MIT-CMU |
| piper-tts | 1.8.0 | GPL-3.0-or-later |
| protobuf | 7.36.2 | 3-Clause BSD License |
| psutil | 7.2.2 | BSD-3-Clause |
| PyAudio | 0.2.14 | MIT License |
| pycparser | 3.11 | BSD-3-Clause |
| pydantic | 2.14.1 | MIT |
| pydantic_core | 2.50.1 | MIT |
| pygame | 2.6.1 | GNU Library or Lesser General Public License (LGPL) |
| pyparsing | 3.3.3 | MIT |
| pypdf | 6.21.0 | BSD-3-Clause |
| pyserial | 3.5 | BSD License |
| pytesseract | 0.3.13 | Apache Software License |
| python-dateutil | 2.9.0.post0 | BSD License, Apache Software License |
| PyVISA-py | 0.8.1 | MIT License |
| PyVISA | 1.16.2 | MIT License |
| PyYAML | 6.0.3 | MIT License |
| requests | 2.34.2 | Apache Software License |
| scikit-learn | 1.9.1 | BSD-3-Clause |
| scipy | 1.17.1 | BSD License |
| six | 1.17.0 | MIT License |
| soundfile | 0.14.0 | BSD License |
| soupsieve | 3.0.3 | MIT |
| SpeechRecognition | 3.17.0 | BSD-3-Clause |
| sympy | 1.13.1 | BSD License |
| threadpoolctl | 3.7.0 | BSD-3-Clause |
| tokenizers | 0.22.2 | Apache Software License |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| trimesh | 4.12.2 | MIT License |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| typish | 1.9.3 | MIT License |
| urllib3 | 2.8.0 | MIT |
