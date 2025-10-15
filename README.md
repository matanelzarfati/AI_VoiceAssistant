# A Local AI-Powered Voice Assistant for Smart-Home Control

**Author:** Matanel Zarfati  
**Program:** B.Sc. Computer Science  
**Platform:** Python 3.13  
**License:** See **License & Usage** (restricted)

> A fully local pipeline (ASR → LLM → deterministic validators → atomic state → TTS) for privacy-preserving smart-home control. No cloud calls in the main loop.

---

## Overview

Commercial voice assistants typically depend on remote services, trading away privacy and adding latency. This project demonstrates a **practical, private, and responsive** assistant that runs entirely on a laptop CPU:

- **Offline ASR:** Whisper (CTranslate2 / `faster-whisper`)  
- **Local LLM:** Mistral-7B-Instruct via `llama-cpp-python` (GGUF)  
- **Deterministic Safety Layer:** Validates and constrains LLM output to safe device updates  
- **Crash-Safe Persistence:** Atomic replace of a JSON state file  
- **Minimal UI:** Static HTML/JS dashboard polling the JSON state

> The academic report is **not** included in this repository.

---

## Repository Layout

```
AI_VoiceAssistant.py
prompt_llm/
  command_prompt.txt
  default_prompt_1.txt
  default_prompt_2.txt
  default_prompt_3.txt
  routine_prompt.txt
server_smart_home/
  devices.json
  index.html
  script.js
  style.css
research-demo/
  smart_home_animation_(smart_home_server).png
README.md
LICENSE
```

---

## Quick Start

```bash
# Python 3.13 virtual environment
python3.13 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

# Core dependencies
pip install huggingface-hub llama-cpp-python faster-whisper sounddevice scipy pyttsx3
```

**Models**

- Obtain a **GGUF** of *Mistral-7B-Instruct* (e.g., `Q4_K_M`) via Hugging Face.
- Whisper is managed by `faster-whisper` (CT2); the *small* model is recommended for laptop CPUs.
- If needed, update the paths inside `AI_VoiceAssistant.py`.

**Run**

```bash
python AI_VoiceAssistant.py
# Opens a local HTTP server; visit: http://localhost:8000/
```

---

## What It Does (high level)

1. **Wake-word & Follow-up:** Listens for “Mia”, then records a short follow-up utterance.  
2. **ASR (local):** Transcribes on CPU (VAD + beam search).  
3. **Prompting:** Concatenates the five prompt files and appends the user utterance.  
4. **LLM (local):** Returns a **single bracketed integer list** (constrained format).  
5. **Validation:**  
   - Rejects unknown/ill-formed codes.  
   - Resolves conflicts by **last state per device**.  
   - Allows multi-action only when matching a **defined routine**.  
6. **Persistence & UI:** Applies changes to `devices.json` via **atomic replace**; the dashboard polls ~1 Hz.  
7. **TTS:** Provides concise spoken feedback.

---

## Requirements & Notes

- **OS:** macOS, Linux, or Windows (tested on macOS with NSSpeechSynthesizer via `pyttsx3`).  
- **Audio:** 16 kHz mono microphone; ensure permissions are granted.  
- **Performance (typical on M1 Pro):** near real-time ASR for short commands; LLM ~100–400 ms for short outputs.

---

## Configuration

- Wake word, chunk sizes, and follow-up length are constants in `AI_VoiceAssistant.py`.  
- Adjust device catalog in `prompt_llm/command_prompt.txt`.  
- Define multi-action routines in `prompt_llm/routine_prompt.txt` (headers starting with `* `).

---

## License & Usage (important)

This repository is **copyright © Matanel Zarfati. All rights reserved.**

- **Non-commercial, no redistribution, no derivative works** without explicit written permission.  
- Academic reviewers may **clone and run locally** for evaluation only.  
- Public forks, code reuse, or model/prompt extraction are **not permitted**.

```
Copyright (c) 2025 Matanel Zarfati. All rights reserved.

Permission is granted to individuals who received this repository directly from the author
to use, run, and evaluate the software for non-commercial, academic review purposes only.
Redistribution, publication, sublicensing, and creation of derivative works are prohibited
without prior written consent from the author.
```

#### For the complete and binding license terms, see the attached [LICENSE](LICENSE) file.

---

## Citation

If you reference this work in an academic context:

```
Zarfati, M. (2025). A Local AI-Powered Voice Assistant for Smart-Home Control.
Undergraduate Research Project, Azrieli College of Engineering Jerusalem.
```

---

## Contact

For academic review access or licensing inquiries: **Matanel Zarfati** — open an issue or contact privately.

---

## Demo

![Smart Home Animation](./research-demo/smart_home_animation_%28smart_home_server%29.png)
