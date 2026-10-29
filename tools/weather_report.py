r"""读取和风天气 daily JSON，在终端打印温度简报（第一版）。

用法：
    py -3.14 weather_report.py --file ..\data\fixtures\daily_ok.json

这一版只有一个 build_report：既算数，又拼字符串。
第二版（weather_report_v2.py）会把它拆成 analyze + render，并加柱状图。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_daily(path: Path) -> list[dict]:
    """读文件 → 校验 → 返回 daily 数组。

    所有失败都转成「一行中文提示 + 非零退出码」，不把 traceback 甩给命令行用户。
    """
    if not path.is_file():
        raise SystemExit(f"找不到文件：{path}")

    try:
        # 必须显式 utf-8：Windows 默认编码是 GBK，含中文的 JSON 会乱码或直接报错。
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
    """"26" → 26.0。

    脏数据要能说清：哪一天、哪个字段、原始值是什么。
    """
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise SystemExit(f"第 {index + 1} 天 {field} 不是数字：{value!r}") from None


def build_report(daily: list[dict]) -> str:
    """纯函数：只做计算和拼字符串，不读文件、不打印。"""
    rows = []
    for index, item in enumerate(daily):
        high = to_float(item.get("tempMax"), "tempMax", index)
        low = to_float(item.get("tempMin"), "tempMin", index)
        rows.append((str(item.get("fxDate", "?")), high, low))

    dates = [row[0] for row in rows]
    highs = [row[1] for row in rows]
    lows = [row[2] for row in rows]

    # 平均温 = 所有最高温与最低温的平均；除以 2 是因为每天贡献了两个温度。
    average = sum(highs + lows) / (2 * len(rows))
    widest = max(rows, key=lambda row: row[1] - row[2])
    hottest = rows[highs.index(max(highs))]
    coldest = rows[lows.index(min(lows))]

    return "\n".join(
        [
            f"{dates[0]} ~ {dates[-1]}（{len(rows)} 天）",
            "",
            f"  最高温  {hottest[1]:.0f}°C   {hottest[0]}",
            f"  最低温  {coldest[2]:.0f}°C   {coldest[0]}",
            f"  平均温  {average:.1f}°C",
            f"  最大温差 {widest[1] - widest[2]:.0f}°C   {widest[0]}",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="读取和风天气 daily JSON，打印温度简报")
    parser.add_argument("--file", required=True, type=Path, help="daily JSON 文件路径")
    args = parser.parse_args()
    print(build_report(load_daily(args.file)))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # 兜底：连意外异常也给人话，不甩一整屏 traceback
        sys.exit(f"未预期的错误：{exc}")
