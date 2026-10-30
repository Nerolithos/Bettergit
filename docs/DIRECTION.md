# 产品方向与需求依据

调研日期：2026-10-30。以下区分已实现能力、社区需求记录和产品推断；没有把历史议题当成当前尚未解决的缺陷。

## 公开证据

1. [Git status 官方文档](https://git-scm.com/docs/git-status)：大型工作区寻找未跟踪文件可能很慢，文档讨论 untracked cache、fsmonitor 等手段。
   对应当前能力：文件扫描证据、status 对照测量；先给出原因线索，再建议优化。
2. [Git Trace2 技术文档](https://github.com/git/git/blob/master/Documentation/technical/api-trace2.adoc)：提供阶段和子进程事件，可观察 SSH、hook 等等待。
   对应当前能力：命令执行期间的阶段观察。局限：事件不保证足以区分 DNS、TCP、TLS 和认证。
3. [Git merge-tree 官方文档](https://git-scm.com/docs/git-merge-tree)：可使用 Git 的合并机制预演，不读取或修改工作区/索引。
   对应当前能力：不用“两个提交是否修改同名文件”这种不准确的规则猜冲突。
4. [GitHub CLI 社区需求汇总 #2184](https://github.com/cli/cli/issues/2184)：记录默认分支、初始化 `.gitignore` 与许可证等创建流程需求。
   这是历史需求证据；[当前 gh repo create](https://cli.github.com/manual/gh_repo_create) 已提供相关能力。
5. [GitHub CLI #7836](https://github.com/cli/cli/issues/7836)：讨论创建 README 后本地/远端分支不一致的情况。
   产品启发：创建远端仓库后需要明确显示“当前分支、upstream、哪些提交在远端”，不能只显示创建成功。
6. [GitHub CLI 创建流程重写 #4567](https://github.com/cli/cli/issues/4567)：展示交互式建仓流程中的 README、gitignore 等选择。
   产品启发：Shell 的 git init 可以补全轻量初始化体验；GitHub 远端能力应复用 gh。

这些材料支持优先验证下面的需求，但不能替代真实用户访谈或量化频率调查。

## 初版 MVP（当前实现另见 IMPLEMENTATION.md）

- 自动慢命令告警与有时间预算的诊断；保留 Git 原始输出及退出状态。
- upstream 同步预演、冲突文件报告、默认拒绝的合并确认。
- 本地初始化时的 `.gitignore` 建议和确认创建。

## 初版功能提案

以下保留调研时的需求清单；其中诊断、同步和恢复基础现已深化，实际状态以 [IMPLEMENTATION.md](IMPLEMENTATION.md) 为准。

| 优先级 | 场景 | 建议交互与验收标准 |
| --- | --- | --- |
| P1 | push 被拒绝但不知道目标关系 | 精确解析 remote/refspec/pushRemote；显示实际目标分支与 ahead/behind，支持与 upstream 不同的目标 |
| P1 | 网络、代理或认证卡住 | 用户主动选择网络探测；分别限制 DNS/TCP/TLS/ls-remote 耗时，隐藏令牌，区分本地 file remote；不自行修改 SSH 或代理 |
| P1 | 大目录和嵌套仓库拖慢操作 | 有预算的目录采样、嵌套 `.git` / 子模块 / worktree 拓扑解释；不跟随符号链接、不全盘遍历；显示采样截断 |
| P1 | PR 创建前才发现提交错分支 | `review` 汇总目标分支、commit diff、未推送提交；调用 gh 创建草稿 PR 前展示具体参数并确认 |
| P1 | CI 失败只能去网页翻日志 | 复用 `gh pr checks`、`gh run view --log-failed`，展示失败 job 和日志位置；不把日志文本当作执行指令 |
| P1 | init 后还要手工补配置 | 显式模板选择器、默认分支、README、许可证可选；许可证需用户选择，避免替用户作授权决定 |
| P2 | 不知道 stash/丢失提交在哪 | 只读 recovery 列表（reflog/stash），恢复前预览 diff；保留用户当前修改 |
| P2 | 多 PR / AI 并行开发容易串工作区 | worktree 创建与列表、分支占用解释、清理前检查未提交/未推送内容 |
| P2 | 大文件或敏感信息误提交 | 提交/推送前本地检查大小和常见凭据模式，给出位置和忽略/LFS 建议；说明误报和漏报边界 |
| P2 | 新手看不懂 detached HEAD/无 upstream | 可执行下一步建议与命令预览；执行修改前确认，不悄悄建立新分支或设置 remote |
| P3 | 常用操作需要组合很多命令 | 团队策略配置、机器可读 JSON、bash/fish 接入；稳定核心接口后实现 |

## 验证需求的方式

招募个人开发者、多人协作维护者、monorepo 用户各 3–5 名，围绕最近一次真实卡顿、同步失败和 PR 操作访谈。
让参与者在临时仓库完成相同任务，记录完成时间、是否理解原因、是否做出错误操作和确认次数。
如未来收集诊断指标，必须显式 opt-in；不收集命令参数、仓库路径、remote URL 或文件内容。
优先级根据实际问题频率、恢复耗时和可解释性调整，不按功能数量增长。

## 架构约束

Git 做版本控制与合并，bettergit 做观察和流程，gh 做 GitHub API 交互。
默认离线诊断，不需要 API key，不依赖生成式模型。无文本冲突并不意味着逻辑正确，合并后仍需项目自己的测试和 CI。
仓库自定义合并驱动、hooks 属于用户现有 Git 配置；预演不是运行不可信仓库代码的沙箱。
