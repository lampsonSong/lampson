---
created_at: '2026-04-28'
description: 飞书消息格式规范与交互流程
invocation_count: 7
name: feishu-format
---
## feishu-format 技能

### 描述
飞书消息格式规范与交互流程

### 交互流程（边干边发）
- 多步骤任务时，每完成一步就用 feishu_send 发消息告知进度，不要等全部完成再一次性回复
- 最终结果再发一条汇总消息
- 目的：让用户实时看到进展，而不是盯着空白等半天

### 格式规则
- ✅ 使用 feishu_send 发送结构化消息（msg_type='card'）
- ✅ 优先使用 form 类型字段列表展示表格数据
- ❌ 不要用 Markdown 表格（太乱）
- ✅ 标题清晰，字段标签对齐

### 示例：form 类型渲染表格
```
card_type: form
fields:
  - label: "组件"      value: "frontend"
  - label: "技术栈"   value: "Next.js"
  - label: "说明"     value: "已构建"
---
card_type: form
fields:
  - label: "组件"      value: "backend"
  - label: "技术栈"   value: "FastAPI"
  - label: "说明"     value: "已构建"
```

### 注意事项
- 当前 feishu_card 工具不支持原生 table 类型
- form 类型的 fields 可以模拟表格行
- 或使用 md 类型但避免用 | 分隔的表格，改用列表
