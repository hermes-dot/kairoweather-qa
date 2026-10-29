"""真实接口回归：默认不跑，只在显式 ``-m live`` 或手动 CI job 中执行。

为什么默认排除：
  · 和风免费额度按天计算，每次提交都打真实接口会把配额烧光；
  · 网络抖动、限流、上游发布都会让结果不稳定，门禁会被无关失败拖红；
  · mock 用例负责「每次提交都跑」，live 用例负责「发布前/手动确认上游还认这套契约」。

运行前提：
  1. 设置环境变量 ``QWEATHER_API_KEY``（不要把 key 写进仓库）；
  2. 可选设置 ``QWEATHER_API_BASE_URL``，默认 ``https://devapi.qweather.com``；
  3. 命令：``uv run pytest -m live -q``。

这 5 条只做契约级断言，不断言具体温度/空气质量数值——真实天气每天在变，
数值断言必然随机红；数值正确性由 mock 场景和 CLI 单测保证。
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import allure
import pytest
import requests

from api.client import LIVE_TIMEOUT, WeatherClient

# 整个模块都打上 live 标记：默认的 `-m "not live"` 会整组排除。
pytestmark = pytest.mark.live

BEIJING = "101010100"
BEIJING_LAT = "39.90499"
BEIJING_LON = "116.40529"

# 和风所有数值字段都是字符串；这里只列 App DTO 真正依赖的字段。
NUMERIC_FIELDS_NOW = ("temp", "feelsLike", "humidity", "pressure", "vis", "windSpeed")
NUMERIC_FIELDS_HOURLY = ("temp", "humidity", "pop", "pressure")
NUMERIC_FIELDS_DAILY = ("tempMax", "tempMin", "humidity", "uvIndex", "precip")


@pytest.fixture(scope="module")
def live_client() -> Iterator[WeatherClient]:
    """真实接口客户端：没有 key 时整组 skip，而不是报一个看不懂的 401。"""
    api_key = os.environ.get("QWEATHER_API_KEY", "").strip()
    if not api_key:
        pytest.skip("未设置 QWEATHER_API_KEY，跳过 live 用例（这是默认行为）")

    base_url = os.environ.get("QWEATHER_API_BASE_URL", "https://devapi.qweather.com").strip()
    client = WeatherClient(base_url=base_url, api_key=api_key, timeout=LIVE_TIMEOUT)
    try:
        yield client
    finally:
        client.close()


def _assert_http_ok(resp: requests.Response) -> dict:
    """真实接口也要先看 HTTP 层，再看业务层；失败信息带响应原文。"""
    assert resp.status_code == 200, f"期望 200，实际 {resp.status_code}，响应体：{resp.text[:500]}"
    assert resp.headers.get("Content-Type", "").startswith("application/json"), (
        f"Content-Type 应为 JSON，实际 {resp.headers.get('Content-Type')!r}"
    )
    return resp.json()


def _assert_string_fields(payload: dict, fields: tuple[str, ...]) -> None:
    for field in fields:
        assert field in payload, f"缺少字段 {field}（App 的 DTO 依赖它）"
        assert isinstance(payload[field], str), (
            f"{field} 应为字符串，实际 {type(payload[field]).__name__}，值={payload[field]!r}"
        )


@allure.epic("KairoWeather 接口自动化")
@allure.feature("真实接口回归")
@allure.story("实时天气")
@allure.severity(allure.severity_level.BLOCKER)
def test_live_now_matches_app_contract(live_client: WeatherClient) -> None:
    """真实 /v7/weather/now 仍返回 App 依赖的字段与字符串类型。"""
    body = _assert_http_ok(live_client.get_now(BEIJING))
    assert body.get("code") == "200", f"业务码异常：{body}"

    now = body["now"]
    _assert_string_fields(now, NUMERIC_FIELDS_NOW + ("icon", "text", "obsTime"))


@allure.epic("KairoWeather 接口自动化")
@allure.feature("真实接口回归")
@allure.story("逐小时预报")
@allure.severity(allure.severity_level.CRITICAL)
def test_live_hourly_matches_app_contract(live_client: WeatherClient) -> None:
    """真实 /v7/weather/24h 仍返回非空 hourly 数组。"""
    body = _assert_http_ok(live_client.get_hourly(BEIJING, hours="24h"))
    assert body.get("code") == "200", f"业务码异常：{body}"

    hourly = body["hourly"]
    assert isinstance(hourly, list) and hourly, "hourly 应为非空数组"
    _assert_string_fields(hourly[0], NUMERIC_FIELDS_HOURLY + ("fxTime", "icon", "text"))


@allure.epic("KairoWeather 接口自动化")
@allure.feature("真实接口回归")
@allure.story("逐天预报")
@allure.severity(allure.severity_level.CRITICAL)
def test_live_daily_matches_app_contract(live_client: WeatherClient) -> None:
    """真实 /v7/weather/7d 仍返回非空 daily 数组，日期格式未被改掉。"""
    body = _assert_http_ok(live_client.get_daily(BEIJING, days="7d"))
    assert body.get("code") == "200", f"业务码异常：{body}"

    daily = body["daily"]
    assert isinstance(daily, list) and daily, "daily 应为非空数组"
    for item in daily:
        _assert_string_fields(item, NUMERIC_FIELDS_DAILY + ("fxDate", "iconDay", "textDay"))
        assert len(item["fxDate"]) == 10 and item["fxDate"][4] == "-", (
            f"fxDate 应为 YYYY-MM-DD，实际 {item['fxDate']!r}"
        )


@allure.epic("KairoWeather 接口自动化")
@allure.feature("真实接口回归")
@allure.story("城市查询")
@allure.severity(allure.severity_level.NORMAL)
def test_live_geo_matches_app_contract(live_client: WeatherClient) -> None:
    """真实 /geo/v2/city/lookup 仍能按城市名返回可用的 id 与坐标。"""
    body = _assert_http_ok(live_client.lookup_city("北京"))
    assert body.get("code") == "200", f"业务码异常：{body}"

    locations = body["location"]
    assert isinstance(locations, list) and locations, "location 应为非空数组"
    for item in locations:
        _assert_string_fields(item, ("id", "name", "lat", "lon"))
        # 坐标必须是可解析的十进制字符串，换城请求会原样拼进 location 参数。
        float(item["lat"])
        float(item["lon"])


@allure.epic("KairoWeather 接口自动化")
@allure.feature("真实接口回归")
@allure.story("空气质量")
@allure.severity(allure.severity_level.CRITICAL)
def test_live_air_matches_app_contract(live_client: WeatherClient) -> None:
    """真实 /airquality/v1/current 仍返回 cn-mee 标准，且路径参数顺序正确。"""
    body = _assert_http_ok(live_client.get_air(BEIJING_LAT, BEIJING_LON))
    # 新版 air 没有 body.code，HTTP 200 即成功——不要照抄旧端点的 code 断言。
    assert "code" not in body, f"新版空气质量响应不该再有 code 字段：{body}"

    indexes = body["indexes"]
    assert isinstance(indexes, list) and indexes, "indexes 应为非空数组"
    cn_mee = [item for item in indexes if item.get("code") == "cn-mee"]
    assert len(cn_mee) == 1, f"应恰有一条 cn-mee，实际 {[i.get('code') for i in indexes]}"
    assert isinstance(cn_mee[0]["aqi"], (int, float)), "aqi 应为数字"
    assert isinstance(cn_mee[0]["category"], str) and cn_mee[0]["category"], "category 不能为空"
