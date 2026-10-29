"""本地 Mock 服务：用标准库起在**测试进程内**，零依赖、随机端口。

为什么不用 Flask（这是个取舍，不是偷懒）：
  · 只需要「按路径 + 场景回一份响应」，标准库的 http.server 就够；
  · 更关键的是它跑在同一个进程里——不用 subprocess 管理子进程生命周期，
    也不会撞上「端口被占用」「服务还没起来就开始跑用例」这两类经典偶发失败；
  · 真正该换 Flask/FastAPI 的信号是：路由表变大、需要中间件、需要跨请求状态。
    M2 已经有 5 个端点 × 12 个场景，但路由仍是静态表、状态仍只在请求上，所以不换。
反过来说，市面上有 MockK/WireMock/httpretty 这类现成工具，但**能自己写 mock 服务**
在测开岗上更值钱——被测的古怪行为它都能造，而现成工具只能造它支持的那些。

**M2 的核心设计：场景走请求参数（?scenario=xxx），服务端不存状态。**
这样用例顺序、并行、失败重跑都不会互相影响；也避免了「用例 A 切到 timeout 忘了切回，
用例 B 莫名超时」这种最难查的串扰。
"""

from __future__ import annotations

import json
import re
import threading
import time
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# 响应样例放 data/fixtures/，与代码分离：
# 和风改了响应结构时，你只改 JSON，不用碰 Python 文件。
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"

# 超时场景里服务端故意睡多久。用例侧把客户端 timeout 设成比它短得多（0.2s），
# 于是「超时」只需 0.2s 就能验证，而不是真等 5s——测试要快速失败。
TIMEOUT_DELAY = 1.0

# 端点表：**顺序有意义**。`/v7/weather/now` 必须排在 `{hours}` 正则前面，
# 否则 now 会被当成小时端点，场景串到别的 fixture 上。
ENDPOINTS: list[tuple[str, re.Pattern[str], str]] = [
    ("now", re.compile(r"^/v7/weather/now$"), "now_ok.json"),
    ("hourly", re.compile(r"^/v7/weather/(?:24h|72h|168h)$"), "hourly_ok.json"),
    ("daily", re.compile(r"^/v7/weather/(?:3d|7d|10d|15d|30d)$"), "daily_ok.json"),
    ("geo", re.compile(r"^/geo/v2/city/lookup$"), "geo_ok.json"),
    ("air", re.compile(r"^/airquality/v1/current/[^/]+/[^/]+$"), "air_ok.json"),
]

# 支持的全部场景。**未知场景一律 400，而不是静默按 ok 返回**：
# 拼错 scenario 时如果你得到一份正常数据，用例会「绿得莫名其妙」，
# 这和 pytest 的 --strict-markers 是同一个思路——错误要早暴露。
SCENARIOS: frozenset[str] = frozenset(
    {
        "ok",
        "timeout",
        "http500",
        "http502",
        "dirty_json",
        "code204",
        "http204",
        "deprecated403",
        "unauthorized401",
        "rate429",
        "type_drift",
        "missing_field",
    }
)

# 依赖「改正常响应」的两个场景只对 now 开放：它是 App 首屏最依赖的端点。
# 其余端点也各造一份就该有 60 个 fixture 了，维护成本远超收益——测的是契约，不是排列组合。
MUTATION_ENDPOINTS: frozenset[str] = frozenset({"now"})


class _MockHTTPServer(ThreadingHTTPServer):
    """多存一份「收到的原始路径」，供用例断言路径参数顺序（如空气质量的纬度在前）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.received_paths: list[str] = []


@lru_cache(maxsize=32)
def _fixture_bytes(filename: str) -> bytes:
    """读样例并缓存：mock 只读不改，重复读盘没有意义。"""
    return (FIXTURES_DIR / filename).read_bytes()


class _MockHandler(BaseHTTPRequestHandler):
    # 默认是 HTTP/1.0，每条请求都要重开 TCP 连接——那会让 requests.Session 的连接复用
    # 完全失效（假"复用"）。声明 1.1 才是真的长连接。
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        """基类实现会往 stderr 打访问日志，把 pytest 的输出冲乱——这里静音。"""
        return

    def handle_one_request(self) -> None:
        """吞掉客户端主动断开导致的写失败。

        超时用例里客户端先放弃连接，BaseHTTPRequestHandler 在 ``do_GET`` 返回后
        还会 flush 一次 wfile；这一步在 ``_respond`` 的 try 之外，会由 socketserver
        打印一整段 traceback。它属于预期行为，不应该污染 pytest 日志。
        """
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def do_GET(self) -> None:  # noqa: N802 —— 标准库要求这个方法名
        parsed = urlparse(self.path)
        # 记录原始 path（含 query），M3 的「纬度在前」回归断言直接读它。
        # list.append 是原子操作，多线程下不需要额外加锁。
        self.server.received_paths.append(self.path)  # type: ignore[attr-defined]

        endpoint, fixture = self._match_endpoint(parsed.path)
        if endpoint is None:
            self._respond(404, b'{"code":"404"}')
            return

        scenario = parse_qs(parsed.query).get("scenario", ["ok"])[0]
        error = self._validate_scenario(endpoint, scenario)
        if error is not None:
            self._respond(400, error)
            return

        status, payload = self._build(endpoint, fixture, scenario)
        self._respond(status, payload)

    # ------------------------------------------------------------ 路由与场景
    @staticmethod
    def _match_endpoint(path: str) -> tuple[str | None, str | None]:
        for name, pattern, fixture in ENDPOINTS:
            if pattern.match(path):
                return name, fixture
        return None, None

    @staticmethod
    def _validate_scenario(endpoint: str, scenario: str) -> bytes | None:
        """非法场景返回 400 的错误体；合法返回 None。"""
        if scenario not in SCENARIOS:
            return json.dumps(
                {
                    "error": {
                        "status": 400,
                        "type": "UnknownScenario",
                        "title": f"未知场景 {scenario!r}，支持：{sorted(SCENARIOS)}",
                    }
                },
                ensure_ascii=False,
            ).encode()
        if scenario in {"type_drift", "missing_field"} and endpoint not in MUTATION_ENDPOINTS:
            return json.dumps(
                {
                    "error": {
                        "status": 400,
                        "type": "UnsupportedScenario",
                        "title": f"场景 {scenario} 只对 {sorted(MUTATION_ENDPOINTS)} 生效",
                    }
                },
                ensure_ascii=False,
            ).encode()
        return None

    def _build(self, endpoint: str, fixture: str, scenario: str) -> tuple[int, bytes]:
        """把「端点 + 场景」翻译成 (HTTP 状态码, 响应体)。"""
        if scenario == "timeout":
            # 睡够让客户端先超时；客户端断开后这里再写响应会抛 BrokenPipeError，
            # 由 _respond 统一吞掉。不加连接复用（Connection: close）是为了避免
            # 半开连接被 requests 的连接池捡回来，污染下一条用例。
            time.sleep(TIMEOUT_DELAY)
            self._force_close = True
            return 200, _fixture_bytes(fixture)

        if scenario == "http500":
            return 500, b'{"error":{"status":500,"title":"Internal Server Error"}}'
        if scenario == "http502":
            return 502, b"<html><body>502 Bad Gateway</body></html>"
        if scenario == "dirty_json":
            # 截断的 JSON：模拟代理插入错误页、或上游把响应写到一半断掉。
            return 200, b'{"code":"200","now":{"temp":"31"'
        if scenario == "code204":
            # 和风的语义：HTTP 200 + body.code=204，代表「成功但无数据」。
            return 200, b'{"code":"204"}'
        if scenario == "http204":
            # 另一种「无数据」实现：HTTP 204，协议规定不能带 body。
            return 204, b""
        if scenario == "deprecated403":
            # 真实踩过的坑：旧端点下线返回 403，body 里带 Deprecated——
            # 它不是「没权限」，所以只看状态码会把排查方向带偏。
            return 403, (
                b'{"error":{"status":403,"type":"https://dev.qweather.com/docs/resource/error-code/",'
                b'"title":"Deprecated API"}}'
            )
        if scenario == "unauthorized401":
            return 401, b'{"error":{"status":401,"title":"Unauthorized"}}'
        if scenario == "rate429":
            return 429, b'{"error":{"status":429,"title":"Too Many Requests"}}'

        # type_drift / missing_field：在正常响应上做最小改动，模拟上游悄悄变更契约。
        body = json.loads(_fixture_bytes(fixture))
        if scenario == "type_drift":
            # 和风所有数值字段都是字符串；这里故意改成数字，模拟上游类型变更。
            body["now"]["temp"] = int(body["now"]["temp"])
        elif scenario == "missing_field":
            body["now"].pop("humidity", None)
        return 200, json.dumps(body, ensure_ascii=False).encode()

    # ------------------------------------------------------------------ 响应
    def _respond(self, status: int, payload: bytes) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            # 必须显式带 Content-Length：HTTP/1.1 下缺了它，客户端不知道响应结束在哪，
            # 表现就是"请求发出去了但一直卡住不返回"——这类问题很难从 pytest 报错里看出来。
            self.send_header("Content-Length", str(len(payload)))
            # 超时场景强制断开：客户端已经超时放弃这条连接，复用它只会拿到坏连接。
            if getattr(self, "_force_close", False):
                self.send_header("Connection", "close")
            self.end_headers()
            # 204 按协议不能带 body；其余情况才写。
            if payload:
                self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # 客户端先超时断开是**预期行为**，不是测试失败。
            # 不吞掉的话，pytest 输出里会混进一堆无关的线程异常，干扰真正的失败信息。
            return


class MockServer:
    """上下文管理器用法：``with MockServer() as srv: srv.base_url``。"""

    def __init__(self) -> None:
        # 端口传 0 = 让操作系统挑一个空闲端口。
        # 写死 8080 是接口测试最常见的偶发失败源：本机被别的程序占了、CI 上并行任务冲突。
        self._httpd = _MockHTTPServer(("127.0.0.1", 0), _MockHandler)
        # daemon=True：主进程退出时不因为这个线程而卡住
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[0], self._httpd.server_address[1]
        return f"http://{host}:{port}"

    @property
    def received_paths(self) -> list[str]:
        """收到的原始 path 列表（含 query），供断言参数顺序/场景参数是否真的发出去了。"""
        return self._httpd.received_paths

    def __enter__(self) -> MockServer:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        # 必须调 shutdown()：serve_forever 是个死循环，不叫停的话
        # pytest 跑完所有用例也不会退出（表现为"卡在最后不动"）。
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
