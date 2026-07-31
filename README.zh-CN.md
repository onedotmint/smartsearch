# smart-search

简体中文 | [English](README.md)

面向 AI agent 和终端用户的 CLI-first、skill-driven 网页调研工具。`smart-search` 把实时搜索、来源发现、网页抓取、站点结构发现、provider 诊断、离线 Deep Research 规划和实时 Deep Research 执行统一成一组可复用命令。

`smart-search` 是普通 CLI，不是 MCP Server。AI 工具可以安装内置的 `smart-search-cli` skill；脚本和终端用户直接调用同一个 `smart-search` 命令。

## 安装

```sh
npm install -g @onedotmint/smart-search@latest
smart-search --version
smart-search setup
```

npm 包会在安装时创建隔离的 Python 运行时。源码 checkout 也支持直接使用 Python，见[入门指南](docs/getting-started.md)。

根级 `--help` 有意只显示 `search`、`fetch`、`capabilities` 和 `setup`。使用 `smart-search --help-all` 可发现 Advanced、provider、developer、experimental 和 legacy-compatible 命令。

前置条件：

- 使用 npm 包需要 Node.js 18 或更高版本。
- 直接运行源码需要 Python 3.10 或更高版本。
- 要运行目标命令，至少配置该命令需要的 provider。

## 第一次运行

第一次调用 provider 前先运行本地 readiness 检查：

```sh
smart-search doctor status --format json
```

只有需要显式联网聚合连通性检查时，才运行 `smart-search doctor probe --format markdown`。

运行一次实时搜索：

```sh
smart-search search "latest Python release" --format json
```

需要逐页核验时，抓取原始页面：

```sh
smart-search fetch "https://www.python.org/downloads/" --format markdown
```

本地版本检查的输出是确定的：

```text
$ smart-search --version
smart-search 0.1.0
```

搜索响应使用带版本号的 JSON envelope。provider 返回的正文和 URL 会变化；稳定结构是 `schema_version`、`command`、`data` 和 `meta`。

### 可选的 v2 Core JSON API

Evidence-first Core API 是推荐的 Agent 默认路径，通过根级全局 flag 选择：

```sh
smart-search --schema-version 2 capabilities
smart-search --schema-version 2 search "示例查询"
smart-search --schema-version 2 fetch "https://example.com/page"
```

`map` 作为 Advanced `site_discovery` operation 提供；v2 用法见命令参考。

- v2 **仅支持 JSON**，返回 Phase 2 envelope（`status`、`operation`、`evidence`、`routing`、`attempts` 等）。
- v2 `search` 只返回 discovery candidates；不会调用 legacy `main_search`，也不接受 `--response-mode`。
- Host Agent 基于 fetched `evidence.items` 写最终回答；discovery candidates 不是 claim-level 证据。
- `capabilities` 使用 envelope-only 元操作 `capability_status`（本地只读检查，不发 Provider 网络请求）。
- `--fail-on-degraded` 与 `--trace` 仅用于 v2。不承诺 subcommand 之后的 `--schema-version` 位置。

## 选择工作流

| 需求 | 命令 | 网络行为 |
| --- | --- | --- |
| Evidence-first 发现与抓取（Agent 默认） | `smart-search --schema-version 2 search\|fetch\|capabilities` | 实时 discovery/fetch；capabilities 本地 |
| 快速 v1 回答和广泛发现 | `smart-search search QUERY` | 实时搜索，可合成答案 |
| 查看意图需要哪些 capability | `smart-search route QUERY` | 不调用搜索/fetch provider；`hybrid` 可能调用已配置的路由 endpoint |
| 阅读一个已知页面 | `smart-search fetch URL` | 实时抓取页面 |
| 先生成调研计划 | `smart-search deep QUERY` / `research plan QUERY` | 离线规划 |
| 分阶段证据调研 | `smart-search research run QUERY` | 实时发现、抓取、gap；由 Host 写答案 |
| 分阶段调研并合成 | `smart-search research run QUERY --synthesize` 或裸 `research` | 实时发现、抓取与 evidence-only 合成 |
| 本地 readiness | `smart-search doctor status` | 仅本地，不 probe |
| 联网聚合连通性 | `smart-search doctor` / `doctor probe` | 脱敏诊断和 provider 检查 |
| 单 Provider 可达性 | `smart-search provider probe PROVIDER` | 只测一个 provider/family |

`deep` / `research plan` 只做离线规划。`research run` 是面向 Agent 的证据工作流。裸 `research` 仍是兼容的合成执行路径。

## 核心示例

```sh
# Agent 默认证据路径
smart-search --schema-version 2 capabilities
smart-search --schema-version 2 search "React useEffect cleanup docs"
smart-search --schema-version 2 fetch "https://react.dev/reference/react/useEffect"

# 分阶段多源调研，不自动合成答案
smart-search research run "比较两个当前 API 设计" --format json

# 先规划，再兼容合成执行
smart-search research plan "比较两个当前 API 设计" --budget standard --format json
smart-search research "比较两个当前 API 设计" --budget deep --format markdown

# 先本地 readiness，再显式联网检查
smart-search doctor status --format json
smart-search doctor probe --format markdown
smart-search provider probe exa --format json

# 本地 provider 元数据和有序备用模型路由
smart-search provider status --format json
smart-search provider routes list --format markdown

# 兼容入口仍可使用
smart-search deep "比较两个当前 API 设计" --budget standard --format json
smart-search model list --format markdown
```

给 agent 和脚本用 `--format json`，给人读报告用 `--format markdown`，终端快速阅读用 `--format content`。参数和 provider 专用命令见[命令参考](docs/commands.md)。

需要多个模型服务按顺序备用时，在配置文件中加入 `SMART_SEARCH_MODEL_ROUTES` JSON 数组，或用 `smart-search model add` 追加。`smart-search model list` 查看顺序和模型，`smart-search model current` 查看当前首选路由，`smart-search model remove ROUTE_ID` 删除路由。查看命令会遮蔽 API key，原有 `XAI_*` 和 `OPENAI_COMPATIBLE_*` 配置仍可继续使用。

## 证据边界

搜索结果只是待发现来源。涉及高风险事实时，先抓取相关页面，再引用抓取文本。没有 fetch 的来源标为未验证候选。完整规则见[证据策略](docs/concepts/evidence.md)。

## 文档

- [入门指南](docs/getting-started.md)：安装、setup、第一次调用和 skill 安装。
- [命令参考](docs/commands.md)：命令、alias、通用参数和输出格式。
- [Provider 指南](docs/providers.md)：能力、兜底边界、API key 和最低配置。
- [Search、Deep Research 和 Research](docs/concepts/search-vs-deep-vs-research.md)：规划器和执行器合约。
- [证据策略](docs/concepts/evidence.md)：发现、抓取、引用和缺口。
- [路由](docs/concepts/routing.md)：意图模式、远程路由调用和可观测字段。
- [开发指南](https://github.com/onedotmint/smartsearch/blob/main/docs/development.md)：验证、打包和发布通道。
- [贡献指南](https://github.com/onedotmint/smartsearch/blob/main/CONTRIBUTING.md)：源码修改、文档同步和 PR 要求。

公开的 AI agent 合约维护在[仓库 skill 目录](https://github.com/onedotmint/smartsearch/tree/main/skills/smart-search-cli)。打包副本会随 Python runtime 发布，两份内容必须保持同步。

## 排障

```sh
smart-search doctor status --format json
smart-search doctor --format markdown
smart-search provider probe exa --format json
smart-search diagnose openai-compatible --format markdown
smart-search regression
smart-search smoke --mock --format json
```

`doctor status` 仅做本地 readiness。`doctor` / `doctor probe` 是联网聚合诊断。`provider probe PROVIDER` 检查一个指定 provider。`provider list` 和 `provider status` 仍是仅本地的元数据与资格视图。

## 开发验证

源码 checkout 的验证和发布说明见[开发指南](https://github.com/onedotmint/smartsearch/blob/main/docs/development.md)。最小验证集如下：

```sh
python -m compileall -q src tests
python -m pytest tests -q
python -m smart_search.cli regression
python -m smart_search.cli smoke --mock --format json
npm test
npm pack --dry-run
git diff --check
```

Windows 如果 `python` 不在 `PATH`，改用 `py -3`。

## License

MIT
