---
created_at: '2026-05-02'
description: 通过 lark-cli 管理飞书消息的 emoji reaction，用于标记消息处理状态
invocation_count: 1
name: feishu-reactions
triggers:
- reaction
- 表情
- emoji
- 处理状态
- 标记消息
---

## 飞书消息 Emoji Reaction 管理

### 前置条件
- lark-cli 已安装：`/opt/homebrew/bin/lark-cli`
- PATH 中需要包含 `/opt/homebrew/bin`
- 使用 bot 身份（`--as bot`）

### 可用 Emoji 类型
常用可用的：THUMBSUP, THINKING, FIRE, HEART, OKHAND, ROCKET, CLOCK, HUNDRED
不可用的：YES, NO, EYES（会报 231001 invalid）

### 添加 Reaction（开始处理时）

```bash
export PATH="/opt/homebrew/bin:$PATH"
lark-cli im reactions create \
  --params '{"message_id":"<MESSAGE_ID>"}' \
  --data '{"reaction_type":{"emoji_type":"THINKING"}}' \
  --as bot
```

返回中提取 `reaction_id` 保存，后续删除用。

### 删除 Reaction（处理完成时）

```bash
export PATH="/opt/homebrew/bin:$PATH"
lark-cli im reactions delete \
  --params '{"message_id":"<MESSAGE_ID>","reaction_id":"<REACTION_ID>"}' \
  --as bot
```

### 查询消息上的 Reactions

```bash
lark-cli im reactions list \
  --params '{"message_id":"<MESSAGE_ID>"}' \
  --as bot
```

### 推荐处理标记 Emoji
- 处理中：THINKING
- 处理成功：THUMPSUP（短暂显示后删除）
- 处理失败：FIRE

### 完整流程
1. 收到任务消息 → 用 THINKING reaction 标记
2. 处理过程中保持 THINKING 状态
3. 处理完成 → 删除 THINKING reaction
4. 如果成功可短暂加 THUMBSUP（可选）
