"""W2：给 weather_report_v2.py 写 pytest 用例。

用例按「一个函数一组」组织，覆盖三类输入：

  1. 正常数据 —— 报表算得对、渲染得出来；
  2. 非法数据 —— 文件缺失 / JSON 损坏 / 业务码异常 / 温度脏数据，
     每一条都对应一种真实会出现的坏输入；
  3. 边界 —— 只有一天、全天同温。空数组和除零最容易在这里翻车。

三条纪律（和 README 呼应）：
  · 不碰真实网络：被测对象是本地 CLI，数据全部来自 workdir 或仓库里的 fixture；
  · 不 mock 被测函数：mock 掉 analyze 再断言 analyze，等于自己证明自己；
  · 失败信息要能定位：SystemExit 的提示里必须带「第几天 / 哪个字段 / 原始值」。

跑法：
    cd qa
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_weather_report.py -v
"""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import allure
import pytest

# 被测脚本在 qa/tools/；qa/pyproject.toml 的 pythonpath = ["."] 已把 qa 根目录加进 sys.path。
from tools import weather_report_v2 as report

# 仓库里现成的 3 天样例（2026-10-23 ~ 10-25），正常路径直接用它。
FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "daily_ok.json"


@pytest.fixture
def workdir() -> Iterator[Path]:
    """每条用例一个独立目录，收尾时删掉。

    为什么不用 pytest 内置的 tmp_path：本会话的受限环境里系统临时目录不可写，
    pytest 建目录会直接 WinError 5。把临时文件放在 qa/tmp_tests/ 里，
    既不依赖系统 TMP，也不会污染仓库（tmp_tests/ 已在 .gitignore 里）。
    """
    path = Path(__file__).resolve().parents[1] / "tmp_tests" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _write_json(path: Path, payload: object) -> Path:
    """把 payload 写成 UTF-8 JSON 文件，返回路径——供需要“坏数据”的用例造输入。"""
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# --------------------------------------------------------------- load_daily
@allure.epic("KairoWeather 测试工程")
@allure.feature("天气简报 CLI")
@allure.story("读取数据")
def test_load_daily_reads_valid_fixture() -> None:
    """正常路径：能读出 3 天，且顺序与文件一致（顺序错了图表会倒着画）。"""
    daily = report.load_daily(FIXTURE)

    assert isinstance(daily, list)
    assert len(daily) == 3
    assert [item["fxDate"] for item in daily] == ["2026-10-23", "2026-10-24", "2026-10-25"]


def test_load_daily_missing_file_exits_with_path(workdir: Path) -> None:
    """文件不存在：给中文提示 + 具体路径，不能甩 traceback 给用户。"""
    missing = workdir / "nope.json"

    with pytest.raises(SystemExit) as excinfo:
        report.load_daily(missing)

    message = str(excinfo.value)
    assert "找不到文件" in message
    assert str(missing) in message, "提示里没有路径，用户不知道该找哪个文件"


def test_load_daily_broken_json_reports_line_and_column(workdir: Path) -> None:
    """坏 JSON：提示要精确到行/列，否则用户只能对着整份文件猜。"""
    broken = workdir / "broken.json"
    # 第 3 行少一个逗号 —— json.JSONDecodeError 会报 line 3 / column 3。
    broken.write_text('{\n  "code": "200"\n  "daily": []\n}', encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        report.load_daily(broken)

    message = str(excinfo.value)
    assert "JSON 解析失败" in message
    assert "第 3 行第 3 列" in message


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "404", "daily": [{"fxDate": "2026-01-01", "tempMax": "1", "tempMin": "0"}]},
        {"daily": [{"fxDate": "2026-01-01", "tempMax": "1", "tempMin": "0"}]},
    ],
    ids=["code=404", "code 缺失"],
)
def test_load_daily_rejects_non_200_business_code(workdir: Path, payload: dict) -> None:
    """业务码不是 200（或缺失）：必须在读 daily 之前就失败，不能拿错误数据继续算。"""
    path = _write_json(workdir / "payload.json", payload)

    with pytest.raises(SystemExit) as excinfo:
        report.load_daily(path)

    assert "业务码异常" in str(excinfo.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "200", "daily": []},
        {"code": "200"},
        {"code": "200", "daily": {}},
    ],
    ids=["空数组", "字段缺失", "类型不是数组"],
)
def test_load_daily_rejects_empty_or_missing_daily(workdir: Path, payload: dict) -> None:
    """没有可分析的数据时要明确报错，而不是让后面的 analyze 去面对空列表。"""
    path = _write_json(workdir / "payload.json", payload)

    with pytest.raises(SystemExit) as excinfo:
        report.load_daily(path)

    assert "daily 为空或缺失" in str(excinfo.value)


# ----------------------------------------------------------------- to_float
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("26", 26.0), ("-3.5", -3.5), (" 7 ", 7.0), ("0", 0.0)],
    ids=["整数串", "负数小数串", "带首尾空格", "零"],
)
def test_to_float_parses_numeric_strings(raw: object, expected: float) -> None:
    """和风把温度当字符串返回；能转的都要转对（含负数和首尾空格）。"""
    assert report.to_float(raw, "tempMax", 0) == expected


@pytest.mark.parametrize("raw", ["abc", None, ""], ids=["非数字串", "None", "空串"])
@allure.epic("KairoWeather 测试工程")
@allure.feature("天气简报 CLI")
@allure.story("脏数据定位")
def test_to_float_rejects_dirty_data_with_context(raw: object) -> None:
    """脏数据：报错要同时给出「第几天 + 字段名 + 原始值」，否则等于没报。"""
    with pytest.raises(SystemExit) as excinfo:
        report.to_float(raw, "tempMax", 1)

    message = str(excinfo.value)
    assert "第 2 天" in message
    assert "tempMax" in message
    assert repr(raw) in message, "原始值没进提示，排障时不知道坏成了什么样"


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf"], ids=["NaN", "Inf", "-Inf"])
@allure.epic("KairoWeather 测试工程")
@allure.feature("天气简报 CLI")
@allure.story("非有限数字")
def test_to_float_rejects_non_finite(raw: str) -> None:
    """NaN/Inf 能被 float() 接受，但不是合法温度；必须在这里拦下。

    如果漏到 render，round(nan) 才会抛 ValueError，报错会离现场很远。
    """
    with pytest.raises(SystemExit) as excinfo:
        report.to_float(raw, "tempMax", 0)

    message = str(excinfo.value)
    assert "不是有限数字" in message
    assert repr(raw) in message


# ------------------------------------------------------------------ analyze
@allure.epic("KairoWeather 测试工程")
@allure.feature("天气简报 CLI")
@allure.story("统计正确性")
def test_analyze_computes_extremes_average_and_widest() -> None:
    """统计正确性：用 README 里那 3 天数据手算一遍对答案。

    26/24/21 与 12/11/9：
      最高 26（10-23）、最低 9（10-25）、最大温差 14（10-23）、
      平均 = (26+24+21+12+11+9) / 6 = 17.1666...，渲染时四舍五入成 17.2。
    """
    result = report.analyze(report.load_daily(FIXTURE))

    assert result["hottest"]["high"] == 26.0
    assert result["hottest"]["date"] == "2026-10-23"
    assert result["coldest"]["low"] == 9.0
    assert result["coldest"]["date"] == "2026-10-25"
    assert result["widest"]["high"] - result["widest"]["low"] == 14.0
    assert result["widest"]["date"] == "2026-10-23"
    assert result["average"] == pytest.approx(103 / 6)
    assert [row["date"] for row in result["rows"]] == [
        "2026-10-23",
        "2026-10-24",
        "2026-10-25",
    ]


def test_analyze_and_render_survive_single_day_with_flat_temperature() -> None:
    """边界：只有一天、且最高温 = 最低温。

    这里藏着两个容易翻车的点：
      1. 单日时 hottest / coldest / widest 必须是同一行，不能为空；
      2. 柱状图要算 (value - low) / (high - low)，全同温时分母为 0。
    """
    result = report.analyze([{"fxDate": "2026-01-01", "tempMax": "20", "tempMin": "20"}])
    text = report.render(result)

    assert result["average"] == 20.0
    assert "2026-01-01 ~ 2026-01-01（1 天）" in text
    assert "最高温  20°C   2026-01-01" in text
    assert "█" in text, "全同温也必须画出至少一格，不能空白或崩溃"


# ------------------------------------------------------------------- render
def test_render_prints_summary_and_one_bar_per_day() -> None:
    """渲染层：首行是日期区间，正文每天一行，柱子长度随温度递减。"""
    text = report.render(report.analyze(report.load_daily(FIXTURE)))
    lines = text.splitlines()

    assert lines[0] == "2026-10-23 ~ 2026-10-25（3 天）"
    assert "平均温  17.2°C" in text

    rows = [line for line in lines if line.startswith("  2026-")]
    assert len(rows) == 3, f"每天应有一行柱状图，实际 {rows}"

    widths = [row.count("█") for row in rows]
    assert widths[0] >= widths[1] >= widths[2], f"柱子长度应随温度递减，实际 {widths}"
    assert widths[0] == report.BAR_WIDTH, "最高温那天应撑满柱状图宽度"


# ---------------------------------------------------------------------- CLI
@allure.epic("KairoWeather 测试工程")
@allure.feature("天气简报 CLI")
@allure.story("CLI 端到端")
def test_main_prints_report_and_fails_cleanly_on_missing_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], workdir: Path
) -> None:
    """CLI 端到端：正常时 stdout 有完整报表；文件不存在时干净失败、不输出半截报表。"""
    monkeypatch.setattr(sys, "argv", ["weather_report_v2.py", "--file", str(FIXTURE)])
    report.main()
    out = capsys.readouterr().out
    assert "最高温" in out and "█" in out

    missing = workdir / "nope.json"
    monkeypatch.setattr(sys, "argv", ["weather_report_v2.py", "--file", str(missing)])
    with pytest.raises(SystemExit) as excinfo:
        report.main()

    # SystemExit 的 code 是非空字符串 → 进程退出码为 1，CI/shell 才能判定失败。
    assert isinstance(excinfo.value.code, str)
    assert "找不到文件" in excinfo.value.code
    assert capsys.readouterr().out == "", "失败时不应先打印半截报表"
