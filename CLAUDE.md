@AGENTS.md

## Claude Code 补充

- 以上约束对所有 Agent 通用；这里只放 Claude Code 专属做法。
- UI 修改使用 `impeccable` skill；把编辑派给子代理或外部模型前、以及接受其提交或 diff 前，使用 `worktree-guard` skill 核验。
- Playwright MCP 会在会话 cwd 下写 `.playwright-mcp/`：在 worktree 中启动会话，截图路径指向 scratchpad。
