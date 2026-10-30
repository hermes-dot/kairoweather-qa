"""契约测试：5 个端点各有一条「字段存在 + 类型正确」的回归防线。

契约断言与数值断言的分工：
  · 契约：字段在不在、类型对不对、可空性——上游改结构时才该红；
  · 数值：``temp == "31"``——真实天气每天在变，只在自造数据上做。

这里用 ``assertions.contract.assert_contract`` 而不是 JSON Schema：
当前契约简单，helper 的中文报错能直接指出「哪个字段、期望什么、实际什么」；
等出现 oneOf/嵌套复用再引入 schema，避免为了工具而工具。
"""

from __future__ import annotations

import allure
import pytest

from assertions.contract import assert_contract


@allure.epic("KairoWeather 接口自动化")
@allure.feature("接口契约")
@allure.story("实时天气")
def test_now_contract(client) -> None:
    body = client.get_now("101010100").json()
    assert body["code"] == "200"
    assert_contract(
        body["now"],
        required=["temp", "text", "humidity", "obsTime"],
        types={
            "temp": str,
            "text": str,
            "humidity": str,
            "obsTime": str,
        },
        path="now.",
    )


@allure.epic("KairoWeather 接口自动化")
@allure.feature("接口契约")
@allure.story("逐小时预报")
def test_hourly_contract(client) -> None:
    body = client.get_hourly("101010100").json()
    assert body["code"] == "200"

    hourly = body["hourly"]
    assert isinstance(hourly, list) and hourly, "hourly 应为非空数组"
    assert_contract(
        hourly[0],
        required=["temp", "icon", "humidity"],
        types={
            "temp": str,
            "icon": str,
            "humidity": str,
        },
        path="hourly[0].",
    )


@allure.epic("KairoWeather 接口自动化")
@allure.feature("接口契约")
@allure.story("逐天预报")
@pytest.mark.parametrize("days", ["3d", "7d", "10d", "15d", "30d"])
def test_daily_contract(client, mock_server, days: str) -> None:
    body = client.get_daily("101010100", days).json()
    assert body["code"] == "200"

    daily = body["daily"]
    assert isinstance(daily, list) and daily, "daily 应为非空数组"

    # 断言天数参数真的进了请求路径：否则 get_daily 哪天漏传，mock 仍回同一份
    # 3 天数据，用例照样绿——这种静默回归只有盯住原始路径才抓得住。
    last_path = mock_server.received_paths[-1]
    assert f"/v7/weather/{days}" in last_path, f"请求路径未包含 {days}：{last_path}"

    # 逐条校验：只查 [0] 的话，后面几条字段坏了发现不了（path 会指出是第几条）。
    for i, item in enumerate(daily):
        assert_contract(
            item,
            required=["tempMax", "tempMin", "textDay"],
            types={
                "tempMax": str,
                "tempMin": str,
                "textDay": str,
            },
            path=f"daily[{i}].",
        )


@allure.epic("KairoWeather 接口自动化")
@allure.feature("接口契约")
@allure.story("城市查询")
def test_geo_contract(client) -> None:
    body = client.lookup_city("北京").json()
    assert body["code"] == "200"

    locations = body["location"]
    assert isinstance(locations, list) and locations, "location 应为非空数组"
    for i, item in enumerate(locations):
        assert_contract(
            item,
            required=["id", "name", "lat", "lon"],
            types={"id": str, "name": str, "lat": str, "lon": str},
            path=f"location[{i}].",
        )
        # 坐标必须是可解析的十进制字符串，换城请求会原样拼进 location 参数。
        float(item["lat"])
        float(item["lon"])


@allure.epic("KairoWeather 接口自动化")
@allure.feature("接口契约")
@allure.story("空气质量")
def test_air_contract(client) -> None:
    body = client.get_air("39.90499", "116.40529").json()
    # 新版 air 没有 code 字段，HTTP 200 即成功。
    assert "code" not in body, f"新版空气质量响应不该再有 code 字段：{body}"

    indexes = body["indexes"]
    assert isinstance(indexes, list) and indexes, "indexes 应为非空数组"
    cn_mee = [item for item in indexes if item.get("code") == "cn-mee"]
    assert len(cn_mee) == 1, f"应恰有一条 cn-mee，实际 {[i.get('code') for i in indexes]}"

    index = cn_mee[0]
    assert_contract(
        index,
        required=["aqi", "category", "health"],
        types={"aqi": (int, float), "category": str},
        path="cn-mee.",
    )
    assert isinstance(index["health"], dict), "health 应为对象（AQI 卡的分人群建议依赖它）"
