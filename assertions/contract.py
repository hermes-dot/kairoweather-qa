"""契约断言：字段存在 + 类型正确；失败信息带路径和实际值。

为什么先写这 30 行，而不是直接上 JSON Schema：
  1. 你现在要测的契约很简单（字段在不在、类型对不对），helper 够用；
  2. 报错是中文、能直接指出"哪个字段、期望什么、实际什么"，排障成本最低；
  3. 等契约复杂到需要 oneOf/anyOf/嵌套复用，再换 schema——那时你才知道它解决了什么问题。

这不是"反工具"，是"先理解问题，再引入工具"。
"""

from __future__ import annotations

from typing import Any

# 允许传单个类型或类型元组，例如 (int, float)
ExpectedType = type | tuple[type, ...]


def assert_contract(
    payload: dict[str, Any],
    required: list[str],
    types: dict[str, ExpectedType],
    path: str = "",
) -> None:
    """检查 payload 的字段存在性与类型。

    Args:
        payload: 要检查的字典，例如 body["now"]。
        required: 必须存在的字段名列表。
        types: 需要检查类型的字段；要求这些字段同时出现在 payload 中。
        path: 报错前缀，例如 "now."，方便定位嵌套字段。
    """
    for name in required:
        assert name in payload, f"缺少字段 {path}{name}；实际字段：{sorted(payload)}"

    for name, expected in types.items():
        # types 里的字段可能没写进 required；这里兜底，避免 KeyError 掩盖真正的契约问题。
        assert name in payload, f"缺少字段 {path}{name}；实际字段：{sorted(payload)}"
        value = payload[name]
        expected_name = getattr(expected, "__name__", str(expected))
        assert isinstance(value, expected), (
            f"{path}{name} 类型错误：期望 {expected_name}，"
            f"实际 {type(value).__name__}，值={value!r}"
        )
