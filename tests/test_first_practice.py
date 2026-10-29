import pytest

from tools.weather_report_v2 import analyze, to_float


@pytest.mark.parametrize(("raw", "expected"), [("3.5", 3.5), ("-3.5", -3.5)])
def test_to_float(raw: str, expected: float) -> None:
    assert to_float(raw, "tempMax", 0) == expected


def test_to_float_rejects_nan() -> None:
    with pytest.raises(SystemExit):
       assert to_float("nan", "tempMax", 0)


def test_analyze_average_two_days() -> None:
    daily = [
        {"fxDate": "2026-01-01", "tempMax": "10", "tempMin": "0"},
        {"fxDate": "2026-01-02", "tempMax": "20", "tempMin": "10"},
    ]
    assert analyze(daily)["average"] == 10.0
