# Tokei 知度 v1.0.52 架构分析与功能拓展规划

> 分析对象：`/Users/mqlsq/Workspace/AI/tokei`（fork 自 `cclank/tokei`，`main` = 上游 v1.0.52，commit `a22bcf0`）
> 分析方式：只读阅读代码与文档，未修改仓库任何文件；所有结论以代码为准，不以 README 宣传语为准。
> 证据索引：`work/evidence-index.md`（关键文件与行号清单）

---

## 0. 结论摘要

1. **Tokei 不是"一个 App + 一个脚本"，而是"一个采集器 + 两套完整 UI + 一条 Git 同步链路"**。
   `usage.30s.py`（20,736 行）是唯一的数据发现、解析、归一化、成本计算和额度查询中枢；macOS（Swift/SwiftUI）和 Windows（Tauri 2 + React 19 + TypeScript）各自实现了一套 UI，只共享 **`--json` 输出契约**，不共享视图代码。
2. **数据契约是 `usage.30s.py --json`**。Swift 的 `Usage`（`Model.swift:1352`）与 TypeScript 的 `Usage`（`windows/src/lib/types.ts:229`）都按这份 JSON 手写映射；多设备同步快照也是同一份结构。
3. **没有插件、注册表或 DSL**。新增一款工具是"贯穿式改动"：以最近接入的 Muse Code 为样本，实际触及 **约 25 个文件、80 处引用**（Python 29 处、Swift 9 个文件、Windows 12 个文件、文档 3 个；按 `grep -rn` 统计，含 import/注释）。核心逻辑集中在三个超大文件：`usage.30s.py`、`PanelView.swift`（5,181 行）、`Model.swift`（1,576 行）。
4. **额度查询不是统一抽象**。每个 Provider 都有自己的登录态来源、缓存文件、TTL、失败退避和 UI 开关；查询代码分散在 Python、macOS Swift、Windows Rust/Python 三层。新增额度来源的成本高于新增"只看本地日志"的工具。
5. **fork 的三个硬约束（最容易踩坑）**：
   - macOS 自动更新硬编码指向上游（`Updater.swift:24-27` + `UpdateSecurity.swift:142-153`），fork 继续用纯数字版本号会被提示"升级"到上游包并覆盖本地实现；
   - 远程 Linux 设备的采集器分发也指向上游（`install.sh`、`skills/tokei-setup.md`、`tokei-collector-skill.md` 都从 `dl.lanshuagent.com/tokei/usage.30s.py` 拉取；`release.sh:42` 上传到上游 R2）；
   - 采集器"只升不降"同步策略依赖脚本头 `TOKEI_COLLECTOR_REVISION`（`usage.30s.py:2`、`CollectorScriptInstaller.swift`），fork 的改动若不自增 revision 或不改分发路径，可能被上游脚本覆盖。
6. **推荐的拓展路线**：不要重写采集器核心，也不要继续在 `PanelView.swift` / `types.ts` 里堆功能。用"**fork 自有增量层 + 单点注册**"：新增独立文件承载功能，上游热文件只做一行接入；优先做 **P0 fork 安全护栏、P1 预算与提醒、P2 额度耗尽预测、P3 本地历史分析库**。这四个方向都能在不改采集器解析核心的前提下交付，且上游冲突面最小。

---

## 1. 架构全貌

### 1.1 三种运行形态

| 形态 | 入口 | 角色 | 是否需要 Python |
|---|---|---|---|
| macOS 菜单栏 App | `Tokei/Sources/Tokei/main.swift` | 完整 UI + 原生额度桥 + 同步 + 更新 | 需要系统 Python 3.8+（App 只打包脚本，不打包解释器） |
| Windows 桌面 App | `windows/src-tauri/src/main.rs` → `lib.rs` | 主窗口 + 托盘 + 快捷面板 | 安装包内置嵌入式 Python（`tauri.windows.conf.json` 的 `python/` 资源，CI 下载 embed 包） |
| 远程/无 UI 设备 | `install.sh` + crontab | 只跑采集器并写 Git 快照 | 系统 Python 3.8+ |

Linux 桌面版是 Windows 工程的第三目标（`windows/src-tauri/tauri.linux.conf.json`），复用同一套 Tauri/React 代码，但依赖系统 Python 和 `python3-zstandard`。

### 1.2 运行时拓扑

```text
┌────────────────────────── macOS（Swift 5.9 / SwiftUI / macOS 13+） ──────────────────────────┐
│ Tokei.app                                                                                     │
│  main.swift: Store / AppDelegate / NSStatusItem / 10s~30s 刷新循环                            │
│    → DataLoader.runScript() → Process(python3 -c <launcher> usage.30s.py --json)              │
│    → JSONDecoder → Usage → PanelView / DashboardView / QuotaHistoryView / WrappedView         │
│  原生额度桥：ClaudeCLIQuotaBridge（Keychain + Anthropic OAuth）                               │
│             DoubaoWorkQuotaBridge（WKWebView 持久会话）                                       │
│             GrokBotHelper（独立可执行 + Keychain/Safe Storage）                                │
│  其他：ProviderCredentialStore(Keychain) / SyncManager(git) / Updater / ActivityReporter      │
└───────────────────────────────────────┬───────────────────────────────────────────────────────┘
                                        │ 唯一契约：usage.30s.py --json
┌───────────────────────────────────────▼───────────────────────────────────────────────────────┐
│ usage.30s.py（Python 3.8+，20,736 行）                                                        │
│  路径常量 → scan_<tool>() → 日桶/模型桶 → ledger_reconcile → ranges → compute() → stdout JSON   │
│  定价：pricing.json + pricing_overrides.json + 内置兜底 + 官方核价                             │
│  额度：Codex/Cursor/Zed/Sub2API/z.ai/Antigravity/Devin/MiniMax/Kimi/QwenWork/Grok...          │
│  本地状态：~/.tokei/{scan cache, ledger.json, quota_cycles.json, *_quota_cache.json}          │
└───────────────────────────────────────┬───────────────────────────────────────────────────────┘
                                        │ Windows：Tauri collector.rs 调用内置 python.exe
┌───────────────────────────────────────▼───────────────────────────────────────────────────────┐
│ Windows（Tauri 2 + React 19 + TypeScript + Vite）                                             │
│  src/lib/store.tsx（主窗口采集，10s/30s）→ collector.ts（invoke run_collector）                │
│  src/cards/* + registry.ts（与 Mac 卡片同序）→ 主窗口 / 快捷面板 / 托盘 tooltip                 │
│  src-tauri：collector.rs / store.rs(keyring) / native_bridge.py / doubaowork_webview.rs       │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
        ┌──────── 远程 Linux / 其他 Mac：install.sh + crontab + 私有 Git 仓库同步 ────────┐
```

### 1.3 目录职责

| 路径 | 职责 | 关键约束 |
|---|---|---|
| `usage.30s.py` | 唯一采集/计算器；同时兼容 SwiftBar 无参调用（纯文本菜单） | 单文件 2 万行；所有工具硬编码；自带 `TOKEI_COLLECTOR_REVISION` |
| `Tokei/Sources/Tokei/` | macOS UI 与业务壳（32 个 Swift 文件） | `PanelView.swift` 同时承载主面板与"设置"页；`Model.swift` 是 JSON 映射 |
| `Tokei/Sources/TokeiUpdateSecurity/` | 更新元数据/下载源校验、采集器安装策略 | 域名白名单写死；`CollectorScriptInstaller` 只升不降 |
| `Tokei/Sources/CZstd/` | 内置 zstd 解压（Claude Desktop 缓存、Codex `.zst`、DSH） | 第三方 C 源码，不要改 |
| `Tokei/Sources/GrokBotBridge/` + `TokeiGrokBotHelper/` | Grok Bot 原生额度桥与独立 helper 进程 | helper 需要单独签名/打包 |
| `Tokei/Localization/` | 4 种语言词表（en/ja/ko/fr） | Windows 复用同一批中文原文 key |
| `windows/src/` | React UI（卡片、Dashboard、Projects、设置、分享图） | 与 Mac 逐卡片对齐，见 `PORTING.md` |
| `windows/src-tauri/` | Tauri 壳、Rust 命令、Windows 原生桥、Doubao WebView | 采集、凭据、窗口/托盘/电源管理 |
| `windows/native_bridge.py` | Windows 侧原生能力（Claude OAuth、DPAPI/Grok、活跃上报） | 安装包内路径 `collector/windows_native.py` |
| `pricing.json` | OpenRouter 公开目录快照（548 个模型，含 `retired`） | 由 `--update-prices` 更新；不要手写业务价格 |
| `pricing_overrides.json` | 本地覆盖：`models`（单价）+ `aliases`（别名） | 优先级最高；安装时会与内置默认值合并 |
| `CALCULATION.md` | 计算口径的权威文档（数据源、字段、账本、成本、额度、区间） | 与代码同步维护的最佳参考 |
| `CONTEXT.md` | 领域术语（Tool / Session / Usage Event / Project / Actual Cost） | 定义"什么算一次计费" |
| `docs/` | 功能说明 + `pr-*` 设计记录 + `plans/` + `releases/` | 不是静态站点；`site/` 才是 |
| `site/` | 单文件营销落地页 `index.html` + `og.png` | `release.sh` 只改下载链接并部署到 Cloudflare Pages |
| `tests/` | 91 个 Python 单测 + 26 个 Swift `*Check.swift` + 5 个 Windows node 测试 | 新增工具必须带 fixture |
| `install.sh` / `skills/` / `tokei-collector-skill.md` | 远程设备部署与 Agent 引导 | 脚本来源指向上游 R2 |
| `.github/workflows/` | Windows/Linux 构建与测试；macOS 走本地 `release.sh` | macOS 发布不在 CI |
| `release.sh` | 本机打包 → R2 三件套 → GitHub Release → 英雄页 → 校验 | 读取 `Updater.swift` 的版本号；强依赖 wrangler/gh |

**纠正一个常见误解**：仓库里没有 `site/docs`。文档在根目录 `docs/`，站点在 `site/`，两者相互独立；`site/index.html` 不消费 `docs/`。

### 1.4 依赖方向

- `usage.30s.py` **不依赖**任何 App 代码，可被 CLI、cron、Linux、SwiftBar 独立运行。
- macOS/Windows UI **只依赖 JSON 契约**，以及少量 `~/.tokei/config.json` 开关。
- macOS 原生桥通过**环境变量**把凭据/额度注入 Python（`ProviderCredentialStore.environmentOverrides()`，`DataLoader.swift:774`），Python 不直接读 macOS Keychain。
- Windows 原生桥反向：Rust 从 Credential Manager 取 Key，作为环境变量（Linux 走 stdin 管道）传给 Python（`collector.rs:218-257`）。
- 多设备同步是"快照文件 + Swift 端合并"，不是共享数据库。

### 1.5 五类资产的关系（一句话版）

- **Python 采集脚本 = 内核**：发现/解析/去重/成本/账本/额度都在这里；没有它，两个 UI 都没有数据。
- **macOS App = 带原生能力的完整外壳**：菜单栏、原生额度桥、Keychain、Git 同步、自动更新。
- **Windows App = 同一 JSON 契约上的第二套外壳**：补了嵌入式 Python、Credential Manager、WebView、托盘，但 UI 是重新实现的，功能并不自动与 macOS 对等。
- **定价表（`pricing.json` + `pricing_overrides.json`）= 纯数据**：被采集器读取，不参与 UI；`pricing_overrides` 优先级最高，`pricing.json` 是可替换的公共目录快照。
- **`docs/` + `site/` = 文档与分发面**：`docs/` 是工程/功能说明（不被站点消费），`site/` 是发版落地页；`release.sh` 同时更新 App 包、采集器包和站点下载链接。两者都不参与产品运行时。

一句话：**采集器产出 JSON，Mac/Win 各自消费；定价表喂给采集器；docs/site 只服务于人和分发。**

---

## 2. 数据链路

### 2.1 采集入口与 CLI

`usage.30s.py` 的 `__main__` 分发（`20707-20736`）：

| 参数 | 作用 | 调用方 |
|---|---|---|
| 无参数 | SwiftBar/文本菜单输出（Claude/Codex/Gemini... 分块） | 传统菜单栏/调试 |
| `--json` | 完整用量 JSON（UI 主契约） | Mac `DataLoader`、Windows `collector.rs` |
| `--json --local-only` | 跳过网络额度查询，只算本地用量 | Windows 首屏预览 |
| `--json --no-sync-snapshot` | 不写 Git 快照 | Mac/Windows 常规刷新 |
| `--write-sync` | 只写多设备同步快照 | Mac 同步、远程 cron |
| `--dashboard` | 日成本 + 热力图 + Wrapped 聚合 | Mac/Windows Dashboard 页 |
| `--quota-detail` | 额度周期曲线 | Mac/Windows 额度曲线页 |
| `--daily-costs` / `--wrapped` / `--projects` | 分项聚合 | 分享图、项目页 |
| `--update-prices` / `--auto-update-prices` / `--update-unknown` | 价格目录与缺价估算 | 设置页/后台任务 |

### 2.2 发现（Discovery）

路径全部硬编码在文件头部的常量区（`172-305`），支持环境变量覆盖的用 `_path_candidates()`：

| 工具 | 主路径 | 格式 |
|---|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` | JSONL，assistant 行 `message.usage` |
| Codex | `~/.codex/{sessions,archived_sessions}/**/rollout-*.jsonl[.zst]` | JSONL/zstd，`token_count` 事件 |
| Gemini/Antigravity | `~/.gemini/**` | SQLite/protobuf + JSON |
| Grok Build | `${GROK_HOME:-~/.grok}/logs/unified.jsonl` + `sessions/**` | JSONL |
| WorkBuddy / Intl. / CodeBuddy | `~/.workbuddy`、`~/.workbuddy-ai`、`~/.codebuddy` | JSONL |
| Qoder 系 | `~/Library/Application Support/Qoder*`、`~/.qoder`、`~/.qoder-cn` | SQLite + JSONL |
| Hermes / ZCode / MiMoCode / OpenCode / MiniMax | 各自 SQLite | SQLite |
| OpenClaw | `~/.openclaw/state/openclaw.sqlite` + agent DB | SQLite/JSONL |
| Pi / Prime Agent / OMP | `~/.pi`、`~/.prime`、`~/.omp` | JSONL |
| DeepSeek Harness | `~/.dsh/sessions/**/*.jsonl.zstd` | 多帧 zstd |
| Qwen Code | `~/.qwen/usage/token-usage-*.jsonl` | JSONL |
| Kimi / Muse / Command / Copilot | 各自的 CLI 目录 | JSONL |
| Doubao Work | IndexedDB 缓存（Application Support/APPDATA） | 自定义 LevelDB/snappy 解析 |
| Grok Bot | `sand-client-persistence/*.blob` | JSON 快照 |

路径常量的组织方式是"按工具一段"，每段通常包含：默认目录、环境变量覆盖、候选路径列表、数据库文件名。新增工具时照抄一段即可。

### 2.3 解析与归一化成 usage event

`CONTEXT.md` 把 **Usage Event** 定义为"一条持久化 assistant 消息上的 usage"，它是 Tokei 累加的最小单位。所有工具的原始字段最终归一成五个 token 字段 + 成本：

| 字段 | 含义 | 典型来源 |
|---|---|---|
| `in` | 非缓存输入 | `input_tokens` / `prompt_tokens - cached` |
| `out` | 输出 | `output_tokens` / `completion_tokens` |
| `cr` | 缓存读 | `cache_read_input_tokens` / `cached_tokens` |
| `cw` | 缓存写 | `cache_creation_input_tokens` |
| `reason` | 推理/思考 | `reasoning_tokens` / `thoughts` |
| `cost` / `cost_cny` / `credits` | 美元成本 / 人民币成本 / 原生积分 | 日志自带或按价表估算 |

关键口径（`CALCULATION.md` §2、§3）：
- Claude 系 `input` **不含**缓存，命中率 = `cr / (cr + cw + in)`；
- Codex/Gemini 的 `input` **含**缓存，命中率 = `cached / input`，展示时输入取非缓存部分；
- CodeBuddy 的 `prompt_tokens` 含缓存，需拆出 `cr/cw`；`completion_thinking_tokens` 已包含在输出里，不重复加；
- 没有精确价格的模型不冒充美元成本（宁可显示未知/积分）。

解析模式基本统一为：

1. 列出文件；
2. 用 `mtime + size`（或 `mtime_ns + size`）做签名，命中缓存则跳过；
3. 流式读 JSONL / 只读打开 SQLite；
4. 每条记录转成 `{date, hour, in, out, cr, cw, reason, cost, model, session, line, dedup...}`；
5. 写入工具的 `file_cache`（带 `parser` 版本）；
6. 聚合到 `days` / `ranges`；
7. 通过 `ledger_reconcile(tool, live_days, source_days)` 合并历史账本；
8. 按 `RANGE_KEYS` 做区间分类（`classify_date`）。

### 2.4 去重规则

去重不是全局统一算法，而是每个工具按自己的稳定标识实现。代码中的实际做法：

| 工具 | 去重键 |
|---|---|
| Claude Code | 同文件内按 message/事件身份；增量扫描用文件签名 + 账本来源 |
| Codex | `response_id` / `token_count` 事件身份；同一 thread 的多段 rollout 按 response id 覆盖/去重；zstd 事件缓存带 `_CODEX_PARSER_VERSION` / `_CODEX_ACCOUNTING_VERSION` |
| WorkBuddy / CodeBuddy | `providerData.messageId` 优先，回退 entry `id`，再与 `sessionId + ts_key` 组合；CodeBuddy 用 messageId 的 SHA-256 |
| Qoder CLI | `request_id` 跨文件去重 |
| Grok Bot | `requestId` 去重流式响应；用户消息按 entry id；多持久化键按首尾 entry 边界 |
| Command Code | assistant `message.id` |
| 无稳定 id 的工具 | 回退 `path:line:ts_key` |

**重要**：`conversationRequestId` 之类会跨多次调用复用的 ID 明确不能作为去重键（`docs/plans/001-codebuddy-integration.md` 决策 2）。

### 2.5 持久化：扫描缓存 + 账本

| 文件 | 作用 | 细节 |
|---|---|---|
| `~/.tokei/scan_cache*.json`（分片） | 每文件解析结果、dashboard 天缓存、价格变更标记 | `_SCAN_CACHE_VERSION=21`；256 桶、512KB/桶；原子写 + 文件锁 |
| `~/.tokei/ledger.json` | 按工具/日期/来源的日快照，防止日志被清理后历史归零 | `v=1`；来源保留 45 天；`ledger_reconcile` 做高水位合并 |
| `~/.tokei/last_usage.json` | 最近一次完整 JSON（UI 首屏） | 0600；启动时 `primeCachedUsage()` |
| `~/.tokei/quota_cycles.json` | 额度周期锚点（只有观测到才知道周期边界） | 随同步快照发布 `_quota_anchors` |
| `~/.tokei/quota_history.json` | macOS/Windows 记录的额度与模型活动曲线 | Swift/TS 两侧格式完全一致 |
| `~/.tokei/*_quota_cache.json` | Codex/Claude/Grok/QwenWork/Provider 额度缓存 | 每个 Provider 独立 TTL/失败退避 |
| `~/.tokei/claude_quota_swift_cache.json` 等 | macOS 原生桥的增量扫描状态 | 避免每次全扫 Claude Desktop 缓存 |
| `~/.tokei/sync/<device>.json` | 多设备快照 | `_device` / `_ts` / `_range_bounds` / `_dashboard` / `_ledger` / `_quota_anchors` |

账本的设计意图（`CALCULATION.md` §2 "持久账本与日志清理"）：
- 同日旧会话日志被清理后，保留旧来源；
- 同一来源重复扫描不重复加总；
- 解析口径升级时只替换对应来源的旧快照；
- 旧版只有日汇总时保留"无法归属的余额"，不伪造小时/模型归属。

这是 Tokei 能在"AI 工具会删日志"的现实下保持历史连续的关键，也是新增工具时**最容易被忽略的一步**：只写 `scan_*()` 而不接 `ledger_reconcile`，历史会在日志清理后丢失。

### 2.6 成本计算

**三级/四级价格查找**（`_effective_pricing_map`，`547-566`）：

```text
内置 _DEFAULT_PRICES  <  pricing.json（OpenRouter 目录）
                      <  内置覆盖 _BUILTIN_OVERRIDE_MODELS / 官方核实 pricing_official.json
                      <  用户 pricing_overrides.json（最高）
```

实现上，`_merge_pricing_overrides()`（`514-533`）先把官方核实价并入 overrides，再由 `_effective_pricing_map()`（`547-566`）按"默认 → 目录 → overrides"逐字段合并；官方价在 `_effective_pricing_map` 里还额外提供 `long_context` 分档。

- `pricing.json`：`{_meta, models}`，每个模型 `{in, out, cache_read, cache_write, retired}`，单位是 USD / 1M token；
- `pricing_overrides.json`：`{models, aliases}`，同时内置 `_BUILTIN_OVERRIDE_MODELS` / `_BUILTIN_OVERRIDE_ALIASES`；
- `pricing_official.json`：由 `--update-prices` 的 OpenAI 官方页核价写入；
- 模型名归一化：`claude-opus-4-8 → anthropic/claude-opus-4.8`、`gpt-5.5 → openai/gpt-5.5`、去 `:free/-free` 后缀；未知模型按保守兜底或沿用同档位上一版。

**成本公式分三类**：
1. 日志自带权威成本（CodeBuddy Credit、Command Code `costUsd`、Devin、部分 Grok 请求）→ 直接保留，不按价表重算；
2. 只有 token、没有成本（Gemini、WorkBuddy、Pi、Muse、Qwen Code 等）→ `_recalc_costs()` 按价表估算；
3. 特殊计价（DeepSeek 官方人民币潮汐价、Claude 长上下文分档、Grok 按请求 200K 档）→ 在扫描阶段按请求时间/上下文精确计算，聚合后不再重算。

**自动核价**：完整采集后启动独立后台进程，通常 24 小时一次；缺价模型提前检查；价格变化写 `_pricing_changed` + 模型列表，扫描缓存按新价重算，但**不清除 Token 历史**。价格指纹（`_make_pricing_fingerprint`）包含价格、别名和长上下文规则。

### 2.7 刷新到 UI

**macOS**（`main.swift:292-415`、`DataLoader.swift`）：
1. 启动：`primeCachedUsage()` 先读 `last_usage.json`，立即出画面；
2. `store.refresh()` → 后台线程跑 Python（`runScriptRaw`，正常超时 600s）；
3. 解析 JSON（16MB 栈专用线程，`Usage.decode`）；
4. 用原生 Claude 额度覆盖脚本额度（取更新时间较新者，`DataLoader.swift:567-591`）；
5. 写回 `last_usage.json`；
6. `Store` 更新 `localUsage`，可选合并 peers，写入额度历史；
7. 菜单栏标题（`updateStatusTitle`）与面板视图刷新；
8. 定时器每 2 秒检查：面板可见 10s、不可见 30s，且间隔 ≥ 上次耗时 × 2（避免扫描慢时刷新首尾相接）。

**Windows**（`windows/src/lib/store.tsx`）：
1. 主窗口的 `UsageProvider` 负责采集；
2. 首次先读 `usage-preview` 缓存，再跑完整 `--json`；
3. `VISIBLE_INTERVAL=10s`、`IDLE_INTERVAL=30s`；
4. 快捷面板是 follower，通过 Tauri 事件订阅同一份带 revision 的快照，不重复采集；
5. 托盘 tooltip 由 `trayQuota.ts` 从同一份 Usage 计算。

**Dashboard / 额度曲线是二次调用**：`--dashboard`、`--quota-detail` 各自有缓存，不会塞进主 JSON；macOS 用 `DashboardRepository` / `QuotaDetailRepository` 预热门闩，Windows 按页面需要调用。

### 2.8 多设备同步

- 配置在 `~/.tokei/config.json`：`device_id`、`sync_dir`、`auto_sync`、`sync_interval`；
- 快照写入 `sync_dir/<device_id>.json`；
- **会同步**：各工具日用量、Dashboard 日成本、Wrapped、账本 `_ledger`、额度周期锚点 `_quota_anchors`；
- **不会同步**（`_sync_safe_usage_payload`，`17730-17754`）：Cursor/Zed/Sub2API/z.ai/Antigravity 的账号额度；Devin/MiniMax 的 quota 部分；Kimi 的额度字段；
- macOS `SyncManager.merge`（`444`）按工具类型逐字段相加；Grok Bot 官方汇总只取时间最新的一份，不按设备相加；
- 同步是 Git 仓库：`git fetch + rebase + push`，Swift 端有 busy/foreignOperation/dirtyRepository 等状态码处理。

---

## 3. 额度（Quota）查询路径

### 3.1 三种鉴权形态

| 形态 | 做法 | 代表 |
|---|---|---|
| 复用本机登录态 | 读本地缓存/凭据文件，或调用本机服务，不代刷 refresh token | Claude Desktop 缓存、Codex `auth.json`、Kimi `credentials`、Cursor 本地 DB、Antigravity 本地 language server、QwenWork 本地 MCP |
| 用户保存的 API Key | Keychain（macOS）/ Credential Manager（Windows）/ config/env；Python 只拿到 env | Sub2API、z.ai、MiniMax |
| 独立 WebView 会话 | App 自己持有一个持久网站会话，不导入其他应用 Cookie | Doubao Work |

### 3.2 全量分布

| 工具 | 登录态/凭据来源 | 查询实现所在层 | 缓存/退避 | 开关 |
|---|---|---|---|---|
| Claude | Desktop Chromium 缓存（zstd `/usage` 响应，仅 macOS 原生桥）+ 可选 CLI `~/.claude/.credentials.json` OAuth | **macOS Swift**：`DataLoader.scanClaudeQuota` + `ClaudeCLIQuotaBridge`；**Windows Python**：`native_bridge.fetch_claude_quota`（仅 CLI OAuth）；采集器只接收 `TOKEI_CLAUDE_QUOTA_JSON` | Swift 状态文件；CLI 5 分钟 / 耗尽 1 小时 / 失败退避 | `claudeCLIQuotaEnabled` |
| Codex | `~/.codex/auth.json` + config.toml | **Python**：`fetch_codex_live_limits`、`fetch_codex_reset_cards` | `codex_quota_cache.json`、`codex_reset_cards_cache.json` | 默认开 |
| Cursor | Cursor.app 本地登录态数据库 / Cookie | **Python**：`_cursor_session`、`fetch_cursor_quota` | `provider_quota_cache.json` | `cursor_quota_enabled` |
| Zed | Zed Keychain（macOS Swift 读取，禁止弹窗）→ env → Python | **Swift 取凭据 + Python 查询** | Provider cache | `zed_quota_enabled` |
| Sub2API | Keychain/WinCred/`SUB2API_API_KEY` + config base_url | **Python**：`fetch_sub2api_quota` | Provider cache | `sub2api_quota_enabled` |
| z.ai / GLM | `Z_AI_API_KEY` + region | **Python**：`fetch_zai_quota` | Provider cache | `zai_quota_enabled` |
| Antigravity | 已运行客户端的 `127.0.0.1` language server + CSRF | **Python**：`fetch_antigravity_quota` | 凭据/扫描 cache | `antigravity_quota_enabled` |
| Devin | 本地 support DB | **Python**：`fetch_devin_quota` | Provider cache | `devin_quota_enabled` |
| MiniMax | 用户填写的 Token Plan Key | **Python**：`fetch_minimax_quota` | Provider cache | `minimax_quota_enabled` |
| Kimi Code | `~/.kimi-code/credentials/kimi-code.json` 的 access_token（绝不代刷 refresh_token） | **Python**：`fetch_kimi_live_limits` | 5 分钟成功缓存；失败 5 分钟不重试 | `TOKEI_KIMI_LIVE_QUOTA` |
| QwenWork | 本地 `mcp-adaptor.config` + 已登录桌面端 MCP | **Python**：`scan_qwenwork_quota` | `qwenwork_quota_cache.json` | `qwenwork_quota_enabled` |
| Grok Build | 本地 billing 日志；可选 `~/.grok/auth.json` | **Python**：`scan_grok_quota` / `fetch_grok_live_quota` | `grok_quota_cache.json` | `grok_live_quota_enabled` |
| Grok Bot | Electron Safe Storage / Keychain / Windows DPAPI | **macOS Swift helper** + **Windows Python** `native_bridge` | helper 缓存/授权 generation | 授权 + `grok_bot` |
| Doubao Work | App 自有 WKWebView（macOS）/ WebViewWindow（Windows）持久会话 | **Swift `DoubaoWorkQuotaBridge`** / **Rust `doubaowork_webview.rs` + `doubaowork_quota.rs`** | 5 分钟静默刷新 | `doubaowork_quota_enabled` |

### 3.3 统一的新鲜度/失败策略（模式而非基类）

每个 Provider 都在自己的函数里重复实现同一套模式：
- 成功缓存 N 分钟；
- 失败/401/429 后冷却，不每轮 30 秒重试；
- 读数过期（窗口 reset 已过 / 读数太旧）标记 `*_stale`，UI 显示"已过期"而不是旧百分比；
- 账号级额度**不进入多设备同步**；
- 查询失败不影响 token 统计。

### 3.4 隐私边界（代码层面的实际约束）

- Python 采集器不直接读 macOS Keychain；凭据由 Swift/Windows 原生层取出后经 env（或 Linux stdin 管道）注入。
- `_read_provider_keys_from_stdin()` + `_PIPED_PROVIDER_KEYS` 让 Linux 上 Key 不进入子进程环境变量。
- `_sync_safe_usage_payload` 明确剔除账号额度与可能含邮箱的字段。
- QwenWork 只读 `.status.json` 的文件元数据（generation），不读账号资料。
- Doubao Work 不导入其他应用 Cookie，只在自己的持久会话里查询。

---

## 4. 扩展点：新增工具 / 指标 / 额度来源

### 4.1 新增一款工具

以 Muse Code 为样本，实际接入点如下（按建议实施顺序）。

#### A. Python 采集器（必须）

| # | 位置 | 要做的事 |
|---|---|---|
| 1 | `usage.30s.py:172-305` 路径常量区 | 加默认目录、env 覆盖、候选路径（多平台） |
| 2 | `usage.30s.py:2005-2070` `_empty_*` | 返回空 ranges（通用工具可复用 `_empty_opencode()`） |
| 3 | 新增 `scan_<tool>()` | 照 `scan_musecode`（`16018`）或 `_scan_workbuddy_root`（`12431`）：文件枚举 → 签名缓存 → 解析 → 日桶 → `ledger_reconcile` → `classify_date` |
| 4 | `compute()` `17180-17227` | `_safe_scan("<tool>", ...)` 注册 |
| 5 | `compute()` range 适配 `17363` | 通用工具用 `token_usage_range`，特殊指标另写 |
| 6 | `compute()` 输出 `17552` | `"<tool>": {"ranges": ...}` |
| 7 | `_recalc_costs()` `17577` | 只有"日志无成本、需按价表估算"时才加入；有权威成本不要加 |
| 8 | `_UNPRICED_COST_TOOLS` `1542` | 若该工具需要"价格变更后重算历史缓存" |
| 9 | `build_daily_costs` `18615` + 循环 `18878` | Dashboard 日成本/模型合并 |
| 10 | `build_wrapped` `19198+` | 年度回顾统计 |
| 11 | `_PROJECT_SOURCES` / `_PROJECT_RECORD_SOURCES` / `_PROJECT_DAY_SOURCES` `20456` | 项目足迹与 Wrapped 项目归属 |
| 12 | `main()` 面板块 `18003` | SwiftBar 文本输出（可选） |
| 13 | `_sync_safe_usage_payload` `17730` | 若含账号信息需剔除 |

#### B. macOS（Swift，必须）

| 文件 | 位置 | 改动 |
|---|---|---|
| `Model.swift` | `1352` Usage、`1389` CodingKeys、`1399` init | 加 `@Boxed` 字段 + 容错解码（`decodeIfPresent ?? .empty`） |
| `PanelView.swift` | `toolCards` 约 `595`、设置开关 `3301`、诊断表 `4527` | 卡片、`@AppStorage` 开关、诊断 |
| `Design.swift` | 主题色 | 新 tint |
| `UsageCardOrdering.swift` | `46` | 卡片排序权重 |
| `UsageSummaryBuilder.swift` | `32` | 复制摘要开关 |
| `DashboardView.swift` | `423/1144/1218/1243` | 模型色、模型合并、token/cost 总计 |
| `ProjectTrailView.swift` | `265` | 项目色 |
| `SyncManager.swift` | `504` | 多设备合并规则（否则同步后看不到 peer 数据） |
| `UsageShareImage.swift` | `149` | 分享图色 |

#### C. Windows（可选，但 fork 若保留双端就必须做）

| 文件 | 位置 | 改动 |
|---|---|---|
| `windows/src/cards/tokenUsage.tsx` | `244` | 新增 `tokenCard({...})` |
| `windows/src/cards/registry.ts` | `20/61` | 注册并保持与 Mac 同序 |
| `windows/src/lib/types.ts` | `229` 附近 | 类型（通用工具可直接复用 `TokenUsageStat`，只在注释里登记） |
| `windows/src/lib/cardOrder.ts` | `41` | 排序 |
| `windows/src/lib/share.tsx` | `55/540/581` | 分享图开关/内容/颜色 |
| `windows/src/pages/Settings.tsx` | `40` | 显示开关 |
| `windows/src/pages/Projects.tsx` | `183` | 项目色 |
| `windows/src/pages/dashboard/types.ts` | `66` | 日成本字段 |
| `windows/src/pages/dashboard/Sections.tsx` | `135` | 图表色 |
| `windows/src/pages/settings/diagnostics.ts` | `13` | 诊断清单 |
| `windows/src/ui/theme.ts` | `36` | tint |

#### D. 文档与测试

- `README.md` 支持工具表、`CALCULATION.md` §1/§2/§4、`CONTEXT.md` 如需；
- `tests/fixtures/` 合成样本（脱敏），新增 `tests/test_<tool>.py`；
- 若涉及原生能力，补 `tests/swift/*Check.swift` 或 `windows/tests/*.mjs`。

#### E. 成本结论

> 一个"只读本地 JSONL、按价表估算、无额度"的工具，约 **25 个文件、80 处引用**（其中真正需要写逻辑的约 40~50 处，其余是 import/注册/注释/文档）；
> 若带原生登录态额度，再加 **3~6 个文件**（Swift 桥/Rust/Python 桥 + 设置页 + 托盘源）。
> 这就是为什么下一节的方案优先选择"减少每工具成本"和"不碰核心大文件"。

### 4.2 新增一个指标

分两种情况：

**情况 1：原始日志已有字段，只是没展示（低成本）**
- Python：在对应 `scan_*` 的 record / day bucket 里带上字段；
- Swift `Model.swift` 对应 Range 增加字段；`PanelView` 的 `metric(...)` 增加一行；
- Windows 对应 card 增加 `metric(...)`；
- 分享图 / 摘要按需；
- 验收：同一份 fixture 的 JSON 字段与 UI 显示一致。

**情况 2：需要新的原始字段（中成本）**
1. 解析器：record 增加字段；
2. `_add_token_usage()` / `_add_model_usage()`（`2275`）增加累加参数；
3. `_empty_token_day()` / `_empty_token_bucket()`（`1995-2004`）初始化；
4. `ledger`：`_LEDGER_FIELDS`（`1367`）与 `_LEDGER_TOKEN_FIELDS`（`1612`）增加字段，并考虑 `_ledger_record_version` 升级；
5. range 适配与 `compute()` 输出；
6. `Model.swift` / `types.ts` / 卡片；
7. `build_daily_costs` / `build_wrapped`（如果指标要进 Dashboard/回顾）；
8. **版本升级**：`_SCAN_CACHE_VERSION` 或对应 parser 版本 +1，确保旧缓存重建；
9. 测试：字段映射、账本合并、旧缓存兼容。

**注意**：改 `_LEDGER_VERSION` 或账本字段会直接影响 `~/.tokei/ledger.json` 与上游 App 的兼容性。fork 若与上游共用 `~/.tokei`，应避免改账本 schema，或使用独立 `TOKEI_CACHE_DIR`/独立用户目录。

**两侧差异**：
- **macOS**：`Model.swift` 是强类型解码；核心工具字段（`claude`/`codex`/`gemini`/`grok`/`hermes`/`openclaw`/`opencode`）用 `decode` 是 required，缺字段会让整份 `Usage` 解码失败；其余工具用 `decodeIfPresent(...) ?? .empty` 兜底（`Model.swift:1399-1430`）。新增工具建议按后者写，并同时补 `CodingKeys` 与 `init`。`PanelView` 的 `metric(...)` 是显式调用，加一行即可。
- **Windows**：多数工具共用 `TokenUsageStat`；只是新增展示项时改 `cards/tokenUsage.tsx` 即可，不必动 `types.ts`。Dashboard 聚合在 `pages/dashboard/*`，与 Mac 的 `DashboardView` 是两套实现。
- **分享图/摘要**：Mac 在 `UsageShareImage.swift` + `UsageSummaryBuilder.swift`；Windows 在 `lib/share.tsx`。两边都要单独改，不会自动同步。

### 4.3 新增一个额度来源

先选"查询放在哪一层"，再按模板接：

| 场景 | 推荐层 | 理由 |
|---|---|---|
| HTTP API + 用户填 Key | **Python provider quota** | 与现有 Sub2API/z.ai/MiniMax 同构，跨平台复用 |
| 需要读取本机 App 登录态（Keychain/凭据文件） | **原生层取凭据 + Python 查询** | 遵守"Python 不读 Keychain"的现有边界 |
| 需要浏览器会话/网页登录 | **原生 WebView 桥** | 参考 Doubao Work（Swift WKWebView / Rust WebViewWindow） |
| 只是本地日志里的额度字段 | **工具自己的 `scan_*`** | 参考 Codex `rate_limits`、Kimi `usages` |

**Python provider quota 模板**（最常用）：

1. `_provider_quota_enabled("<name>")`（`6936`）与 config 键 `<name>_quota_enabled`；
2. `_normalize_<name>_quota()`：统一成 `{status, updated, windows:[{id,title,used_pct,reset,...}]}`；
3. `_cached_provider_quota` / `_save_provider_quota_cache`（`7121/7176`）；
4. `scan_<name>_quota()`：读凭据 → 请求 → 规范化 → 缓存；
5. `scan_provider_quotas()`（`9926`）注册到 `all_scans`；
6. `compute()` 的 `local_only` 分支（`17408-17414`）与结果键（如 `"<name>": provider_quotas["<name>"]`）；
7. Swift `ProviderQuotaStat` 或专用模型 + 卡片 + 设置开关 + 托盘额度源；
8. Windows `providerQuota.tsx`（通用）或专用 card + `ProviderQuotaSection`；
9. 若含账号信息，加入 `_sync_safe_usage_payload` 的剔除列表；
10. 测试：正常、401、429、过期读数、缓存标记。

**新增"工具自带额度"模板**（如 Codex/Kimi）：在 `compute()` 的工具结果里增加 `p5/pw/r5/rw/*_stale`，并在 Swift/Windows 卡片里画 `QuotaRow`。这类改动会影响 `Model.swift` 的 `KimiCodeStat`/`CodexStat`，冲突面比 provider quota 大。

**两侧差异**：
- **凭据**：macOS 用 `ProviderCredentialStore`（Keychain，`ProviderCredentialStore.swift:143`）；Windows 用 `store.rs`（Credential Manager，`store.rs:164/183`）；Linux 走 stdin 管道。三处是独立实现。
- **原生桥**：需要 Swift bridge 时，Windows 通常要再写一份 `native_bridge.py` 或 Rust 命令，不能复用 Swift；Doubao Work 就是两套（`DoubaoWorkQuotaBridge.swift` / `doubaowork_webview.rs`）。
- **UI 与托盘**：macOS 若归一成 `ProviderQuotaStat`，通用卡片几乎零改动；Windows 需在 `ProviderQuotaSection.tsx` 登记开关。托盘额度源 macOS 在 `MenuBarStyle.swift`，Windows 在 `trayQuota.ts`。

### 4.4 上游冲突面

| 文件 | 上游改动频率 | 冲突风险 | 规避方式 |
|---|---|---|---|
| `usage.30s.py` | 极高（每个工具/价格更新都改） | 极高 | 新功能放独立模块，核心只留 1~3 行 import/注册 |
| `PanelView.swift` | 极高（所有卡片与设置） | 极高 | 新 UI 放独立 Swift 文件；`toolCards` 只加一行 |
| `Model.swift` | 高（每个新工具） | 高 | 用容错解码；新结构放独立文件 + extension |
| `windows/src/lib/types.ts` | 高 | 高 | 新卡片类型放卡片文件；共用类型只做可选字段 |
| `windows/src/cards/registry.ts` | 高 | 中 | 只加一行 import + 一行数组元素 |
| `SyncManager.swift` | 中 | 中 | 新工具合并规则放 extension/独立文件 |
| `Tokei/package.sh` + `windows/src-tauri/tauri.conf.json` | 中 | 中 | fork 新增资源必须同步改打包清单 |
| `Updater.swift` + `UpdateSecurity.swift` | 低 | 低但致命 | fork 必须改或关闭自动更新 |
| `release.sh` + `install.sh` + `skills/*` | 低 | 低但致命 | fork 必须改分发目标 |
| `README.md` / `CALCULATION.md` | 中 | 低 | 冲突可手工合并 |

### 4.5 打包/分发耦合（容易漏）

macOS：
- `Tokei/package.sh:84-92` 只复制 `usage.30s.py`、`doubaowork-quota.js`、`pricing*.json`、图标、音效；
- **不打包 Python**；`DataLoader.pythonPath` 依次找 `/opt/homebrew/bin/python3`、`/usr/local/bin/python3`、`/usr/bin/python3`；
- fork 新增的任何运行时文件（插件、配置、模型）都必须加入 `package.sh`，否则 App 里不存在。

Windows：
- `tauri.conf.json` 的 `bundle.resources` 打包 `usage.30s.py`、`pricing.json`、`pricing_overrides.json`、`native_bridge.py`；
- `tauri.windows.conf.json` 额外打包 `python/`（嵌入式解释器 + `zstandard` + `cryptography`）；
- fork 新增文件要同时改这两处，否则 Windows 安装包缺文件。

远程设备：
- `install.sh` 只拉 `usage.30s.py`、`pricing.json`、`pricing_overrides.json`；
- fork 的采集器若依赖新模块，必须改 `install.sh` 的下载清单与来源。

---

## 5. 功能拓展方案

### 5.0 优先级矩阵

评分口径：价值 = 对"个人 AI 用量管理"的实际帮助；成本 = 代码/测试/双端工作量；冲突 = 与上游同步的冲突面。

| 优先级 | 方向 | 价值 | 成本 | 上游冲突 | 结论 |
|---|---|---|---|---|---|
| P0 | fork 安全护栏（更新/分发/版本/隔离） | 极高（不做会丢功能） | 低 | 低 | **先做** |
| P1 | 预算与阈值提醒（日/月/额度/异常） | 高 | 中低 | 低 | **推荐首做功能** |
| P2 | 额度耗尽预测与周期分析 | 高 | 中 | 低 | 推荐 |
| P3 | 本地历史分析库（SQLite）+ 长期报告 | 高 | 中高 | 极低 | 推荐（外挂式） |
| P4 | 通用额度 Provider 插件化 | 中高（长期） | 中高 | 中 | 第二个工具后再做 |
| P5 | 通用 JSONL 工具适配器（声明式） | 高（长期） | 高 | 高 | 谨慎，独立模块 |
| P6 | 模型成本 What-if / 路由建议 | 中 | 低 | 低 | 可选 |
| P7 | Windows 功能对齐 | 取决是否用 Windows | 高（每功能×2） | 中 | 先做产品决策 |
| P8 | 团队共享/服务端聚合 | 中 | 高 | 高 | 暂不推荐 |

### P0. fork 安全护栏（必须先做）

**问题**：当前 fork 的自动更新、采集器分发、脚本同步都指向上游；一旦上游发版，fork 可能被覆盖，或者远程设备继续跑上游采集器。

**要做的事**：
1. **更新通道**
   - 个人自用：打包时设置 `TOKEI_LOCAL_BUILD=1`（`package.sh:20-24` 会写 `TokeiLocalBuild`），`Updater.checkForUpdate` 自动跳过；同时隐藏/禁用设置页的手动"检查更新"，或接受它仍指向上游的风险。
   - 要分发：fork 自己的 `latest.json` + 下载域名，并同步修改 `Updater.swift:24-27` 与 `UpdateSecurity.swift:142-153` 的白名单。这是小改动但每次上游改这两处都会冲突，建议单独 patch 文件管理。
   - 不建议用 `1.0.52-fork.1` 这种非纯数字 tag：`versionParts` 只接受全数字分段（`UpdateSecurity.swift:261-268`），会让所有版本比较失效，`release.sh` 也会解析失败。
2. **采集器 revision**：`usage.30s.py:2` 的 `TOKEI_COLLECTOR_REVISION` 自增（例如 1000+），确保 `CollectorScriptInstaller` 的"只升不降"不会用上游脚本覆盖 fork 脚本。
3. **分发源**：`install.sh`、`skills/tokei-setup.md`、`tokei-collector-skill.md`、`release.sh:42` 改为 fork 自己的地址；否则远程 Linux 设备永远跑上游采集器。
4. **状态隔离**：fork 与上游共用 `~/.tokei`。若 fork 改了账本/扫描缓存 schema，必须升 `_LEDGER_VERSION` / `_SCAN_CACHE_VERSION`，并考虑用 `TOKEI_CACHE_DIR` 或独立目录，避免上游 App 读写同一份不兼容缓存。
5. **版本号**：`Updater.releaseTag`、`windows/package.json`、`windows/src-tauri/Cargo.toml`、`tauri.conf.json`、安装包元数据需要一致的 fork 版本策略。

**涉及文件**：`Tokei/Sources/Tokei/Updater.swift`、`Tokei/Sources/TokeiUpdateSecurity/UpdateSecurity.swift`、`Tokei/package.sh`、`usage.30s.py`（头部 revision）、`install.sh`、`skills/tokei-setup.md`、`tokei-collector-skill.md`、`release.sh`、`windows/package.json`、`windows/src-tauri/Cargo.toml`、`windows/src-tauri/tauri.conf.json`。

**风险**：改更新域名会降低安全性（必须保留 HTTPS、SHA-256、域名白名单校验）；自建分发需要维护证书/对象存储。

**验收**：
- 断网/正常网络下，启动 24h 内不出现"升级到上游版本"；
- `~/.tokei/usage.30s.py` 在安装 fork 后 revision 为 fork 值，重装上游不会降级；
- 远程设备 `crontab` 拉取的是 fork 采集器；
- `./release.sh` 只发布到 fork 目标。

### P1. 预算与阈值提醒

**价值**：Tokei 现在只"展示"，不"提醒"。对个人用户，最有用的扩展是：日/月成本预算、额度低于阈值、异常消耗（某工具突然飙升）、订阅周期即将重置。

**要做的事**：
1. 新增 fork 模块（建议 `fork/budget/` 或 `Tokei/Sources/Tokei/Fork/Budget.swift` + Windows 对应文件）：
   - 预算配置存 `~/.tokei/config.json` 的 `fork_budget` 键（不与上游键冲突）；
   - 输入：`last_usage.json`（或直接 `--json`）、`quota_cycles.json`、`quota_history.json`；
   - 规则：日成本 > X、月成本 > Y、额度剩余 < Z%、耗尽预测 < N 小时、单日环比 > M%。
2. 通知：
   - macOS：`UserNotifications`（需要 App bundle 的 notification 权限；本地 App 可行）；
   - Windows：托盘气泡/Toast（Tauri notification 插件或 Rust `windows-sys`）。
3. 防抖：同一规则每天只提醒一次；状态存 `~/.tokei/fork_alerts_state.json`。
4. UI：设置页加一个独立 section（macOS 新 Swift 文件 + 一行挂载；Windows 新 section 文件 + 一行挂载）。

**涉及文件**：
- 新增：`fork/budget/*`（Python 计算/预测可复用）、`Tokei/Sources/Tokei/ForkBudget.swift`、`windows/src/pages/settings/ForkBudgetSection.tsx`；
- 修改（尽量只一行）：`main.swift`（启动通知调度）、`PanelView.swift` 设置页挂载、`windows/src/pages/Settings.tsx` 挂载。

**风险**：
- macOS 通知权限/签名：ad-hoc 签名的 App 通知可能不稳定；需要实测；
- 不要让提醒逻辑进入采集器主循环（会拖慢 30s 刷新）；
- 上游若新增设置 section，挂载点可能冲突（低）。

**验收**：
- 构造 `last_usage.json` 使日成本超阈值，10 秒内出现一次通知；
- 同一天重复刷新不重复通知；
- 关闭开关后不再通知；
- 不影响刷新耗时（对比开启前后 `--json` 耗时）。

### P2. 额度耗尽预测与周期分析

**价值**：现有 `QuotaDetail` 已经能画"周期内已用 vs 额度"，但没有"按当前速度多久耗尽"和"本周期预计超额多少"。这是额度管理最直接的决策信息。

**要做的事**：
1. 在 UI 侧计算（不改采集器）：
   - 输入：`quota_cycles.json`（周期锚点）、`quota_history.json`（每分钟剩余额度）、`--quota-detail` 的 cycles；
   - 线性/加权回归得到 burn rate；输出 `ETA 耗尽`、`周期结束预计已用%`、`剩余可用小时`；
   - 对 5h 窗口和周窗口分别计算。
2. 展示：
   - macOS `QuotaRow` 下方加一行 `ForkForecastRow`；
   - Windows `cards/shared.tsx` 或 `quotaHistory` 页加同样一行。
3. 可选：把预测结果写回 `quota_detail` 的扩展字段（不改核心 JSON，另开 `fork_forecast` 键）。

**涉及文件**：新增 `Tokei/Sources/Tokei/ForkQuotaForecast.swift`、`windows/src/lib/forkForecast.ts`；修改 `QuotaHistoryView.swift` / `windows/src/pages/quotaHistory/*` 的挂载点；`CycleSpendRow` 可复用。

**风险**：
- 额度曲线是"观测点"，不是连续计量；窗口重置/账号切换会造成跳变，需要剔除异常点；
- 低样本时预测不可靠，必须显示置信度或"样本不足"；
- Grok 没有带时间戳的事件缓存，只能按账本 hours 估算，预测精度较低。

**验收**：
- 用历史 `quota_history.json` 回放，预测值与实际耗尽时间误差在可接受范围；
- 窗口重置后不出现负剩余/错误预测；
- 无数据时显示"样本不足"，不显示 NaN。

### P3. 本地历史分析库（SQLite）+ 长期报告

**价值**：当前历史查询依赖"扫描缓存 + 账本 + 各工具日志"，没有可 SQL 查询的长期库；跨年趋势、模型性价比、项目成本排名都只能靠现有聚合。外部 SQLite 库可以做任意分析，且**完全不改采集器核心**。

**要做的事**：
1. 新增独立进程/脚本（fork 自有，例如 `fork/history_export.py`）：
   - 定时（如每小时）调用 `usage.30s.py --json --no-sync-snapshot`、`--dashboard`、`--quota-detail`；
   - 写入 `~/.tokei/fork_history.sqlite`：`usage_daily`、`usage_model_daily`、`quota_points`、`projects`、`snapshots`；
   - 用 `date + tool + model + project` 做唯一键，幂等 upsert。
2. 查询/报表：
   - 独立 CLI（`fork/report.py`）输出 Markdown/CSV；
   - 或一个只读 HTML 报表（不进入 App）。
3. 可选：把汇总读回 App（通过 `last_usage.json` 旁边的 `fork_summary.json`），App 只读展示。

**涉及文件**：新增 `fork/history_export.py`、`fork/report.py`（可选 `fork/README.md`）；不修改 `usage.30s.py`、`Model.swift`、`types.ts`。安装/定时：`Tokei/package.sh` 或 launchd/crontab（按分发方式）。

**风险**：
- 重复采集增加 CPU/IO；必须错开主刷新，且复用 `--no-sync-snapshot`；
- 若直接从 SQLite 读工具原始库，会与采集器的去重/账本口径不一致 → **只消费 JSON，不重解析原始日志**；
- 数据库增长（按天/模型/项目，量很小，可接受）。

**验收**：
- 连续跑 3 天，`usage_daily` 与 App 今日数字一致；
- 重复执行不产生重复行；
- 不改仓库核心文件即可运行；
- 采集器升级后脚本仍能解析（字段向后兼容）。

这是"**最合适的实现路径**"示例：与其把 SQLite 塞进 20k 行采集器并承担上游冲突，不如把 JSON 当稳定 API，外挂一个库。

### P4. 通用额度 Provider 插件化

**价值**：现在每加一个额度源要复制 8~10 处模式（normalize/cache/scan/注册/UI/同步剔除）。如果 fork 计划接 2 个以上新 Provider，抽象一次是划算的。

**要做的事**：
1. 在 Python 新增 `fork/quota_providers.py`：
   - 声明式 `ProviderSpec`：`id`、`enabled_key`、`credential_env`、`url`、`headers`、`normalize(payload)`、`ttl`、`cooldown`；
   - 通用 `scan_fork_providers()`；
   - 复用现有 `_cached_provider_quota` / `_save_provider_quota_cache`（它们是通用函数）。
2. 在 `scan_provider_quotas()` 里加一行合并结果（唯一核心改动）。
3. UI 用现有通用 `ProviderQuotaCard`（Windows `cards/providerQuota.tsx`、macOS `CodexBar-compatible quota providers` 区段）渲染 `ProviderQuotaStat`。
4. 配置写入 `~/.tokei/config.json` 的 `fork_providers`，Key 仍走原生 Keychain/CredManager → env。

**涉及文件**：新增 `fork/quota_providers.py`；修改 `usage.30s.py` 的 `scan_provider_quotas`（1 行）与 `compute()` 输出（1 行）；UI 复用现有通用卡片；新增 `tests/test_fork_quota_providers.py`。

**风险**：
- 声明式 schema 容易过度设计；建议只覆盖"HTTP GET + Bearer/API Key + JSON 百分比"这一类，其余仍写专用函数；
- 安全：URL/主机必须白名单，避免把 Key 发到任意地址；
- 上游 `scan_provider_quotas` 改动会导致一行冲突，可接受。

**验收**：
- 用 mock HTTP 测 200/401/429/超时；
- 新 Provider 只加配置 + normalize 函数即可出现在 UI；
- Key 不出现在日志/同步快照。

### P5. 通用 JSONL 工具适配器（声明式）

**价值**：把"新增一个只读 JSONL 工具"从 25 文件/60 处降到"一份映射配置 + 卡片注册"。长期收益最大。

**要做的事**：
1. 定义 `fork/tool_specs/*.json`：
   - `root`、`glob`、`format`（jsonl）、`record_path`（如 `message.usage`）、字段映射（`in/out/cr/cw/reason/model/timestamp/session/project`）、去重键、成本来源（log/price）；
2. 通用 `scan_declared_tool(spec, bounds, cache)`，内部复用 `_add_token_usage`、`ledger_reconcile`、`classify_date`；
3. `compute()` 遍历 spec 注册（一个循环，而不是每工具一段）；
4. UI：继续用通用 `tokenUsage` 卡片，按 spec 生成 `CardSpec`（Windows 可动态注册；macOS Swift 需要静态类型，建议生成代码或用一个通用 `DeclaredToolStat`）。

**涉及文件**：新增 `fork/tool_specs/`、`fork/declarative_scan.py`；修改 `compute()`（一个循环 + 输出合并）；Swift/TS 通用卡片复用。

**风险（高）**：
- 各工具字段差异极大（Claude/Codex/Grok/X 的 usage 结构、去重、时区、特殊计价都不同），声明式很难覆盖全部；容易变成"配置里写 Python"；
- **对上游冲突最大**：`compute()` 是核心函数，上游每个版本都改；
- macOS Swift 是静态类型，动态工具需要额外的通用模型与卡片，可能与 `PanelView` 的强类型卡片体系冲突。

**建议**：不把它作为第一步。等 P4 稳定、且确认要接 3 个以上同构工具后再做；或者只用于 fork 私有的小众工具，不改上游工具路径。

### P6. 模型成本 What-if / 路由建议

**价值**：基于已记录的 token 结构，计算"同样 token 如果走另一个模型/供应商要多少钱"，帮助选择模型。

**要做的事**：
1. 新增只读分析脚本 `fork/whatif.py`：
   - 输入 `--json` 的 models + `pricing.json`；
   - 输出每个模型/每个工具的"当前成本 vs 备选模型成本"；
2. UI（可选）：在模型明细里加一列"备选价"，或在独立报表展示；
3. 注意：不要把 what-if 成本混入真实 `cost` 字段。

**涉及文件**：新增 `fork/whatif.py`；可选新增 macOS/Windows 报表页。**不需要改采集器核心**。

**风险**：价格表覆盖不全；同一 token 结构在不同模型上的缓存行为不同，结果只能作为粗略参考。

**验收**：对已知模型组合，手算与脚本一致；不修改任何真实成本。

### P7. Windows 策略（先做决策）

Windows 版是完整重写的第二套 UI（`PORTING.md` 明确要求逐卡片对齐、文案 key 与 Swift 完全一致）。每加一个功能，双端成本约为单端的 1.8~2 倍。

**建议**：
- 如果 fork 的用户只用 macOS：**冻结 Windows 功能对齐**，只保证采集器 JSON 不被破坏（Windows 仍可显示旧卡片）；新功能只做 macOS。
- 如果必须双端：优先做"数据/通知"类功能（可在 Python 或共享层完成），少做"纯 UI"功能；每个功能先写 Windows 卡片再写 Swift，避免二次翻译。

**这是本规划中最重要的产品决策**，会直接决定后续成本。

### P8. 团队/服务端聚合（暂不推荐）

现有 Git 同步适合"个人多设备"，不适合团队：快照是整份 JSON、没有增量协议、没有权限模型；账号额度又被明确排除。做团队版等于新建后端，成本高且违背项目"本地优先"的定位。

---

## 6. 上游同步与工程流程建议

### 6.1 分支与补丁策略

1. 保留 `upstream` remote，定期 `git fetch upstream --tags`；
2. fork 功能分支从 `upstream/main` 拉出，**一个功能一个分支**；
3. 每个功能尽量满足"**新增文件 + 单点注册**"：
   - Python：新模块 + `compute()`/`scan_provider_quotas()` 一行 import/调用；
   - Swift：新文件 + `toolCards`/设置页一行；
   - TS：新卡片文件 + `registry.ts` 一行；
4. 维护一份 `FORK.md`（或 `docs/fork-changes.md`）记录"改了哪些上游文件、为什么、冲突时怎么解"；
5. 上游发版后先跑 `git rebase upstream/main`，冲突集中在已知热文件，按 `FORK.md` 逐项处理；
6. 每次 rebase 后跑完整测试（见 6.3）。

### 6.2 版本与分发

| 事项 | 建议 |
|---|---|
| 版本号 | fork 用独立 major/minor（如 `2.x`）或 `1.0.52` 基础上递增 patch；避免非数字后缀 |
| 自动更新 | 个人自用：`TOKEI_LOCAL_BUILD=1` 关闭自动检查；对外分发：自建 `latest.json` + 改白名单 |
| 采集器 revision | 每次改 `usage.30s.py` 自增 `TOKEI_COLLECTOR_REVISION` |
| Windows 版本 | `package.json` / `Cargo.toml` / `tauri.conf.json` 三处同步 |
| 发布 | 个人自用可只本地 `package.sh`；不要直接运行上游 `release.sh`（会发到上游仓库/R2） |

### 6.3 验收清单（每个功能）

```bash
# Python
python3 -m py_compile usage.30s.py
PYTHONPATH=tests python3 -m unittest discover -s tests
python3 usage.30s.py --json --no-sync-snapshot > /tmp/tokei.json
python3 usage.30s.py --dashboard > /tmp/tokei-dashboard.json
python3 usage.30s.py --quota-detail > /tmp/tokei-quota.json

# macOS
swift build --package-path Tokei
bash Tokei/Tests/run-sync-integration-checks.sh

# Windows
cd windows && npx tsc --noEmit -p . && node scripts/check-i18n.mjs && node --test

# 打包
cd Tokei && TOKEI_LOCAL_BUILD=1 ./package.sh
```

UI 层建议在真实数据上对比"改动前/后"的同一区间数字，确认没有重复计数或漏计。

---

## 7. 最终建议（按执行顺序）

1. **先做 P0**：不解决更新/分发，后面的功能可能被上游覆盖，或远程设备根本用不到。
2. **再做 P1 + P2**：预算/阈值提醒、额度耗尽预测，都是"读现有 JSON/历史"的轻量功能，用户感知强、冲突面小。
3. **P3 作为分析底座**：外部 SQLite 导出，不碰核心，后续所有报表都基于它。
4. **P4 在你确实要接第二个新额度 Provider 时再做**；P5 暂缓，除非你准备长期维护一套声明式适配层。
5. **先决定 P7（Windows 是否跟随）**；这个决策比任何单点功能都影响总成本。
6. 所有新功能都遵守一条硬规则：**上游热文件只做一行注册，业务逻辑放 fork 自有文件**。

---

## 附录 A：关键文件速查

| 主题 | 文件 |
|---|---|
| 采集/计算总装 | `usage.30s.py:17180`（compute）、`17795`（json）、`18600`（dashboard）、`20359`（quota detail）、`20456`（projects） |
| 账本 | `usage.30s.py:1365-1897` |
| 价格 | `usage.30s.py:357-916`、`18032-18440`；`pricing.json`；`pricing_overrides.json`；`~/.tokei/pricing_official.json` |
| 数据源路径 | `usage.30s.py:172-305` |
| macOS 入口/刷新 | `Tokei/Sources/Tokei/main.swift:7/108/294/308` |
| macOS 脚本执行 | `Tokei/Sources/Tokei/DataLoader.swift:556/753` |
| macOS 模型 | `Tokei/Sources/Tokei/Model.swift:1352` |
| macOS 面板/设置 | `Tokei/Sources/Tokei/PanelView.swift:54/595/2966` |
| macOS 同步 | `Tokei/Sources/Tokei/SyncManager.swift:329/444` |
| macOS 凭据 | `Tokei/Sources/Tokei/ProviderCredentialStore.swift:143` |
| macOS 更新 | `Tokei/Sources/Tokei/Updater.swift:24`；`TokeiUpdateSecurity/UpdateSecurity.swift:142` |
| Windows 采集 | `windows/src-tauri/src/collector.rs:37/103/134` |
| Windows 原生桥 | `windows/native_bridge.py:142/250/285/500` |
| Windows 凭据 | `windows/src-tauri/src/store.rs:22/164/183` |
| Windows 前端 | `windows/src/lib/store.tsx`、`collector.ts`、`types.ts`、`cards/registry.ts` |
| Windows 设置 | `windows/src/pages/Settings.tsx`、`pages/settings/*` |
| 打包 | `Tokei/package.sh:84`、`windows/src-tauri/tauri.conf.json`、`tauri.windows.conf.json:8` |
| 发布 | `release.sh`、`.github/workflows/*.yml` |
| 文档 | `README.md`、`CALCULATION.md`、`CONTEXT.md`、`docs/`、`site/` |

## 附录 B：结论的证据强度说明

- **强证据**：目录/文件/函数/行号均来自实际代码阅读；工具数量、接入点数量来自 `musecode` 的跨文件检索统计；更新/分发指向来自 `Updater.swift`、`UpdateSecurity.swift`、`release.sh`、`install.sh` 的实际 URL。
- **中证据**：上游冲突频率基于文件职责与历史版本演进（`docs/releases/`、`git log`）判断，不是精确统计；建议在真正开始改造前用 `git log --stat` 对目标文件做一次改动频率排序。
- **推断项**：P1~P8 的工作量/价值排序是工程判断，不是代码事实；落地前应按团队/个人实际使用场景重新打分。
