# kairoweather-qa

[![QA CI](https://github.com/hermes-dot/kairoweather-qa/actions/workflows/ci.yml/badge.svg)](https://github.com/hermes-dot/kairoweather-qa/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

KairoWeather（天气通）的接口自动化测试工程：用 **pytest + requests** 覆盖和风天气 5 个端点，
用进程内 mock 造 12 类真实异常，并在 JVM 单测之外补上「接口契约」这一层。

默认档完全离线：不访问真实接口、不需要 API Key、不消耗和风日配额，CI 上可以稳定重复跑。

> 被测 App：[hermes-dot/KairoWeather](https://github.com/hermes-dot/KairoWeather)
> 本仓库是独立的 Python 工程，从 App 的 `qa/` 目录抽离，方便单独展示与接入 CI。

## 快速开始

需要 [uv](https://docs.astral.sh/uv/)（会自动选择 Python 3.12）：

```bash
git clone https://github.com/hermes-dot/kairoweather-qa.git
cd kairoweather-qa
uv sync

# 默认档：只跑 mock，离线、快速
uv run pytest -q

# 看用例名
uv run pytest -v

# 覆盖率 + Allure 原始结果
uv run pytest -q --cov --cov-report=term-missing --alluredir=allure-results

# 真实接口回归（默认不跑；需要 QWEATHER_API_KEY）
uv run pytest -m live -q -rs
```

Windows 上如果不想用 uv，也可以直接调用已建好的虚拟环境：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

> **不要用 `python -m pytest`**：本机 PATH 里的 `python` 可能指向微软商店别名（退出码 9009）。
> 用 `uv run`，或明确写 `.\.venv\Scripts\python.exe`。

## 测试分层

| 层 | 文件 | 数量 | 回答的问题 |
|---|---|---:|---|
| 冒烟 | `tests/test_smoke.py` | 5 | 5 个端点在顺风局下是否都返回 App 依赖的字段与类型 |
| 异常场景 | `tests/test_scenarios.py` | 16 | 超时、500、脏 JSON、403 下线、204 无数据时，客户端是否原样暴露 |
| 契约 | `tests/test_contracts.py` | 9 | 5 个端点的字段存在性/类型/可空性是否稳定；daily 5 档是否都覆盖 |
| CLI 工具 | `tests/test_weather_report.py` | 22 | 本地天气简报的读取、计算、渲染与错误退出是否可回归；含 NaN/Inf 回归 |
| 真实接口 | `tests/test_live.py` | 5 | 上游契约是否仍然成立；默认被 `-m "not live"` 排除 |

当前默认档 **52 passed**；另加 5 条 live（无 Key 时 skip）。覆盖率只统计被测代码/测试基建，
当前约 **93%**（`fail_under = 90`）。

## 关键设计

### 1. Mock 跑在测试进程内，而不是另起一个 Flask 服务

`mock/server.py` 用标准库 `http.server`，端口传 `0` 由系统分配，并用 `ThreadingHTTPServer`
保证 `requests.Session` 的连接复用真正生效。它和 pytest 在同一个进程里，因此没有
「子进程还没起来就开始跑」和「端口被占用」这两类经典偶发失败。

该换 Flask/FastAPI 的信号是：路由表继续变大、需要中间件、需要跨请求状态。当前是静态路由
+ 请求级场景参数，标准库足够。

### 2. 场景走请求参数（`?scenario=xxx`），服务端不存状态

用例 A 不会因为切过 `timeout` 而污染用例 B。每条用例自带意图，顺序、重跑、并行都不互相影响。
未知场景一律返回 400，而不是静默按 `ok` 返回——拼错场景名必须立刻暴露。

12 个场景：`ok / timeout / http500 / http502 / dirty_json / code204 / http204 /
deprecated403 / unauthorized401 / rate429 / type_drift / missing_field`。

### 3. 契约断言与数值断言分开

- **契约**：字段在不在、类型对不对、可空性——上游改结构时才应该红；
- **数值**：`temp == "31"`——真实天气每天在变，只在自造数据上做。

`assertions/contract.py` 先提供 40 行 helper，报错直接给出「字段路径 + 期望类型 + 实际值」。
等契约复杂到需要 `oneOf`/嵌套复用时再引入 JSON Schema，避免为了工具而工具。

### 4. live 用例默认不跑，但永远不会「假绿」

`pyproject.toml` 的 `addopts` 默认 `-m "not live"`。真实接口会限流、受网络影响、烧日配额，
它的正确位置是「手动触发的发布前回归」，不是每次提交的门禁。

CI 的离线 job 只跑 mock；需要真实回归时，在 Actions 页面手动触发 `live` job。
如果一条用例都没被选中，pytest 会以退出码 **5** 失败，而不是「零用例通过」式假绿。

### 5. 失败信息按「CI 上只有日志」来写

断言消息带响应原文、字段路径、实际值；mock 未知场景返回结构化 400；CLI 的脏数据报错带
「第几天 / 哪个字段 / 原始值」。失败时不需要把服务拉起来复现。

## 目录结构

```text
.
├─ api/client.py                 # 怎么发请求：Session、鉴权头、超时、scenario
├─ assertions/contract.py        # 契约断言 helper
├─ mock/server.py                # 进程内假服务：5 端点 × 12 场景
├─ data/fixtures/                # 5 个端点的正常响应样例（数据与代码分离）
├─ tools/                        # 天气简报 CLI（v1 对照版 + v2 拆分版）
├─ tests/                        # 用例：只表达「什么输入 → 什么期望」
├─ conftest.py                   # fixture 生命周期（session / function）
├─ pyproject.toml                # 依赖、pytest、coverage、ruff 配置
├─ .github/workflows/ci.yml      # 离线门禁 + 手动 live job
└─ docs/TEST-DESIGN.md           # 场景表、用例矩阵、设计取舍
```

四层职责的边界：`api` 只负责发出去、拿回来；`mock` 只负责造响应；`assertions` 只负责
「字段/类型」；`tests` 只负责组合它们并断言。任何一层都不替另一层做决定。

## CI 与 Allure

每次 push/PR：

1. `uv sync --frozen`（锁文件与 `pyproject.toml` 不一致会直接失败）；
2. `ruff check .`；
3. `pytest` 离线档 + 覆盖率 + JUnit XML + Allure 原始结果；
4. `if: always()` 归档 `coverage.xml`、`htmlcov/`、`allure-results/`、`reports/junit.xml`。

真实接口回归需要：

1. 仓库 `Settings → Secrets and variables → Actions` 新建 `QWEATHER_API_KEY`；
2. 可选新建仓库变量 `QWEATHER_API_BASE_URL`（不配则用 `https://devapi.qweather.com`）；
3. Actions 页面选择 `QA CI → Run workflow`。

本地看 Allure HTML（需要另装 [Allure CLI](https://allurereport.org/docs/install/)）：

```bash
uv run pytest -q --alluredir=allure-results
allure serve allure-results
```

## 这些用例守住的真实坑

- **403 不一定是没权限**：旧端点下线时和风返回 `403 + body 含 "Deprecated"`，只看状态码会把
  排查方向带到 Key/订阅上。
- **`code=204` 不是失败**：HTTP 200 + `body.code=204` 表示「成功但无数据」，和 HTTP 204 是两件事。
- **空气质量路径纬度在前**：`/airquality/v1/current/{lat}/{lon}`，写反了会拿到别的坐标的数据；
  mock 会记录原始路径，专门守这条。
- **`fxDate` 格式**：App 曾因 `substring(0, 2)` 在日期缺失时越界崩溃；契约层先守住 `YYYY-MM-DD`。

## 已知限制

- 只覆盖和风 5 个端点，不含和风全部接口；
- mock 只模拟「我们要测的契约」，不模仿和风全部行为；
- 不做 UI 自动化、性能/安全测试——那是 KairoWeather 主仓库的后续路线；
- `tools/weather_report.py` 是 v1 对照版，不纳入覆盖率（没有独立用例，保留用于说明重构前后差异）。

## License

[MIT](LICENSE)
