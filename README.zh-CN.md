简体中文 | [English](README.md)

面向 AI agent 和终端用户的 CLI-first、证据优先网页调研工具。v1 是破坏性版本：公开日常命令为 `search`、`read`、`research`；`setup` 仅用于首次配置。

## 安装

```sh
npm install -g @onedotmint/smart-search@latest
smart-search setup
```

npm wrapper 需要 Node.js 18+，源码使用需要 Python 3.10+。

## 日常使用

```sh
smart-search search "latest Python release" --format json
smart-search read "https://www.python.org/downloads/" --format json
smart-search research "比较两个当前 API 设计" --format json
```

所有命令返回稳定的 v1 JSON 合约。`search` 发现候选来源，`read` 读取已知 URL 的证据，`research` 分阶段收集证据但不生成最终答案。由 host agent 根据已读取的证据写答案；搜索摘要不是证明。

`setup` 将 discovery provider 配置保存到本地，CI 仍可使用环境变量。只有实际需要时命令才会联网；打包检查和帮助命令均离线。

## Pi 工具

使用 Pi 时安装独立包：

```sh
pi install npm:@onedotmint/pi-smart-search@latest
```

它只提供 `web_search`、`web_read`、`web_research` 三个工具，并使用同一 v1 CLI 合约。

## 迁移与文档

本版本移除旧命令、envelope 和 Python facade，不提供运行时别名。请阅读[迁移指南](docs/migration.md)、[入门](docs/getting-started.md)、[命令参考](docs/commands.md)和[Provider 指南](docs/providers.md)。

详见[开发与发布指南](https://github.com/onedotmint/smartsearch/blob/main/docs/development.md)。

```sh
python3 -m compileall -q src tests
PYTHONPATH=src python3 -m pytest tests -q
npm test
npm pack --dry-run
(cd integrations/pi && npm run typecheck && npm test && npm pack --dry-run)
git diff --check
```

MIT License。
