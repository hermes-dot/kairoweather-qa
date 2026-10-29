r"""天气温度简报 · 第二版（加柱状图，并把「计算」和「渲染」拆开）。

用法（在仓库根目录执行）：
    uv run python tools/weather_report_v2.py --file data/fixtures/daily_ok.json

和第一版的结构差异只有一处：
    第一版：build_report 既算数又拼字符串
    第二版：analyze 只算数，render 只拼字符串

为什么要拆：算数那部分以后要被 pytest 反复测，拼字符串那部分天天改格式。
混在一起时，改一个颜色都要跑一遍测试；拆开后各自独立（W2 的测试就建立在这个结构上）。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

# 柱状图最大宽度（字符数）。改这一个数字，整张图的比例就变了。
BAR_WIDTH = 20


def load_daily(path: Path) -> list[dict]:
    """读文件 → 校验 → 返回 daily 数组。所有失败都转成中文提示 + 非零退出码。"""
    if not path.is_file():
        raise SystemExit(f"找不到文件：{path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"JSON 解析失败（第 {exc.lineno} 行第 {exc.colno} 列）：{exc.msg}") from exc

    if payload.get("code") != "200":
        raise SystemExit(f"业务码异常：{payload.get('code')!r}")

    daily = payload.get("daily")
    if not isinstance(daily, list) or not daily:
        raise SystemExit("daily 为空或缺失，没有可分析的数据")
    return daily


def to_float(value: object, field: str, index: int) -> float:
    """"26" → 26.0。脏数据要能说清：哪一天、哪个字段、原始值是什么。"""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise SystemExit(f"第 {index + 1} 天 {field} 不是数字：{value!r}") from None
    # float("nan") / float("inf") 不会抛异常，但它们不是合法温度；
    # 不在这里拦，render 里的 round(nan) 才会崩，报错也离现场更远。
    if not math.isfinite(number):
        raise SystemExit(f"第 {index + 1} 天 {field} 不是有限数字：{value!r}")
    return number


def analyze(daily: list[dict]) -> dict:
    """纯计算：输入原始 JSON 数组，输出一份「报告数据」。不拼字符串、不打印。

    返回的 dict 里全是数据，没有任何格式。这样测试只需要断言数字对不对，
    不用去解析一段带汉字和空格的文本。
    """
    rows = [
        {
            "date": str(item.get("fxDate", "?")),
            "high": to_float(item.get("tempMax"), "tempMax", index),
            "low": to_float(item.get("tempMin"), "tempMin", index),
        }
        for index, item in enumerate(daily)
    ]

    highs = [row["high"] for row in rows]
    lows = [row["low"] for row in rows]
    return {
        "rows": rows,
        "hottest": max(rows, key=lambda row: row["high"]),
        "coldest": min(rows, key=lambda row: row["low"]),
        "widest": max(rows, key=lambda row: row["high"] - row["low"]),
        "average": sum(highs + lows) / (2 * len(rows)),
    }


def _bar(value: float, low: float, high: float, width: int = BAR_WIDTH) -> str:
    """把温度映射成一根柱子。span 至少为 1，避免全周同温时除以 0。"""
    span = max(high - low, 1.0)
    filled = round((value - low) / span * width)
    return "█" * max(1, filled)  # 至少留一格，否则最冷那天看起来像“缺失”


def render(report: dict) -> str:
    """纯渲染：只把 report 变成文字。不做任何计算，也不读文件。"""
    rows = report["rows"]
    week_low = min(row["low"] for row in rows)
    week_high = max(row["high"] for row in rows)

    lines = [
        f"{rows[0]['date']} ~ {rows[-1]['date']}（{len(rows)} 天）",
        "",
        f"  最高温  {report['hottest']['high']:.0f}°C   {report['hottest']['date']}",
        f"  最低温  {report['coldest']['low']:.0f}°C   {report['coldest']['date']}",
        f"  平均温  {report['average']:.1f}°C",
        f"  最大温差 {report['widest']['high'] - report['widest']['low']:.0f}°C   "
        f"{report['widest']['date']}",
        "",
    ]
    for row in rows:
        bar = _bar(row["high"], week_low, week_high)
        lines.append(f"  {row['date']}  {row['high']:>3.0f} {bar} {row['low']:>3.0f}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="读取和风天气 daily JSON，打印带柱状图的温度简报")
    parser.add_argument("--file", required=True, type=Path, help="daily JSON 文件路径")
    args = parser.parse_args()
    print(render(analyze(load_daily(args.file))))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # 兜底：连意外异常也给人话，不甩一整屏 traceback
        sys.exit(f"未预期的错误：{exc}")
