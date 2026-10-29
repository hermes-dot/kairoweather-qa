r"""D1 练习：给 CLI 小工具补 3 条测试。

这 3 条是样板：D2/D3 的用例按同样结构自己写。
跑法：
    cd qa
    .\.venv\Scripts\python.exe -m pytest tests/test_my_practice.py -q
"""

from __future__ import annotations

import pytest

from tools.weather_report_v2 import analyze, to_float


@pytest.mark.parametrize(("raw", "expected"), [("-3.5", -3.5), (" 7 ", 7.0)])
def test_to_float_parses_negative_and_spaces(raw: str, expected: float) -> None:
    """负数和首尾空格都要能转对。"""
    assert to_float(raw, "tempMax", 0) == expected


def test_to_float_rejects_nan() -> None:
    """NaN 不是合法温度：应在 to_float 就被拒绝，而不是等到 render 里 round(nan) 崩溃。"""
    with pytest.raises(SystemExit):
        to_float("nan", "tempMax", 0)


def test_analyze_average_two_days() -> None:
    """两天四口温度的平均值：(10 + 0 + 20 + 10) / 4 = 10.0。"""
    daily = [
        {"fxDate": "2026-01-01", "tempMax": "10", "tempMin": "0"},
        {"fxDate": "2026-01-02", "tempMax": "20", "tempMin": "10"},
    ]
    assert analyze(daily)["average"] == 10.0
