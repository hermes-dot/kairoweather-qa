"""M2 场景用例：让用例主动去撞失败路径。

为什么必须有这一组：mock 返回什么完全由我们决定，如果只测 Happy Path，
全绿也只能证明"mock 没写错"。这 12 个场景不是随便挑的——它们都是和风真实会返回的形态，
其中「403 + Deprecated」「200 + code=204」「超时」都是 App 已经踩过或专门防御过的坑。

场景怎么选：用例里传 ``scenario=...``，mock 按参数分流。服务端不存状态，
所以用例顺序、重跑、并行都互不影响。
"""

from __future__ import annotations

import allure
import pytest
import requests

BEIJING = "101010100"


# --------------------------------------------------------------------- 超时
@allure.epic("KairoWeather 接口自动化")
@allure.feature("异常场景")
@allure.story("超时")
@allure.severity(allure.severity_level.BLOCKER)
def test_timeout_surfaces_as_read_timeout(short_timeout_client) -> None:
    """服务端睡 1s、客户端 0.2s 超时 —— 超时必须以 ReadTimeout 的形式暴露。

    为什么单独用 short_timeout_client：超时会把连接留在 requests 的连接池里，
    复用它的下一条用例可能偶发失败（半开连接）。独立实例从根上避开这个串扰。
    """
    with pytest.raises(requests.exceptions.ReadTimeout):
        short_timeout_client.get_now(BEIJING, scenario="timeout")


def test_mock_server_survives_a_timed_out_request(short_timeout_client, client) -> None:
    """超时后 mock 仍然健康：证明服务端吞掉了 BrokenPipe，而不是被打崩。"""
    with pytest.raises(requests.exceptions.ReadTimeout):
        short_timeout_client.get_now(BEIJING, scenario="timeout")

    # 换正常 client 再发一次：能拿到 200 才说明服务还在。
    assert client.get_now(BEIJING).status_code == 200


# --------------------------------------------------------------- HTTP 层错误
def test_http_500_is_returned_as_is(client) -> None:
    """上游 500 不能被客户端"吞掉"或改写成 200——接口测试要看到原始状态码。"""
    resp = client.get_now(BEIJING, scenario="http500")
    assert resp.status_code == 500, f"期望 500，实际 {resp.status_code}：{resp.text}"
    assert resp.json()["error"]["status"] == 500


def test_http_502_returns_gateway_error(client) -> None:
    """网关错误页通常不是 JSON：这条守住"非 JSON 错误响应"也要能原样拿到。"""
    resp = client.get_now(BEIJING, scenario="http502")
    assert resp.status_code == 502
    assert "Bad Gateway" in resp.text
    assert not resp.text.startswith("{"), "502 不应被 mock 伪装成 JSON"


def test_dirty_json_is_preserved_verbatim(client) -> None:
    """脏 JSON：解析必须失败，但**原始文本必须还在**。

    这是 client 返回 Response 而不是 dict 的价值：排障时你要看的正是那段坏掉的原文。
    如果 client 层替用例解析，这层信息就被吃掉了。
    """
    resp = client.get_now(BEIJING, scenario="dirty_json")
    assert resp.status_code == 200
    with pytest.raises(requests.exceptions.JSONDecodeError):
        resp.json()
    assert resp.text.startswith('{"code":"200"'), f"原文被改动：{resp.text!r}"


# ------------------------------------------------------- 两种「无数据」的形态
@allure.epic("KairoWeather 接口自动化")
@allure.feature("异常场景")
@allure.story("业务无数据")
@allure.severity(allure.severity_level.CRITICAL)
def test_code_204_is_http_200_with_business_code(client) -> None:
    """和风的 204：HTTP 200 + body.code=204（成功但无数据，**不是失败**）。"""
    body = client.get_now(BEIJING, scenario="code204").json()
    assert body == {"code": "204"}
    assert "now" not in body, "无数据时不应出现 now 对象"


def test_http_204_has_no_body(client) -> None:
    """另一种「无数据」：HTTP 204。协议规定不能带 body，别和上面混成一条断言。"""
    resp = client.get_now(BEIJING, scenario="http204")
    assert resp.status_code == 204
    assert resp.content == b"", f"204 不应有响应体，实际 {resp.content!r}"


# --------------------------------------------------------------- 鉴权与限流
@allure.epic("KairoWeather 接口自动化")
@allure.feature("异常场景")
@allure.story("旧端点下线")
@allure.severity(allure.severity_level.BLOCKER)
def test_deprecated_endpoint_returns_403_with_marker(client) -> None:
    """403 + body 含 Deprecated = 旧端点下线，不是"没权限"。

    只看状态码会把排查方向带偏（去查 key/订阅），所以断言必须落到 body 关键字上。
    """
    resp = client.get_now(BEIJING, scenario="deprecated403")
    assert resp.status_code == 403
    assert "Deprecated" in resp.text, f"403 的 body 应带 Deprecated 标记：{resp.text}"


def test_unauthorized_401_is_distinguishable(client) -> None:
    """401（key 错/缺失）与 403（权限/下线）要能区分——它们的修复动作完全不同。"""
    resp = client.get_now(BEIJING, scenario="unauthorized401")
    assert resp.status_code == 401
    assert resp.json()["error"]["status"] == 401


def test_rate_limited_429_is_reported(client) -> None:
    """429 超限：CI 上稳定跑 mock 的意义就在这里——限流不该让门禁随机红。"""
    resp = client.get_now(BEIJING, scenario="rate429")
    assert resp.status_code == 429
    assert resp.json()["error"]["status"] == 429


# ----------------------------------------------------------- 上游契约悄悄变更
@allure.epic("KairoWeather 接口自动化")
@allure.feature("异常场景")
@allure.story("上游类型漂移")
@allure.severity(allure.severity_level.CRITICAL)
def test_type_drift_violates_string_contract(client) -> None:
    """上游把 temp 从 "31" 改成 31：契约被破坏，App 的 String DTO 会直接解析失败。"""
    body = client.get_now(BEIJING, scenario="type_drift").json()
    temp = body["now"]["temp"]
    assert isinstance(temp, int) and not isinstance(temp, bool), (
        f"场景没生效：temp 应为 int，实际 {type(temp).__name__}"
    )
    # 明确断言"这不是契约允许的形态"：和风所有数值字段都应是字符串。
    assert not isinstance(temp, str), "type_drift 场景应当破坏字符串契约"


def test_missing_field_is_detectable(client) -> None:
    """字段消失比字段变类型更隐蔽：UI 可能只是少显示一行，不报错。"""
    body = client.get_now(BEIJING, scenario="missing_field").json()
    assert "humidity" not in body["now"], f"场景没生效：{body['now']}"


# ------------------------------------------------------------- mock 自身的护栏
def test_unknown_scenario_is_rejected_instead_of_silently_ok(client) -> None:
    """拼错场景名必须 400，而不是静默返回正常数据。

    静默回落会让用例"绿得莫名其妙"——和 pytest 的 --strict-markers 同一个思路：
    错误配置要立刻暴露，而不是等某天发现"这条用例其实一直没测到东西"。
    """
    resp = client.get_now(BEIJING, scenario="timeuot")  # 故意拼错
    assert resp.status_code == 400
    assert "UnknownScenario" in resp.text


def test_mutation_scenario_on_unsupported_endpoint_is_rejected(client) -> None:
    """type_drift/missing_field 只对 now 生效；用错端点要 400，避免以为测到了。"""
    resp = client.get_daily(BEIJING, scenario="type_drift")
    assert resp.status_code == 400
    assert "UnsupportedScenario" in resp.text


def test_scenario_is_sent_as_query_param(client, mock_server) -> None:
    """确认场景参数真的随请求发出去了——否则 mock 永远走 ok，用例会假绿。"""
    client.get_now(BEIJING, scenario="http500")
    assert "scenario=http500" in mock_server.received_paths[-1]


def test_air_endpoint_keeps_latitude_before_longitude(client, mock_server) -> None:
    """空气质量路径必须是「纬度在前」——App 真实踩过的坑，用 mock 记录路径守住它。"""
    assert client.get_air(latitude="39.90499", longitude="116.40529").status_code == 200
    path = mock_server.received_paths[-1]
    assert "/airquality/v1/current/39.90499/116.40529" in path, (
        f"纬度/经度顺序被写反了：{path}"
    )
