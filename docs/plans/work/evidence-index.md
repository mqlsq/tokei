# 证据索引（只读分析）

本文件只记录分析中实际读取的关键代码位置，供报告复核。仓库代码未做任何修改。

## 采集器
- 数据源清单头注释：`usage.30s.py:17-39`
- 路径常量：`usage.30s.py:172-305`
- 价格/缓存文件常量：`usage.30s.py:329-341`
- 定价加载与三级查找：`usage.30s.py:357-569`、`price_for()` `893-916`
- 扫描缓存版本/分片：`usage.30s.py:1052-1363`
- 持久账本：`usage.30s.py:1365-1897`
- 通用 token 桶：`usage.30s.py:2005-2070`、`2275-2310`
- `compute()` 总装：`usage.30s.py:17180-17572`
- `--json` / 面板输出：`usage.30s.py:17795-18029`
- 自动核价：`usage.30s.py:18032-18440`
- Dashboard / Wrapped / QuotaDetail / Projects：`18600`、`19198`、`20359`、`20456-20645`
- CLI 分发：`usage.30s.py:20707-20736`

## 单个工具接入样本（Muse Code）
- `_empty_musecode` `2044`
- `scan_musecode` `16018`
- `compute()` 注册 `17215`、输出 `17552`
- `build_daily_costs` `18622`、`18878`
- `main()` 面板块 `18003`
- Swift：`Model.swift:1378/1396/1429`、`PanelView.swift:134/454/595/3301/4527`、`Design.swift:76`、`DashboardView.swift:423/1144/1218/1243`、`ProjectTrailView.swift:265`、`SyncManager.swift:504`、`UsageCardOrdering.swift:46`、`UsageShareImage.swift:149`、`UsageSummaryBuilder.swift:32`
- Windows：`cards/tokenUsage.tsx:244`、`cards/registry.ts:20/61`、`lib/types.ts:235`、`lib/share.tsx:55/540/581`、`lib/cardOrder.ts:41`、`pages/Settings.tsx:40`、`pages/Projects.tsx:183`、`pages/dashboard/types.ts:66`、`pages/dashboard/Sections.tsx:135`、`pages/settings/diagnostics.ts:13`、`ui/theme.ts:36`

## macOS 壳
- 入口与刷新：`Tokei/Sources/Tokei/main.swift:7`、`108`、`294-295`、`308-415`
- 脚本执行：`DataLoader.swift:14`、`556`、`603`、`674-703`、`753`
- Claude 额度原生扫描：`DataLoader.swift:232-315`、`ClaudeCLIQuotaBridge.swift:65/76`
- 凭据与开关：`ProviderCredentialStore.swift:143-193`
- 同步合并：`SyncManager.swift:329/444`
- 自动更新：`Updater.swift:24-27`；白名单：`UpdateSecurity.swift:142-153`
- 打包资源：`Tokei/package.sh:84-92`
- 数据模型：`Model.swift:1337/1352/1389/1399`

## Windows
- 采集命令：`windows/src-tauri/src/collector.rs:37/103/124/134`
- 原生桥：`windows/native_bridge.py:142/250/285/468/500`
- 凭据：`windows/src-tauri/src/store.rs:22/164/183`
- 前端刷新：`windows/src/lib/store.tsx:12-13`、`lib/collector.ts:7`
- 卡片契约：`windows/src/cards/spec.ts:22`、`cards/registry.ts:30`
- 类型：`windows/src/lib/types.ts:229`
- 打包资源：`windows/src-tauri/tauri.conf.json`（bundle.resources）、`tauri.windows.conf.json:8`
- 移植规范：`windows/PORTING.md:1-30`

## 文档/发布
- 计算口径：`CALCULATION.md`（§1 数据源、§2 字段/账本、§3 命中率、§4 成本、§5 额度、§6 区间）
- 术语：`CONTEXT.md`
- 新工具接入样例方案：`docs/plans/001-codebuddy-integration.md`
- 自动核价：`docs/automatic-pricing.md`
- 发布：`release.sh:10/42/58`、`.github/workflows/windows.yml`
- 远程设备：`install.sh`、`skills/tokei-setup.md`、`tokei-collector-skill.md`

## 核验命令（未执行，供后续验收）
- `python3 -m py_compile usage.30s.py`
- `PYTHONPATH=tests python3 -m unittest discover -s tests`
- `swift build --package-path Tokei`
- `bash Tokei/Tests/run-sync-integration-checks.sh`
- `cd windows && npx tsc --noEmit -p . && node scripts/check-i18n.mjs`
- `python3 usage.30s.py --json --no-sync-snapshot`
