"""pytest 的「接线板」：跨用例共享的对象，它们的**生命周期**都在这里定义。

fixture 的 scope（作用域）是 pytest 最核心、也最容易用错的概念。
选择标准只有一条：**创建成本 vs 用例之间互相污染的代价**。

  · 创建贵 + 无状态（比如起一个 mock 服务）→ session 级，整个测试会话只建一次
  · 便宜 + 会被改脏（比如一份可变数据）    → function 级，每条用例一份新的

M1 只有 session 级的两个 fixture；M3 会补 function 级的对照组，那时对比着讲。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from api.client import WeatherClient
from mock.server import MockServer


@pytest.fixture(scope="session")
def mock_server() -> Iterator[MockServer]:
    """整个测试会话只起一次 mock 服务。

    它可以被共享，是因为它**无状态**：每个请求都返回同一份固定数据，
    用例 A 发的请求不会改变用例 B 看到的结果——"用例之间不能互相影响"这条铁律。
    """
    with MockServer() as server:
        # yield 之前 = setup，之后 = teardown。
        # 这正是 pytest fixture 与 JUnit 的 @Before/@After    的最大区别：
        # 资源获取与释放写在同一处，不会出现"@After 忘了写"导致的泄漏。
        yield server


@pytest.fixture(scope="session")
def client(mock_server: MockServer) -> Iterator[WeatherClient]:
    """被测客户端。默认指向 mock —— 所以用例默认不出网、可离线跑、CI 上稳定。

    fixture 可以依赖另一个 fixture（看这个 mock_server 参数），pytest 会按依赖顺序建好它们。
    这也是为什么不需要在用例里手写"先起服务、再建客户端"。
    """
    c = WeatherClient(base_url=mock_server.base_url)
    yield c
    c.close()  # 会话在 teardown 里关掉，用例里不用管资源


@pytest.fixture
def short_timeout_client(mock_server: MockServer) -> Iterator[WeatherClient]:
    """超时场景专用客户端（function 级，每条用例一个新的）。

    **为什么不能复用 session 级 client**：requests 在超时后会把这个连接留在池子里，
    下一次复用到它就是一条半开连接——表现为"单跑绿、全量跑偶尔红"，极难排查。
    独立实例 + 短超时（0.2s）让"超时"只花 0.2s 就能验证，不必真等 5s。
    """
    c = WeatherClient(base_url=mock_server.base_url, timeout=0.2)
    yield c
    c.close()
