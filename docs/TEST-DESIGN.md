# 测试设计说明

> 这份文档回答「为什么是这些用例」，而不是逐条解释代码。代码在 `tests/`，运行方式见 README。

## 1. 测试金字塔与本项目的位置

```text
        UI 自动化（未做，属于 KairoWeather 主仓库后续）
      ─────────────────────────────────────
        接口 / 契约 / 场景（本仓库的主战场）
      ─────────────────────────────────────
        纯函数单测（CLI 的 load/analyze/render/to_float）
```

接口层比 UI 层稳定、比纯函数更接近真实链路，所以在「单测已有、UI 测试成本高」的阶段，
先把接口契约和异常路径铺满是性价比最高的选择。

## 2. 被测对象

App 实际使用的 5 个和风端点：

| 端点 | 用途 | 关键契约 |
|---|---|---|
| `GET /v7/weather/now` | 实时天气 | `code=200`；`now` 的数值字段都是字符串 |
| `GET /v7/weather/{hours}` | 逐小时 | `24h/72h/168h`；`hourly` 非空数组；`fxTime` 带时区 |
| `GET /v7/weather/{days}` | 逐天 | `3d/7d/10d/15d/30d`；`fxDate=YYYY-MM-DD` |
| `GET /geo/v2/city/lookup` | 城市查询 | `location` 数组；`id/name/lat/lon` 为字符串 |
| `GET /airquality/v1/current/{lat}/{lon}` | 空气质量 | 无 `code`；`indexes` 里有且仅有一条 `cn-mee` |

## 3. 12 个异常场景

| scenario | HTTP | 真实世界谁造成 | 客户端应表现 | 对应用例 |
|---|---|---|---|---|
| `ok` | 200 | 正常 | 200 + 正常 JSON | `test_smoke.py` 5 条 |
| `timeout` | 不响应 | 弱网/上游卡死 | `ReadTimeout` | `test_timeout_surfaces_as_read_timeout` |
| `http500` | 500 | 上游故障 | 原样 500，不吞 | `test_http_500_is_returned_as_is` |
| `http502` | 502 | 网关挂了 | 502，非 JSON | `test_http_502_returns_gateway_error` |
| `dirty_json` | 200 | 代理插错误页/写一半 | `JSONDecodeError` | `test_dirty_json_is_preserved_verbatim` |
| `code204` | 200 | 和风「成功但无数据」 | `body.code=204`，不是失败 | `test_code_204_is_http_200_with_business_code` |
| `http204` | 204 | 另一种无数据实现 | 204 且 body 为空 | `test_http_204_has_no_body` |
| `deprecated403` | 403 | 旧端点下线 | 403 + body 含 `Deprecated` | `test_deprecated_endpoint_returns_403_with_marker` |
| `unauthorized401` | 401 | Key 错/缺失 | 401，先查凭证 | `test_unauthorized_401_is_distinguishable` |
| `rate429` | 429 | 超限 | 429，按退避策略处理 | `test_rate_limited_429_is_reported` |
| `type_drift` | 200 | 上游偷改类型 | 契约破坏（`temp` 变数字） | `test_type_drift_violates_string_contract` |
| `missing_field` | 200 | 上游删字段 | 字段缺失，UI 少一行 | `test_missing_field_is_detectable` |

mock 自身还有 4 条护栏：未知场景 400、mutation 场景用错端点 400、`scenario` 确实随请求发出、
空气质量路径纬度在前。没有这些护栏，用例可能在「mock 永远走 ok」的情况下假绿。

## 4. `/v7/weather/now` 的 10 条用例矩阵

| # | 场景 | 输入 | 期望 | 设计方法 | 防什么 |
|---|---|---|---|---|---|
| 1 | 正常 LocationID | `101010100` | 200 + `code=200` | 等价类 | 顺风局 |
| 2 | 城市名 | `北京` | 200 | 等价类 | 参数形态 |
| 3 | 坐标 | `116.41,39.92` | 200 | 等价类 | 经度在前 |
| 4 | 空 location | `""` | 400/204 | 边界值 | 参数校验 |
| 5 | 非法 location | `asdfgh` | 204 或 400 | 错误推测 | 无数据 |
| 6 | 超时 | `scenario=timeout` | `ReadTimeout` | 异常场景 | 弱网 |
| 7 | 500 | `scenario=http500` | 500 原样 | 异常场景 | 上游故障 |
| 8 | 脏 JSON | `scenario=dirty_json` | `JSONDecodeError` | 异常场景 | 代理错误页 |
| 9 | 类型漂移 | `scenario=type_drift` | 契约红 | 契约 | 上游偷改类型 |
| 10 | 403 下线 | `scenario=deprecated403` | 403 + `Deprecated` | 错误推测 | 旧端点 |

真实接口只做 1/6/7/8/10 的「契约级」验证，不做 2–5 的破坏性输入——那会消耗配额，且可能被
上游风控。mock 负责可控的坏输入，live 负责「上游还认这套契约」。

## 5. fixture 的 scope 为什么这么选

| fixture | scope | 理由 |
|---|---|---|
| `mock_server` | session | 创建成本高（起线程/端口），但无状态，用例间不会互相影响 |
| `client` | session | 复用连接池，模拟真实 App 的长连接；只读 mock，不会改变服务端状态 |
| `short_timeout_client` | function | 超时会把半开连接留在池子里，复用会污染下一条用例；独立实例 + 0.2s 超时 |
| `workdir`（CLI 测试） | function | 每条用例一个独立临时目录，避免文件互相覆盖 |

选择标准只有一条：**创建成本 vs 用例之间互相污染的代价**。

## 6. 为什么 mock 用标准库而不是 Flask

- 需求只有「按路径 + 场景回固定响应」，标准库够；
- 跑在测试进程内，不需要 subprocess 管理生命周期；
- 端口传 0，系统分配，避免端口占用；
- 真正该换框架的信号是路由表变大/需要中间件/跨请求状态——到那时再换。

## 7. 为什么 live 默认不跑

| 维度 | mock 档 | live 档 |
|---|---|---|
| 网络 | 不需要 | 需要 |
| 配额 | 不消耗 | 每次请求都消耗 |
| 稳定性 | 完全可控 | 受限流/网络/上游发布影响 |
| 触发频率 | 每次 push/PR | 手动或发布前 |
| 作用 | 防止本地代码回归 | 确认上游契约仍成立 |

CI 的「假绿」防线：默认档如果一条都没选中，pytest 以退出码 5 失败，不会显示「0 passed」还变绿。

## 8. 失败信息的最低标准

1. 带**路径**：`now.temp`、`daily[2].tempMax`、`location[0].lat`；
2. 带**期望 vs 实际**：类型、值、状态码；
3. 带**响应原文**（截断到合理长度）：CI 上无法复现时，这是唯一的第一现场；
4. CLI 的 `SystemExit` 文案带「第几天 / 字段 / 原始值」，退出码非 0。

## 9. 覆盖率口径

- `source = api / assertions / mock / tools`，不含 `tests/`；
- `branch = true`，分支也计入；
- `fail_under = 90`，实测约 93%；
- `tools/weather_report.py`（v1 对照版）排除：它没有独立用例，保留是为了说明「计算与渲染混在一起」
  的重构前状态，不是生产代码。

门槛按实测值定，而不是一上来写 90% 然后天天红；等用例补到能稳定覆盖新分支时再上调。

## 10. 已知不测

- UI 自动化（Compose/Appium）：属于主 App 的测试路线；
- 性能、安全、混沌：当前项目规模不划算；
- 和风全部端点/全部错误码：只覆盖 App 真正使用的 5 个端点和 12 类高频异常。
