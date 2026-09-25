"""Local Whisper transcription with provenance-preserving artifacts."""
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, List, Optional
import json

@dataclass
class WordTimestamp:
    start: float
    end: float
    word: str
    probability: Optional[float] = None

@dataclass
class SpeechSegment:
    start: float
    end: float
    text: str
    speaker: str = ""
    words: List[WordTimestamp] = field(default_factory=list)

    def to_dict(self):
        return {"start": self.start, "end": self.end, "text": self.text,
                "speaker": self.speaker, "words": [asdict(w) for w in self.words]}

def format_timestamp(seconds: float) -> str:
    m, s = int(seconds // 60), int(seconds % 60)
    return f"{m:02d}:{s:02d}"

def format_transcript_for_prompt(segments: List[SpeechSegment]) -> str:
    return "\n".join(f"[{format_timestamp(s.start)} - {format_timestamp(s.end)}] {s.speaker + ': ' if s.speaker else ''}{s.text.strip()}" for s in segments)

class Transcriber:
    """The only transcription source is local faster-whisper (engine is explicit)."""
    engine = "local-whisper"
    def __init__(self, model_size: str = "medium", device: str = "cpu", compute_type: str = "int8", diarization: bool = False):
        if self.engine != "local-whisper":
            raise ValueError("unsupported transcription engine")
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ImportError("Brak biblioteki faster-whisper") from exc
        self.model_name, self.device, self.compute_type = model_size, device, compute_type
        self.diarization_requested = diarization
        self.diarization_available = False
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
        if diarization:
            try:
                import pyannote.audio  # noqa: F401
                self.diarization_available = True
            except ImportError:
                pass

    def transcribe(self, wav_path: Path, language: str = "pl") -> List[SpeechSegment]:
        generated, _ = self.model.transcribe(str(wav_path), language=language, beam_size=5,
            word_timestamps=True, vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=200))
        results = []
        for s in generated:
            text = s.text.strip()
            if not text:
                continue
            words = []
            for w in (getattr(s, "words", None) or []):
                words.append(WordTimestamp(float(w.start), float(w.end), str(w.word), getattr(w, "probability", None)))
            results.append(SpeechSegment(float(s.start), float(s.end), text, "", words))
        return results

def write_transcription_artifacts(output_dir: Path, segments: List[SpeechSegment], metadata: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {"engine": "local-whisper", "model": metadata.get("transcription_model"),
               "track": metadata.get("track_chosen", metadata.get("track_requested")),
               "segments": [s.to_dict() for s in segments]}
    (output_dir / "transcription_segments.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
