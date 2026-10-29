"""冒烟：5 个端点的「顺风局」契约。

**看清楚这些用例测的是什么**：mock 服务是我们自己写的，它返回什么完全由我们决定。
所以它们不是在验证和风接口，而是在验证两件事：
  1. 测试基建是通的（客户端能发请求、服务能应答、JSON 能解析）；
  2. 我们准备的 fixture 与 App 的 DTO 契约**一致**。

这也是接口测试里一个真实的陷阱：如果打桩打得比被测系统还"听话"，
用例全绿也只能证明"你的 mock 没写错"。所以 `test_scenarios.py` 专门造异常场景
（超时/500/脏 JSON/字段漂移），让用例去撞真实的失败路径，而不是只在顺风局里验证 Happy Path。

断言纪律（M3 会抽到 assertions/ 层）：
  · 契约断言：字段在不在、类型对不对 —— 这里只做这个；
  · 数值断言：`temp == "31"` —— 真实天气每天在变，打真接口必然随机红，只在自造数据时做。
"""

from __future__ import annotations

import allure
import requests

# 和风所有数值字段都返回字符串（"31" 而不是 31）。
# 断言类型能挡住"上游偷偷把类型改了"这类最难查的兼容性问题：
# App 用的是 kotlinx.serialization，类型一变就直接解析失败。
NUMERIC_FIELDS_NOW = ("temp", "feelsLike", "humidity", "pressure", "vis", "windSpeed")
NUMERIC_FIELDS_HOURLY = ("temp", "humidity", "pop", "pressure")
NUMERIC_FIELDS_DAILY = ("tempMax", "tempMin", "humidity", "uvIndex", "precip")


def _assert_http_ok(resp: requests.Response) -> dict:
    """统一断言 HTTP 层：状态码 + Content-Type，并把解析后的 body 返回给用例。"""
    # 断言消息里带上响应原文：CI 上你只能看日志，断言消息就是你的"第一现场"。
    assert resp.status_code == 200, f"期望 200，实际 {resp.status_code}，响应体：{resp.text}"
    content_type = resp.headers.get("Content-Type", "")
    assert content_type.startswith("application/json"), (
        f"Content-Type 应为 JSON，实际 {content_type!r}"
    )
    return resp.json()


def _assert_qweather_code(body: dict) -> None:
    """和风把业务码放在 body.code 里：HTTP 200 也可能是业务失败（如 code=404 无此城市）。"""
    assert body.get("code") == "200", f"业务码异常：{body}"


def _assert_string_fields(payload: dict, fields: tuple[str, ...]) -> None:
    for field in fields:
        assert field in payload, f"缺少字段 {field}（App 的 DTO 依赖它）"
        assert isinstance(payload[field], str), (
            f"{field} 应为字符串，实际 {type(payload[field]).__name__}，值={payload[field]!r}"
        )


@allure.epic("KairoWeather 接口自动化")
@allure.feature("接口冒烟")
@allure.story("实时天气")
@allure.severity(allure.severity_level.BLOCKER)
def test_now_returns_200_and_matches_dto_contract(client) -> None:
    """实时天气：HTTP 200 + 业务码 200 + App 依赖的字段都在且类型正确。"""
    body = _assert_http_ok(client.get_now(location="101010100"))  # 101010100 = 北京
    _assert_qweather_code(body)

    now = body["now"]
    _assert_string_fields(now, NUMERIC_FIELDS_NOW + ("icon", "text", "obsTime"))


@allure.epic("KairoWeather 接口自动化")
@allure.feature("接口冒烟")
@allure.story("逐小时预报")
@allure.severity(allure.severity_level.CRITICAL)
def test_hourly_returns_list_of_forecasts(client) -> None:
    """逐小时：列表非空 + 每项字段类型正确。"""
    body = _assert_http_ok(client.get_hourly(location="101010100", hours="24h"))
    _assert_qweather_code(body)

    hourly = body["hourly"]
    assert isinstance(hourly, list) and hourly, "hourly 应为非空数组"
    for item in hourly:
        # fxTime 是带时区偏移的 ISO8601，App 用它判断"当前小时"。
        _assert_string_fields(item, NUMERIC_FIELDS_HOURLY + ("fxTime", "icon", "text"))


@allure.epic("KairoWeather 接口自动化")
@allure.feature("接口冒烟")
@allure.story("逐天预报")
@allure.severity(allure.severity_level.CRITICAL)
def test_daily_returns_list_of_forecasts(client) -> None:
    """逐天：列表非空 + 日期格式是 YYYY-MM-DD（App 用 substring/take(2) 取日期）。"""
    body = _assert_http_ok(client.get_daily(location="101010100", days="7d"))
    _assert_qweather_code(body)

    daily = body["daily"]
    assert isinstance(daily, list) and daily, "daily 应为非空数组"
    for item in daily:
        _assert_string_fields(item, NUMERIC_FIELDS_DAILY + ("fxDate", "iconDay", "textDay"))
        # 曾经的真实缺陷：fxDate 缺失时 App 用 substring(0,2) 越界崩溃。
        # 契约层先守住格式，UI 层再做防御（take(2)）。
        assert len(item["fxDate"]) == 10 and item["fxDate"][4] == "-", (
            f"fxDate 应为 YYYY-MM-DD，实际 {item['fxDate']!r}"
        )


@allure.epic("KairoWeather 接口自动化")
@allure.feature("接口冒烟")
@allure.story("城市查询")
@allure.severity(allure.severity_level.NORMAL)
def test_geo_returns_city_list(client) -> None:
    """城市查询：location 数组 + id/name/lat/lon 供换城与展示使用。"""
    body = _assert_http_ok(client.lookup_city(location="北京"))
    _assert_qweather_code(body)

    locations = body["location"]
    assert isinstance(locations, list) and locations, "location 应为非空数组"
    for item in locations:
        _assert_string_fields(item, ("id", "name", "lat", "lon"))
        # 坐标是字符串且经度在前——换城请求会把它原样拼进 location 参数。
        assert isinstance(float(item["lat"]), float)
        assert isinstance(float(item["lon"]), float)


@allure.epic("KairoWeather 接口自动化")
@allure.feature("接口冒烟")
@allure.story("空气质量")
@allure.severity(allure.severity_level.CRITICAL)
def test_air_returns_cn_mee_index(client) -> None:
    """空气质量（新版端点）：没有 code 字段，indexes 里至少有一条中国标准 cn-mee。"""
    body = _assert_http_ok(client.get_air(latitude="39.90499", longitude="116.40529"))
    # 注意：air 响应没有 body.code —— HTTP 状态码即结果，别照抄旧端点的断言。
    assert "code" not in body, f"新版空气质量响应不该再有 code 字段：{body}"

    indexes = body["indexes"]
    assert isinstance(indexes, list) and indexes, "indexes 应为非空数组"
    cn_mee = [i for i in indexes if i.get("code") == "cn-mee"]
    assert len(cn_mee) == 1, f"应恰有一条 cn-mee 标准，实际 {[i.get('code') for i in indexes]}"

    index = cn_mee[0]
    assert isinstance(index["aqi"], (int, float)), "aqi 应为数字（新版接口不再是字符串）"
    assert isinstance(index["category"], str) and index["category"], "category 不能为空"
    # health 在部分标准/等级下可能缺失（fixture 里的 us-epa 就是），所以这里只断言 cn-mee 有。
    assert "health" in index, "cn-mee 应带 health 建议（AQI 卡要用）"
