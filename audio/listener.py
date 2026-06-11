"""
Audio listener - captures microphone audio in real-time,
transcribes using either:
  - faster-whisper  (local, free, no API key needed)  [STT_PROVIDER=faster-whisper]
  - OpenAI Whisper API                                 [STT_PROVIDER=whisper]
and detects when a question is being asked.
"""
import io
import numpy as np
import threading
import queue
import re
import time
from typing import Callable, Optional
import config


# Lazy imports - only loaded when listener starts
_sounddevice  = None
_WhisperModel = None   # faster-whisper
_openai       = None   # openai whisper API


# ── Question detection patterns ───────────────────────────────────────────────
QUESTION_PATTERNS = [
    r"\?$",                                           # Ends with question mark
    r"^(what|who|where|when|why|how|which|whose)\b",  # WH-questions
    r"^(can|could|would|should|is|are|do|does|did|has|have|will|won't|can't)\b",
    r"(tell me|explain|describe|clarify|elaborate|what do you think)",
]

QUESTION_REGEX = re.compile(
    "|".join(QUESTION_PATTERNS),
    re.IGNORECASE
)


def _load_sounddevice():
    global _sounddevice
    import sounddevice as sd
    _sounddevice = sd


def _load_faster_whisper(model_size: str):
    global _WhisperModel
    from faster_whisper import WhisperModel as _WM
    _WhisperModel = _WM
    print(f"[STT] Loading faster-whisper '{model_size}' model... (first run may download ~150MB)")
    model = _WhisperModel(model_size, device="cpu", compute_type="int8")
    print(f"[STT] OK faster-whisper '{model_size}' loaded")
    return model


def _load_openai_whisper():
    global _openai
    import openai as _oa
    _openai = _oa
    key = config.OPENAI_API_KEY
    if not key:
        raise ValueError(
            "OPENAI_API_KEY not set. Add it to your .env file "
            "(required when STT_PROVIDER=whisper)."
        )
    _openai.api_key = key
    print(f"[STT] OK OpenAI Whisper API ready (model: {config.STT_MODEL})")
    return None  # no local model object needed


# ── AudioListener ─────────────────────────────────────────────────────────────

class AudioListener:
    def __init__(
        self,
        on_question_detected: Callable[[str], None],
        sample_rate: int   = 16000,
        chunk_duration: float  = 0.5,    # seconds per audio chunk
        buffer_duration: float = 8.0,    # rolling buffer size in seconds
        silence_threshold: float = 0.01, # RMS below this = silence
        silence_gap: float     = 1.2,    # seconds of silence to trigger transcription
        # Deprecated positional arg kept for backwards compat; prefer STT_MODEL in .env
        model_size: Optional[str] = None,
    ):
        self.on_question_detected = on_question_detected
        self.sample_rate     = sample_rate
        self.chunk_samples   = int(sample_rate * chunk_duration)
        self.buffer_samples  = int(sample_rate * buffer_duration)
        self.silence_threshold = silence_threshold
        self.silence_gap     = silence_gap

        # STT config from env (env wins over legacy positional arg)
        self._stt_provider = config.STT_PROVIDER
        self._stt_model    = model_size or config.STT_MODEL

        self._audio_queue: queue.Queue = queue.Queue()
        self._running          = False
        self._local_model      = None   # faster-whisper model instance
        self._rolling_buffer   = np.array([], dtype=np.float32)
        self._last_speech_time = time.time()
        self._speech_detected  = False

    # ── Lifecycle ─────────────────────────────────────────────────

    def start(self):
        """Load STT model/client and start listening."""
        _load_sounddevice()

        if self._stt_provider == "faster-whisper":
            self._local_model = _load_faster_whisper(self._stt_model)
        elif self._stt_provider == "whisper":
            _load_openai_whisper()
        else:
            raise ValueError(
                f"Unknown STT_PROVIDER '{self._stt_provider}'. "
                "Choose one of: faster-whisper, whisper"
            )

        self._running = True

        worker = threading.Thread(target=self._transcription_worker, daemon=True)
        worker.start()

        print(f"[STT] Listening on default microphone...")
        with _sounddevice.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.chunk_samples,
            callback=self._audio_callback,
        ):
            while self._running:
                time.sleep(0.1)

    def stop(self):
        self._running = False

    # ── Internal helpers ──────────────────────────────────────────

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status):
        """Called by sounddevice for each audio chunk."""
        if status:
            print(f"[STT] Stream status: {status}")
        self._audio_queue.put(indata[:, 0].copy())

    def _transcription_worker(self):
        """
        Consumes audio chunks, maintains a rolling buffer,
        and transcribes when silence is detected after speech.
        """
        while self._running:
            try:
                chunk = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                if (self._speech_detected and
                        time.time() - self._last_speech_time > self.silence_gap):
                    self._transcribe_buffer()
                continue

            self._rolling_buffer = np.append(self._rolling_buffer, chunk)

            if len(self._rolling_buffer) > self.buffer_samples:
                self._rolling_buffer = self._rolling_buffer[-self.buffer_samples:]

            rms = float(np.sqrt(np.mean(chunk ** 2)))
            if rms > self.silence_threshold:
                self._speech_detected = True
                self._last_speech_time = time.time()
            elif (self._speech_detected and
                  time.time() - self._last_speech_time > self.silence_gap):
                self._transcribe_buffer()

    def _transcribe_buffer(self):
        """Transcribe the current rolling buffer and detect questions."""
        if len(self._rolling_buffer) < self.sample_rate * 1.0:
            self._speech_detected = False
            return

        audio_to_transcribe = self._rolling_buffer.copy()
        self._rolling_buffer   = np.array([], dtype=np.float32)
        self._speech_detected  = False

        try:
            if self._stt_provider == "faster-whisper":
                transcript = self._transcribe_local(audio_to_transcribe)
            else:
                transcript = self._transcribe_api(audio_to_transcribe)

            if not transcript:
                return

            print(f"[STT] Transcript: {transcript}")

            if self._is_question(transcript):
                print(f"[STT] ❓ Question detected: {transcript}")
                self.on_question_detected(transcript)

        except Exception as exc:
            print(f"[STT] Transcription error: {exc}")

    def _transcribe_local(self, audio: np.ndarray) -> str:
        """Transcribe using local faster-whisper model."""
        segments, _info = self._local_model.transcribe(
            audio,
            beam_size=5,
            language="en",
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    def _transcribe_api(self, audio: np.ndarray) -> str:
        """Transcribe using OpenAI Whisper API (sends raw PCM as WAV bytes)."""
        import wave

        # Build an in-memory WAV file from the float32 PCM data
        pcm_int16 = (audio * 32767).clip(-32768, 32767).astype(np.int16)
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)   # 16-bit
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm_int16.tobytes())
        wav_buffer.seek(0)
        wav_buffer.name = "audio.wav"   # required by OpenAI client

        response = _openai.audio.transcriptions.create(
            model=self._stt_model,
            file=wav_buffer,
            language="en",
        )
        return response.text.strip()

    def _is_question(self, text: str) -> bool:
        """Determine if transcribed text is a question."""
        text = text.strip()
        if not text:
            return False
        if len(text.split()) < 4:
            return False
        return bool(QUESTION_REGEX.search(text))
