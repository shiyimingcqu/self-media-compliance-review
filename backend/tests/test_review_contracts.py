import pytest
from pydantic import ValidationError

from app.core.review_contracts import Evidence


def test_reject_unlocatable_time_evidence():
    with pytest.raises(ValidationError):
        Evidence(scope="time-range", description="问题", start_ms=2000, end_ms=1000)
    with pytest.raises(ValidationError):
        Evidence(scope="source", description="问题")


def test_whole_video_absence_does_not_invent_a_timestamp():
    evidence = Evidence(scope="whole-video", description="全片未发现对应卖点，需人工复核")
    assert evidence.start_ms is None and evidence.end_ms is None
