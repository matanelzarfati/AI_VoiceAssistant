"""
Mia — Local AI-Powered Voice Assistant for Smart Home Control

==========================================================
=          Architect, developer and researcher:          =
=                     Matanel Zarfati                    =
==========================================================

It runs a fully local voice assistant that can listen, think, talk, and
toggle smart-home devices.

What it does
------------
• Listens for the wake word "Mia", then records a short follow-up utterance.
  - Offline STT: Faster-Whisper (CTranslate2) at 16 kHz.
• Builds a structured prompt from files in prompt_llm/ and queries a local
  Mistral-7B-Instruct (Q4_K_M, via llama-cpp-python).
• Parses the model’s reply for numeric device codes, validates them against
  command_prompt.txt and routine_prompt.txt, deduplicates per unit, and
  updates server_smart_home/devices.json atomically.
• Hosts a simple smart-home dashboard at http://localhost:8000 and opens it
  in Chrome.
• Speaks status/acknowledgements via pyttsx3.

Quick start
-----------
Python ≥ 3.10
pip install:
  huggingface-hub llama-cpp-python faster-whisper sounddevice scipy
  SpeechRecognition pyaudio pyttsx3
(Models download to ~/models on first run.)

Config tips
-----------
• WAKE_WORD is "Mia" by default.
• Devices use codes like 61 → device 6, mode 1 (last digit is mode 0/1 (off/on)).

Folders expected
----------------
prompt_llm/{default_prompt_1.txt, routine_prompt.txt, default_prompt_2.txt,
            command_prompt.txt, default_prompt_3.txt}
server_smart_home/devices.json  (list of {"number": int, "mode": 0|1})
"""


import os
from pathlib import Path
from huggingface_hub import hf_hub_download
from llama_cpp import Llama
import re
import pyttsx3

import tempfile
import pathlib
import sounddevice as sd
import scipy.io.wavfile as wavfile
from faster_whisper import WhisperModel
from huggingface_hub import snapshot_download

import json
from typing import List, Set

import sys
import http.server
import socketserver
import functools
import subprocess
import threading
import time

import speech_recognition as sr

import socket
from contextlib import closing
from typing import Iterable, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# ─── CONFIGURATIONS ──────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

# ─── CONFIGURATION (STT) ──────────────────────────────────────────────────────────

SAMPLE_RATE     = 16000             # 16 kHz mono for Whisper
SHORT_CHUNK_SEC = 0.5               # Record 0.5 second at a time for wake-word detection
FOLLOWUP_SEC    = 3.0               # Once “WAKE_WORD” is heard, record the next 3 seconds
WAKE_WORD       = "Mia"             # or 'Mia', 'Kim'
WAKE_WORD       = WAKE_WORD.lower()

# Instead of “base.en”, we point to the HF repo that contains the CTranslate2-converted files:
# MODEL_REPO    = "Systran/faster-whisper-base.en" # x2 real time, 0.5s Approx. wall-clock per 1 s audio
MODEL_REPO    = "Systran/faster-whisper-small.en" # x1 real time, 1s Approx. wall-clock per 1 s audio
# MODEL_REPO    = "Systran/faster-whisper-medium.en" # x0.3 real time, 3s Approx. wall-clock per 1 s audio
# MODEL_REPO    = "Systran/faster-whisper-large-v3" # x0.1 real time, 10s Approx. wall-clock per 1 s audio
DEVICE        = "cpu"        # or "cuda" if you have a GPU + matching CTranslate2 build
# "COMPUTE_TYPE" defaults to float32, for more accuracy
COMPUTE_TYPE  = "float32"       # int8 for speed, float16 (for GPU - cuda) for a bit more accuracy


# ─── CONFIGURATION (STT_net) ──────────────────────────────────────────────────────────

SAMPLE_RATE_NET     = 16000      # 16 kHz mono
LANGUAGE = "en-US"               # change to "he-IL" for Hebrew
SHORT_CHUNK_SEC_NET = 1.0        # Record 1.0 second at a time for wake-word detection
FOLLOWUP_SEC_NET    = 3.0        # Once “WAKE_WORD” is heard, record the next 3 seconds


# ─── CONFIGURATION (Switch_STT_Type) ──────────────────────────────────────────────────────────

# STT_TYPE = 0 => Local mode
# STT_TYPE = 1 => Network on mode
# STT_TYPE = 2 => Auto select mode (Local / Network on)
STT_TYPE = 1


# ─── CONFIGURATION (Server Smart Home) ──────────────────────────────────────────────────────────

PORT = 8000


# ─────────────────────────────────────────────────────────────────────────────
# ─── FUNCTIONS ──────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

# ─── List [int] processing (level 1) (verifies that numbers represent mode units) ───────────────────────────────────

def list_numbers_representing_mode_units(codes: List[int]) -> bool:
    """
    Given a list of integer codes, returns True if every code
    is defined in prompt_llm/command_prompt.txt and no contradictory
    on/off for the same device is requested. Otherwise, speaks an
    error prompt and returns False.
    """
    # empty list → ask user to repeat
    if not codes:
        text_to_speech("I don't understand, could you please repeat yourself?")
        return False

    # if not on/off for device? e.g. [63]
    for c in codes:
        mode = c % 10
        # only care about mode 0 or 1
        if mode not in (0, 1):
            text_to_speech("I don't understand, could you please repeat yourself?")
            return False

    # read all valid numbers from the .txt file
    cmd_file = Path(__file__).parent / "prompt_llm" / "command_prompt.txt"
    valid_numbers = set()
    with cmd_file.open("r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r'^\s*(\d+)\.\s', line)
            if m:
                valid_numbers.add(int(m.group(1)))

    # check that every requested code exists
    if not all(code in valid_numbers for code in codes):
        text_to_speech("I don't understand, could you please repeat yourself?")
        return False
    return True


# ─── List [int] processing (level 2) (create list of unique numbers and last mode per unit) ─────────────────────────

def list_unique_numbers_last_mode_unit(nums: List[int]) -> List[int]:
    """
    Process a list of ints:
      1) Remove exact duplicates while preserving first occurrences.
      2) For numbers sharing the same 'unit' (n//10), keep only the last
         occurrence among the deduplicated list (i.e., drop earlier states).

    Examples:
    - list_unique_numbers_last_mode_unit([60, 61])           # same unit 6 -> keep later state
    [61]
    - list_unique_numbers_last_mode_unit([60, 61, 60])       # dedupe -> [60,61] -> keep later per unit -> [61]
    [61]
    - list_unique_numbers_last_mode_unit([61, 60])           # same unit 6 -> keep later state
    [60]
    - list_unique_numbers_last_mode_unit([60, 61, 70, 71])   # keep 61 (unit 6) and 71 (unit 7)
    [61, 71]
    """
    # remove exact duplicates, keeping first occurrence
    seen = set()
    dedup: List[int] = []
    for n in nums:
        if n not in seen:
            seen.add(n)
            dedup.append(n)

    # within each 'unit' (tens group), keep the last occurrence
    last_index_by_unit = {}
    for i, n in enumerate(dedup):
        unit = n // 10
        last_index_by_unit[unit] = i

    result = [n for i, n in enumerate(dedup) if last_index_by_unit[n // 10] == i]
    return result


# ─── List [int] processing (level 3) (verifies that numbers represent standing instruction) ─────────────────────────

def list_numbers_representing_standing_instruction(nums: List[int]) -> bool:
    """
    Validate a list of integers against the standing-instruction groups found in
    'routine_prompt.txt'.

    Rules:
    - If len(nums) > 1 and the set of nums does NOT match (order-independent)
      any instruction group's numbers parsed from the file:
        1) call text_to_speech("I don't understand, could you please repeat yourself?")
        2) return False
    - Else: return True

    Notes:
    - The file is expected to have sections that start with lines beginning with '* '
      (e.g., '* Good morning'), followed by lines like '11. Turn on room light.'.
    - Matching is by exact set equality against a group's numbers (order doesn't matter).
    """
    # Early acceptance when list length == 1
    if len(nums) == 1:
        return True

    def _parse_groups_from_file(path: str = "prompt_llm/routine_prompt.txt") -> List[Set[int]]:
        groups: List[Set[int]] = []
        current: Set[int] | None = None

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                # New group header like: "* Good morning"
                if re.match(r"^\s*\*\s+", line):
                    if current:
                        groups.append(current)
                    current = set()
                    continue

                # Task line like: "11. Turn on room light."
                m = re.match(r"^\s*(\d+)\.\s", line)
                if m:
                    if current is None:
                        current = set()
                    current.add(int(m.group(1)))

        if current:
            groups.append(current)
        return groups

    try:
        groups = _parse_groups_from_file("prompt_llm/routine_prompt.txt")
    except FileNotFoundError:
        groups = []  # If file is missing, treat as no known groups.

    input_set = set(nums)

    # Accept if the numbers match exactly one of the groups (order-independent).
    for group_set in groups:
        if input_set == group_set:
            return True

    # If we got here: list has >1 item and doesn't match any known standing instruction.
    text_to_speech("I don't understand, could you please repeat yourself?")
    return False


# ─── Ensure model locally (STT) ──────────────────────────────────────────────────────────

def ensure_model_locally(repo_id: str, download_root: str) -> pathlib.Path:
    """
    Ensure the CTranslate2 model for `repo_id` exists under `download_root`.
    Returns the local path to the model directory.
    """
    download_root = os.path.expanduser(download_root)
    os.makedirs(download_root, exist_ok=True)

    # snapshot_download will clone the entire repo into download_root/<repo_id>.
    local_dir = snapshot_download(
        repo_id=repo_id,
        cache_dir=download_root,
        local_files_only=False,  # will fetch if missing
    )
    return pathlib.Path(local_dir)


# ─── Record audio to wav (STT) ──────────────────────────────────────────────────────────

def record_audio_to_wav(duration: float, fs: int) -> pathlib.Path:
    """
    Records `duration` seconds of mono 16-bit audio from the default microphone
    at sampling rate `fs`, writes it out to a temporary .wav file, and returns
    the pathlib.Path of that file.
    """
    recording = sd.rec(int(duration * fs), samplerate=fs, channels=1, dtype="int16")
    sd.wait()  # block until recording finishes

    fd, wav_path_str = tempfile.mkstemp(suffix=".wav")
    try:
        os.close(fd)
    except:
        pass

    wav_path = pathlib.Path(wav_path_str)
    wavfile.write(str(wav_path), fs, recording)  # write 16-bit PCM
    return wav_path


# ─── Transcribe whisper (STT) ──────────────────────────────────────────────────────────

def transcribe_whisper(model: WhisperModel, wav_path: pathlib.Path) -> str:
    """
    Uses faster-whisper’s model to transcribe the entire WAV file at `wav_path`.
    Returns the full transcript (lowercased, with segments concatenated).
    """
    segments, _info = model.transcribe(
        str(wav_path),
        beam_size=5,      # adjust for speed/accuracy
        vad_filter=True   # optional VAD to skip long silences
    )

    text = "".join([seg.text for seg in segments]).strip().lower()
    return text


# ─── Words Suffix Cleaner ──────────────────────────────────────────────────────────

def clean_suffix_words(words):
    """
    Given a list of strings, return a new list where each word has
    any trailing '.', ',', ':', ';', or '-', '!' removed.
    """
    return [word.rstrip('.,:;-!') for word in words]


# ─── Speech To Text (STT) ──────────────────────────────────────────────────────────

def speech_to_text() -> str:
    """
    Main entry point for speech recognition.
    Returns the transcript (lowercased, with segments concatenated).
    """
    # Ensure the model is downloaded under ~/models
    print(f"[Whisper] Checking for model “{MODEL_REPO}” in ~/models…")
    model_dir = ensure_model_locally(MODEL_REPO, "~/models")
    print(f"[Whisper] Model is available at: {model_dir}\n")

    # Load the Whisper model from that local directory
    print(f"[Whisper] Loading model from “{model_dir}” (device={DEVICE}, compute_type={COMPUTE_TYPE})…")
    model = WhisperModel(str(model_dir), device=DEVICE, compute_type=COMPUTE_TYPE)
    print("[Whisper] Model loaded. Starting wake-word loop…\n")

    try:
        text_to_speech("I'm listening...")
        while True:
            # Record 1 second (short chunk) for wake-word detection
            print(f"[Recorder] Recording {SHORT_CHUNK_SEC:.1f} sec chunk…", end="", flush=True)
            chunk_path = record_audio_to_wav(SHORT_CHUNK_SEC, SAMPLE_RATE)
            print(f"\r[Recorder] Chunk saved → {chunk_path.name}         ")

            # Transcribe that chunk
            print("[Whisper] Transcribing short chunk…", end="", flush=True)
            transcript = transcribe_whisper(model, chunk_path)
            print(f"\r[Whisper] Transcript: “{transcript}”        ")

            # Clean up the short-chunk WAV
            try:
                chunk_path.unlink()
            except:
                pass

            # Check for "WAKE_WORD"
            if WAKE_WORD in clean_suffix_words(transcript.split()):
                # AI_VoiceAssistant ready to listen
                text_to_speech("Yes...")

                print(f"[Assistant] Wake-word “{WAKE_WORD}” detected!")
                print(f"[Recorder] Now recording next {FOLLOWUP_SEC:.1f} sec…", end="", flush=True)

                # Record the next 5 seconds of audio
                followup_path = record_audio_to_wav(FOLLOWUP_SEC, SAMPLE_RATE)
                print(f"\r[Recorder] Follow-up saved → {followup_path.name}        ")

                # Transcribe those 5 seconds
                print("[Whisper] Transcribing follow-up…", end="", flush=True)
                followup_text = transcribe_whisper(model, followup_path)
                print(f"\r[Assistant] You said (next {FOLLOWUP_SEC:.0f} sec): “{followup_text}”\n")

                # Clean up the follow-up WAV
                try:
                    followup_path.unlink()
                except:
                    pass

                # After printing, go back to listening for “WAKE_WORD” again:
                # continue
                return followup_text

            else:
                print("[Assistant] No wake-word detected.\n")

            # Loop back to record another 1-sec chunk…

    except KeyboardInterrupt:
        print("\n[Assistant] Interrupted by user. Exiting.")
        exit(0)


# ─── Speech To Text Net (STT_net) ──────────────────────────────────────────────────────────

def recognize_google_safe(r: sr.Recognizer, audio: sr.AudioData, language: str) -> str:
    """
    Google Web Speech (free, rate-limited).
    """
    try:
        return r.recognize_google(audio, language=language)
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as e:
        # Network/service error
        print(f"[network error: {e}]")
        return ""

def listen_for_wake_word_stt_net(r: sr.Recognizer, source: sr.Microphone) -> None:
    """
    Blocks until the wake word is detected.
    """
    # print(f"Say '{WAKE_WORD}' to start...")
    text_to_speech("I'm listening...")
    while True:
        # short chunk listening for the wake word
        audio = r.listen(source, timeout=None, phrase_time_limit=SHORT_CHUNK_SEC_NET)
        text = recognize_google_safe(r, audio, LANGUAGE).lower()
        if WAKE_WORD in text:
            # AI_VoiceAssistant ready to listen
            text_to_speech("Yes...")
            return

def stream_for_stt_net(r: sr.Recognizer, source: sr.Microphone, language: str) -> str:
    """
    Capture speech for ~FOLLOWUP_SEC_NET seconds after wake and return one transcription.
    """
    try:
        audio = r.listen(source, timeout=FOLLOWUP_SEC_NET + 0.5, phrase_time_limit=FOLLOWUP_SEC_NET)
    except sr.WaitTimeoutError:
        return "" # nothing captured in this slice, try next

    return recognize_google_safe(r, audio, language).strip() # return text

def speech_to_text_net() -> str:
    """
    Transcribe speech to text using a free web API.
    Free STT via SpeechRecognition's Google Web Speech backend.
    Forces 16 kHz mono audio to avoid HTTP 400 'Bad Request'.

    If `audio_path` is provided (WAV/AIFF/FLAC recommended), transcribes that file.
    Otherwise, records from the default microphone.

    Returns:
        Recognized text (empty string if unintelligible).

    Raises:
        RuntimeError: If the web request fails or the API is unreachable.
    """
    r = sr.Recognizer()
    r.dynamic_energy_threshold = True

    # Force mic at 16 kHz to avoid Google 400 errors seen with higher rates
    with sr.Microphone(sample_rate=SAMPLE_RATE_NET) as source:
        # one-time ambient calibration
        r.adjust_for_ambient_noise(source, duration=0.5)

        listen_for_wake_word_stt_net(r, source)
        # tiny gap so the voice-assistant audio doesn't leak into next capture
        time.sleep(0.15)
        return stream_for_stt_net(r, source, LANGUAGE) # return text


# ─── Switch Speech To Text Type (STT type) ──────────────────────────────────────────────────────────

def switch_stt_type() -> str:
    """
    Switch between speech to text types according network on/off
    :return:
        string
    """
    # Local mode
    if STT_TYPE == 0:
        print("Offline Mode")
        text = speech_to_text()
        while is_text_empty(text):
            text = speech_to_text()
        return text

    # Network on mode
    elif STT_TYPE == 1:
        text = speech_to_text_net()
        while is_text_empty(text):
            text = speech_to_text_net()
        return text

    # Auto select mode (Local / Network on) (STT_TYPE == 2)
    else:
        while True:
            # Local mode
            if not internet_connection():
                print("Offline Mode")
                text = speech_to_text()
                if is_text_empty(text):
                    continue
                return text

            # Network on mode
            text = speech_to_text_net()
            if is_text_empty(text):
                continue
            return text


# ─── Is text empty ──────────────────────────────────────────────────────────

def is_text_empty(text: str) -> bool:
    """
    Check if text is empty.
    :param text:
    :return: True or False
    """
    if text == "":
        text_to_speech("I don't understand, could you please repeat yourself?")
        return True
    return False


# ─── Check Internet Connection ──────────────────────────────────────────────────────────

def internet_connection(timeout: float = 3.0,
                        targets: Iterable[Tuple[str, int]] | None = None) -> bool:
    """
    Check if the machine appears to have internet access.

    It tries to open short TCP connections to a few well-known public IPs.
    Using IP addresses (not hostnames) avoids relying on DNS.

    Args:
        timeout: Max seconds to wait for each connection attempt.
        targets: Optional iterable of (host, port) pairs to try.
                 Defaults include both IPv4 and IPv6 endpoints.

    Returns:
        True if any connection succeeds within the timeout; otherwise False.
    """
    if targets is None:
        targets = (
            ("1.1.1.1", 443),                 # Cloudflare
            ("8.8.8.8", 53),                  # Google DNS (TCP)
            ("9.9.9.9", 53),                  # Quad9 DNS (TCP)
            ("2606:4700:4700::1111", 443),    # Cloudflare IPv6
            ("2001:4860:4860::8888", 53),     # Google DNS IPv6
        )

    for host, port in targets:
        try:
            with closing(socket.create_connection((host, port), timeout=timeout)):
                return True
        except OSError:
            continue
    return False


# ─── LLM (large language model) setup ──────────────────────────────────────────────────────────

def get_model_path() -> str:
    """
    Download (if needed) and return the local path to the GGUF file.
    Uses ~/.cache/huggingface or overridden by HF_HOME; final download lands in ~/models.
    """
    cache_dir = os.path.expanduser("~/models")
    Path(cache_dir).mkdir(parents=True, exist_ok=True)

    return hf_hub_download(
        repo_id="TheBloke/Mistral-7B-Instruct-v0.2-GGUF",
        filename="mistral-7b-instruct-v0.2.Q4_K_M.gguf",
        # repo_id="TheBloke/Mixtral-8x7B-Instruct-v0.1-GGUF",
        # filename="mixtral-8x7b-instruct-v0.1.Q3_K_M.gguf",
        cache_dir=cache_dir
    )


# ─── Inference helper (LLM) ─────────────────────────────────────────────────────

def ask_llm(prompt: str,
            max_tokens: int = 256,
            temperature: float = 0.3,
            top_p: float = 0.95) -> str:
    """
    Send `prompt` to the local Mistral-7B and return its generated text.
    """
    resp = LLM(prompt,
               max_tokens=max_tokens,
               temperature=temperature,
               top_p=top_p)

    choice = resp["choices"][0]
    # Handle chat‐ and completion‐style outputs uniformly
    if "message" in choice and isinstance(choice["message"], dict):
        return choice["message"]["content"].strip()
    return choice.get("text", "").strip()


# ─── Text processing (String (response LLM) to list of numbers) ─────────────────────────────────────────────────────

def extract_numbers_from_brackets(text: str) -> list[int]:
    """
    Extracts numbers from the first bracketed list in `text`, e.g. "[1,2,3]".
    Returns them as a list of ints: [1, 2, 3].

    Raises:
        ValueError: if text exceeds 120 words or no valid bracketed list is found.
    """
    # Enforce max 120 words
    word_count = len(text.split())
    if word_count > 120:
        raise ValueError(f"Input has {word_count} words; maximum is 120.")

    # Find the first "[...]" containing digits, commas, and optional spaces
    match = re.search(r"\[([\d,\s]+)\]", text)
    if not match:
        # raise ValueError("No bracketed list of numbers found in the input.")
        nums = []
        return nums

    # Split on commas, strip whitespace, convert to ints (ignore non‐digits)
    nums_str = match.group(1)
    nums = []
    for token in nums_str.split(","):
        tok = token.strip()
        if tok.isdigit():
            nums.append(int(tok))
    return nums


# ─── Building a structure for prompt (LLM) ─────────────────────────────────────────────────────────

def build_structure_prompt() -> str:
    """
    Reads five text files (in a predetermined order) from the "prompt_llm/" folder,
    strips out all '\n' characters, concatenates their contents, appends user_prompt,
    and returns the resulting string.
    """
    # List the files in the exact order
    filenames = [
        "default_prompt_1.txt",
        "routine_prompt.txt",
        "default_prompt_2.txt",
        "command_prompt.txt",
        "default_prompt_3.txt"
    ]

    combined_text = ""

    for fname in filenames:
        path_fname = "prompt_llm/" + fname

        with open(path_fname, "r", encoding="utf-8") as f:
            # Read the content and remove all newline characters
            # content = f.read().replace("\n", "")

            # Read the content
            content = f.read()
            combined_text += content

    # Append the user_prompt (with a preceding space)
    combined_text += " '" + switch_stt_type() + "'"
    return combined_text


# ─── Text to Speech (TTS) ─────────────────────────────────────────────────────────

def text_to_speech(text: str):
    """
    Receive a string and speak it aloud using a preferred female macOS voice
    (tries 'Samantha' first, then 'Moira', then falls back to the system default).
    Speech rate is slowed by 25 wpm from the default, and volume is set to max.
    """
    # Initialize the engine with the macOS NSSpeechSynthesizer backend
    engine = pyttsx3.init(driverName="nsss")

    # Query all installed voices
    voices = engine.getProperty("voices")
    chosen_voice_id = None

    # List of preferred female‐voice name fragments (in order of priority)
    woman_voice_id = ["samantha", "ava", "karen", "tessa", "moira", "fiona"]

    # Try “Samantha” first
    for v in voices:
        if woman_voice_id[0] in v.name.lower():
            chosen_voice_id = v.id
            break

    # If “Samantha” wasn’t found, try “Moira” (index 4)
    if not chosen_voice_id:
        for v in voices:
            if woman_voice_id[4] in v.name.lower():
                chosen_voice_id = v.id
                break

    # Fallback to the first installed voice
    if not chosen_voice_id and voices:
        chosen_voice_id = voices[0].id

    # Apply the chosen voice
    engine.setProperty("voice", chosen_voice_id)

    # Fetch the default rate and subtract 25 wpm to slow down
    default_rate = engine.getProperty("rate")
    slowed_rate = max(80, default_rate - 25)
    engine.setProperty("rate", slowed_rate)

    # Ensure maximum volume
    engine.setProperty("volume", 1.0)

    # Speak the provided text
    engine.say(text)
    engine.runAndWait()


# ─── Devices Mode Processor (Atomic replace - [.json.tmp -> .json]) (.json file) ────────────────────────────────────

def update_devices_mode_atomic(
    codes: List[int],
    json_path: str = "server_smart_home/devices.json"
) -> None:
    """
    Given a list of integer codes, update the "mode" of each device in the JSON file,
    then atomically replace the old file.

    - Each code is interpreted as follows:
        * The last digit (0 or 1) is the new mode.
        * All preceding digits form the device number to look up in the JSON.

    Example:
      codes = [10, 31, 120]
        10  → device_number = 1,  new_mode = 0
        31  → device_number = 3,  new_mode = 1
        120 → device_number = 12, new_mode = 0
    """
    file_path = Path(json_path)

    # Load the existing list of devices from JSON.
    if not file_path.exists():
        raise FileNotFoundError(f"No such file: {json_path}")

    with file_path.open("r", encoding="utf-8") as f:
        try:
            devices = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Error decoding JSON from {json_path}: {e}")

    # Build a lookup from device_number -> device_dict
    device_lookup = {d.get("number"): d for d in devices}

    # Process each code
    for code in codes:
        code_str = str(code)
        if len(code_str) < 2:
            # Skip any "codes" that don't have at least 2 digits
            continue

        # Last character is the new mode; must be '0' or '1'
        new_mode_char = code_str[-1]
        if new_mode_char not in ("0", "1"):
            # Skip invalid modes
            continue
        new_mode = int(new_mode_char)

        # All preceding characters form the device number
        device_number = int(code_str[:-1])

        # Look up the device in our loaded list
        device = device_lookup.get(device_number)
        if device is None:
            # If no such device, skip or optionally log a warning
            # print(f"Warning: No device with number {device_number}")
            continue

        # Update the mode
        device["mode"] = new_mode

    # Write out to a temp file in the same directory (the updated list of devices back to .json.tmp -> .json)
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(devices, f, indent=2)
        f.flush()
        os.fsync(f.fileno())  # ensure all data is on disk

    # Atomically replace the old file
    os.replace(tmp_path, file_path)


# ─── Open chrome (Server Smart Home) ─────────────────────────────────────────────────────────

def open_chrome(url: str, width: int = 720, height: int = 680) -> None:
    """
    On macOS: open a brand-new Chrome window sized to WxH via osascript.
    On Windows/Linux: fall back to CLI flags.
    """
    if sys.platform.startswith("darwin"):
        # AppleScript wants {left, top, right, bottom}.
        # We offset the top by 22px so the titlebar isn't cut off.
        top_bar = 22
        apple = f'''
        tell application "Google Chrome"
            activate
            -- make a brand-new window
            set win to make new window
            -- position & size: {0},{top_bar} → {width},{height+top_bar}
            set bounds of win to {{0, {top_bar}, {width}, {height + top_bar}}}
            -- load our URL
            tell win to set URL of active tab to "{url}"
        end tell
        '''
        subprocess.run(["osascript", "-e", apple])

    elif sys.platform.startswith("win"):
        # Windows: give an empty title then flags
        args = ["--new-window", f"--window-size={width},{height}", url]
        cmd = f'start "" chrome {" ".join(args)}'
        subprocess.Popen(cmd, shell=True)

    else:
        # Linux: call the binary directly
        args = ["--new-window", f"--window-size={width},{height}", url]
        # try google-chrome then chrome
        for bin_name in ("google-chrome", "chrome"):
            try:
                subprocess.Popen([bin_name, *args])
                return
            except FileNotFoundError:
                continue
        raise FileNotFoundError("Could not find chrome on PATH")


# ─── Run http server (Server Smart Home) ─────────────────────────────────────────────────────────

def run_http_server(webroot: str, port: int):
    """
    Serve files from `webroot` on localhost:port until the program exits.
    This function blocks, so we start it in a separate thread.
    """
    # Create a request handler class that always serves files from the `webroot` directory
    Handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=webroot
    )
    with socketserver.TCPServer(("", port), Handler) as httpd:
        print(f"→ (HTTP Server) Serving folder '{webroot}' on port {port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n→ (HTTP Server) Shutting down...")
            httpd.server_close()


# ─── Main function ─────────────────────────────────────────────────────────

def main():
    """
    Main function for AI_VoiceAssistant.
    """
    # =====================================================================
    # ─── Smart Home Server (thread 2) ────────────────────────────────────
    # =====================================================================

    # Locate the folder that contains this script
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # We want to serve from the "server_smart_home" subfolder
    webroot = os.path.join(script_dir, "server_smart_home")
    if not os.path.isdir(webroot):
        print(f"Error: Cannot find folder 'server_smart_home' under:\n  {script_dir}")
        sys.exit(1)

    # Build the local URL
    url = f"http://localhost:{PORT}/"

    # ─── Start the HTTP server in a background thread ───────────────────────────
    server_thread = threading.Thread(
        target=run_http_server,
        args=(webroot, PORT),
        daemon=True  # don’t have to explicitly “close” or join thread when the main program exits
    )
    server_thread.start()

    # Give the server a moment to spin up (optional, but recommended)
    time.sleep(0.5)

    # ─── Open Chrome pointing to that URL ───────────────────────────────────────
    print(f"→ Attempting to open Chrome at {url}")
    open_chrome(url)

    # =====================================================================
    # =====================================================================

    # AI_VoiceAssistant ready to start
    text_to_speech(f"Hi, I’m {WAKE_WORD}. How can I help you?")


    # =====================================================================
    # ─── Interactive LLM loop in the main thread (thread 1) ──────────────
    # =====================================================================

    try:
        while True:
            # Build the prompt for the LLM
            user_prompt = build_structure_prompt()
            print("\n💬 Prompt:", user_prompt)

            # Let the TTS say “Thinking…”
            text_to_speech("Thinking…")

            # Ask the LLM (it returns something like "[1,2,3]")
            response_llm = ask_llm(user_prompt, max_tokens=120, temperature=0.3)
            print("\n💡 Raw LLM response:", response_llm)

            # Extract the numeric codes
            list_response = extract_numbers_from_brackets(response_llm)
            print("🔢 Parsed codes:", list_response)

            # Check 'list_response' commands (level 1)
            if not list_numbers_representing_mode_units(list_response):
                continue

            # Check 'list_response' commands (level 2)
            list_response = list_unique_numbers_last_mode_unit(list_response)
            print("🔢 Parsed codes (level 2):", list_response)

            # Check 'list_response' commands (level 3)
            if not list_numbers_representing_standing_instruction(list_response):
                continue

            # Update the devices.json file under server_smart_home/
            update_devices_mode_atomic(list_response)

            # Re‐open and print the updated JSON so we can see changes
            with open(os.path.join(webroot, "devices.json"), "r", encoding="utf-8") as f:
                updated = json.load(f)
            print("📂 Updated devices.json:")
            print(json.dumps(updated, indent=2))

            # Sleep for a moment before repeating (server smart home fetch each 1 second)
            time.sleep(1.0)

            text_to_speech("Done.")

    except KeyboardInterrupt:
        # When the user presses Ctrl+C, break out and let the program exit
        print("\nInterrupted by user. Exiting…")
        sys.exit(0)


# ─── Main ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Create a global Llama instance so the model loads just once
    print("▶ Downloading/loading model (this may take ~1–2 minutes)…")
    LLM = Llama(
        model_path=get_model_path(),
        n_ctx=32768,  # unlocks the 32 K token context
        n_threads=8,
        n_gpu_layers=0,
        verbose=False  # suppress all of those loader / metal diagnostics
    )
    print("✅ Model ready!")

    # =====================================================================
    main()
