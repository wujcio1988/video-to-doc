from pathlib import Path
from unittest.mock import patch, MagicMock
from vtd.core.frame_qa import FrameReviewer


def test_frame_reviewer_verdict_ok(tmp_path):
    fp = tmp_path / "frame.png"
    fp.write_bytes(b"dummy")

    reviewer = FrameReviewer()

    with patch.object(reviewer.provider, "chat_completion", return_value='{"verdict": "ok", "reason": "Wyraźne okno"}'):
        res = reviewer.review_step_frame(1, "Otwarcie okna", "Otwórz moduł", fp)
        assert res["verdict"] == "ok"
        assert "Wyraźne" in res["reason"]


def test_frame_reviewer_verdict_unclear(tmp_path):
    fp = tmp_path / "frame.png"
    fp.write_bytes(b"dummy")

    reviewer = FrameReviewer()

    with patch.object(reviewer.provider, "chat_completion", return_value='{"verdict": "unclear", "reason": "Rozmyte"}'):
        res = reviewer.review_step_frame(1, "Otwarcie okna", "Otwórz moduł", fp)
        assert res["verdict"] == "unclear"
