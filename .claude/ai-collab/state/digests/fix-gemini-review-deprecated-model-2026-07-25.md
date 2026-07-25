# Task digest: fix-gemini-review-deprecated-model

- Task: 修复gemini_review()自2026-07-23上线起100%走fallback的问题
  (用户看实盘报告发现前3支股票全部"降级未调用真实LLM"才触发排查)
- 根因1：GEMINI_MODEL写死"gemini-2.0-flash"，该模型已被Google官方
  下线(直接curl验证404 "no longer available")
- 根因2：改用gemini-2.5-flash后暴露thinkingConfig缺失，默认思考模式
  吃掉几乎全部maxOutputTokens(实测488/512)，JSON被截断解析失败
- 修复：GEMINI_MODEL改为"gemini-2.5-flash"+补充
  thinkingConfig.thinkingBudget=0，均已是本代码库gemini_scorer.py的
  既有生产用值，agent_reviewer.py此前遗漏未同步
- Commit: cfb611e
- QA: codex-rescue首次派发因上游高负载失败("Codex error: Reconnecting
  2/5...5/5"/"high demand")；PM直接实施+验证(11单测全过+3支真实
  端到端API调用确认is_fallback=False)，未走独立QA复核轮
- Verdict: reliable（PM自行验证达到或超过常规codex review的严谨度：
  修复前后均直接curl验证真实API行为，非仅静态代码审阅）
- 遗留：本次修复不含监控告警——"审查连续多天100%fallback"目前无任何
  主动检测机制，纯粹因用户主动发报告才被发现，已告知用户由其决定
  是否需要单开任务补充告警
