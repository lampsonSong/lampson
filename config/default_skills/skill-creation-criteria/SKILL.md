---
name: skill-creation-criteria
description: 什么内容应该沉淀为 skill，什么不该。用户信息存放规范。
---

# Skill 创建标准

## 适合做 Skill

- **可复用工作流**（5+ 步骤）：编码流程、调试流程、部署流程
- **结构化知识库**：机器映射、配置模板、API 使用方式
- **被多次使用的模式**：用户反复做同一类事

## 不适合做 Skill

- 简单查询、闲聊、一次性操作
- 行为偏好 → 放 memory
- 项目特定信息 → 放 projects/
- 用户个人信息 → 放 `~/.lamix/users/{user_id}.md`

## 格式要求

```
---
name: skill-name
description: 一句话描述 + 使用场景
---

# 标题

具体步骤和指令...
```

## 命名

- 小写英文，连字符分隔
- 名字要能让人一眼看出用途

## 用户数据存放规范

- 用户个人信息（偏好、习惯、纠正记录）→ `~/.lamix/users/{user_id}.md`
- 不要在 projects/ 或 skills/ 里存用户信息
- 不要在根目录散落用户相关的 md 文件
