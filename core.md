# Lampson 核心记忆

## 用户偏好
- 喜欢简洁回复，优先使用工具完成任务

## 飞书消息
- 使用 feishu_card，类型用 form（不支持原生 table）
- 不用 Markdown 表格，用 form fields 列表展示结构化数据
- 字段 label 和 value 要对齐

## 项目路径
- lampason: ~/lampson
- model-platform: /nas/syh/workspace/model-platform/
- hermes: ~/.hermes/hermes-agent

## 远程机器
- 使用 ~/.ssh/config 配置的别名连接
- 连接前用 project_context(name="machines") 确认别名

## 常用工具
- search_content: 内容搜索，替代 grep
- search_files: 文件搜索，替代 find
- shell: 执行命令

## 约束
- 危险操作（删除、修改系统）需先确认
- 搜索远程机器目录要加 -maxdepth 限制深度

## 技能目录
- ~/.lampson/skills/ 存放可复用工作流
