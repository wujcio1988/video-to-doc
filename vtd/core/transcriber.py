from dataclasses import dataclass
from pathlib import Path
from typing import List

@dataclass
class SpeechSegment:
    start: float
    end: float
    text: str
    speaker: str = ""

def format_timestamp(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"

def format_transcript_for_prompt(segments: List[SpeechSegment]) -> str:
    lines = []
    for s in segments:
        lines.append(f"[{format_timestamp(s.start)} - {format_timestamp(s.end)}] {s.text.strip()}")
    return "\n".join(lines)

class Transcriber:
    """
    Wrapper na faster-whisper zoptymalizowany pod polski język techniczny.
    """
    def __init__(self, model_size: str = "large-v3", device: str = "cpu", compute_type: str = "int8"):
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ImportError(
                "Brak biblioteki faster-whisper. Zainstaluj ją: pip install faster-whisper"
            )
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)

    def transcribe(self, wav_path: Path, language: str = "pl") -> List[SpeechSegment]:
        segments_generator, _ = self.model.transcribe(
            str(wav_path),
            language=language,
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=200),
        )
        results = []
        for s in segments_generator:
            text = s.text.strip()
            if text:
                results.append(SpeechSegment(start=s.start, end=s.end, text=text))
        return results
