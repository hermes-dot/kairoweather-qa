"""被测接口的客户端封装。

为什么要有这一层，而不是在用例里直接写 requests.get(...)：

1. **会话复用**：requests.Session 会复用 TCP 连接。这和 App 里 OkHttp 的连接池是同一个思路
   ——接口用例动辄几十条，每条都新建连接会让整个套件慢得肉眼可见。

2. **变化点收敛到一个地方**：鉴权头怎么加、超时多少、URL 怎么拼，只在这里写一次。
   哪天和风把鉴权从 header 挪到 query 参数，改这一个文件，用例一行都不用动。

3. **用例只表达意图**：用例应该读起来像「拿北京的实时天气，检查契约」，
   而不是「GET https://... 带 X-QW-Api-Key 头」——后者的细节每写一次就多一个出错机会。

**关键设计：base_url 由外部注入。** 同一套用例既能打本地 mock 也能打真实接口，
用例自己不需要知道在打谁。这是「被测环境可切换」的前提，也是 M4 接 CI 时才不会痛的原因。

**M2 新增：scenario 参数。** 只有打 mock 时才传（如 ``scenario="timeout"``），
它会被拼进 query string，由 mock 服务选择对应分支；打真实接口的 live 用例绝不传。
把它放在请求上而不是服务端全局状态里，是为了让每条用例自带意图、服务端保持无状态——
否则用例 A 切到 timeout 忘了切回，用例 B 就会莫名超时，典型的「单独跑绿、一起跑红」。
"""

from __future__ import annotations

import requests

# App 里的 OkHttp 设的是 connect/read 各 30s（见 di/NetworkModule.kt:36-37）。
# 但测试里我故意用更短的 5s，理由是两者优化目标相反：
#   · App 面向真实用户的弱网，宁可多等也不轻易失败；
#   · 测试要**快速失败**——挂 30s 只会把 CI 拖到超时，而且失败信息还不如「超时」本身有用。
# 打真实接口的 live 用例会单独放宽到 30s，与 App 对齐。这个差异是刻意的，不是抄漏了。
DEFAULT_TIMEOUT = 5.0
LIVE_TIMEOUT = 30.0


class WeatherClient:
    """和风天气 API 的薄封装（只做「发出去、拿回来」，不做解析也不抛业务异常）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str = "test-key",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        # 去掉结尾的斜杠，避免拼出 //v7/weather/now 这种路径（有的网关会因此 404）
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        # 鉴权头只在这里设一次；头名与 App 的 OkHttp 拦截器一致（X-QW-Api-Key）。
        # 默认值 "test-key" 是刻意的：**用例不应该依赖真实密钥**，
        # 打 mock 时服务端根本不校验它，打真接口时才通过环境变量注入真 key。
        self.session.headers.update(
            {
                "X-QW-Api-Key": api_key,
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------ 端点
    # 参数与 remote/WeatherApiService.kt 一一对应，包括 lang=zh 这个默认值：
    # 接口测试如果和 App 请求参数不一致，就会出现「用例绿、App 挂」的假安心。

    def get_now(
        self,
        location: str,
        lang: str = "zh",
        scenario: str | None = None,
    ) -> requests.Response:
        """实时天气：GET /v7/weather/now?location=&lang="""
        return self._get(
            "/v7/weather/now",
            {"location": location, "lang": lang},
            scenario,
        )

    def get_hourly(
        self,
        location: str,
        hours: str = "24h",
        lang: str = "zh",
        scenario: str | None = None,
    ) -> requests.Response:
        """逐小时预报：GET /v7/weather/{24h|72h|168h}"""
        # 端点本身只有三档，传别的值会直接 404——这正好可以用来测参数边界。
        return self._get(
            f"/v7/weather/{hours}",
            {"location": location, "lang": lang},
            scenario,
        )

    def get_daily(
        self,
        location: str,
        days: str = "7d",
        lang: str = "zh",
        scenario: str | None = None,
    ) -> requests.Response:
        """逐天预报：GET /v7/weather/{3d|7d|10d|15d|30d}"""
        return self._get(
            f"/v7/weather/{days}",
            {"location": location, "lang": lang},
            scenario,
        )

    def lookup_city(
        self,
        location: str,
        number: int = 10,
        lang: str = "zh",
        scenario: str | None = None,
    ) -> requests.Response:
        """城市查询：GET /geo/v2/city/lookup

        ``location`` 接受三种值：城市名、LocationID、``经度,纬度``（经度在前）。
        同一个端点既做反查又做搜索，差异只在参数——所以客户端只暴露一个方法。
        """
        return self._get(
            "/geo/v2/city/lookup",
            {"location": location, "number": number, "lang": lang},
            scenario,
        )

    def get_air(
        self,
        latitude: str,
        longitude: str,
        lang: str = "zh",
        scenario: str | None = None,
    ) -> requests.Response:
        """实时空气质量：GET /airquality/v1/current/{latitude}/{longitude}

        ⚠️ 新版端点是**路径参数**且**纬度在前**（旧 /v7/air/now 已下线）。
        这个顺序是 App 真实踩过的坑，用例里要专门守一条。
        """
        return self._get(
            f"/airquality/v1/current/{latitude}/{longitude}",
            {"lang": lang},
            scenario,
        )

    # ------------------------------------------------------------------ 内部
    def _get(
        self,
        path: str,
        params: dict[str, object],
        scenario: str | None,
    ) -> requests.Response:
        """统一发请求。scenario 只在显式传入时拼接，保证 live 用例的请求与 App 完全一致。"""
        if scenario is not None:
            params = {**params, "scenario": scenario}
        return self.session.get(
            f"{self.base_url}{path}",
            params=params,
            timeout=self.timeout,
        )

    def close(self) -> None:
        """显式关闭会话。放在 fixture 的 teardown 里调用，避免连接泄漏。"""
        self.session.close()
