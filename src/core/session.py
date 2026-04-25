"""Session — Agent 生命周期管理、命令路由、上下文压缩。

gateway 层（cli.py / listener.py）只需关心消息收发，
所有业务逻辑通过 Session 这一层统一处理。
"""

from __future__ import annotations

import concurrent.futures
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.core.config import LAMPSON_DIR
from src.core.llm import LLMClient
from src.core.compaction import CompactionConfig
from src.core.agent import Agent
from src.memory import manager as memory_mgr
from src.skills import manager as skills_mgr

logger = logging.getLogger(__name__)

HELP_TEXT = """\
可用命令：
  /help                          显示此帮助
  /config                        查看当前配置
  /model                         显示当前模型和可用模型列表
  /model <name>                  切换到指定模型
  /model all <question>          同时向所有可用模型提问，对比回答
  /memory show                   查看核心记忆
  /memory add <text>             添加记忆条目
  /memory search <keyword>       搜索记忆
  /memory forget <keyword>       删除含关键词的记忆条目
  /skills list                   列出所有技能
  /skills show <name>            查看技能详情
  /skills create <name>          创建新技能
  /feishu send <id> <msg>        发送飞书消息（需配置 app_id/secret）
  /feishu read <chat_id>         读取飞书消息
  /serve                         启动飞书消息监听服务（长连接 WebSocket）
  /update <需求描述>              触发自更新
  /update rollback               回滚自更新
  /update list                   列出自更新分支
  /exit                          退出

直接输入自然语言即可与 Lampson 对话。"""


@dataclass
class HandleResult:
    """handle_input 的返回值，让 gateway 知道发生了什么。"""

    reply: str = ""              # 要展示/发送的回复文本
    is_exit: bool = False        # 用户要求退出
    is_command: bool = False     # 这是一条 / 命令（不需要再格式化）
    compaction_msg: str = ""     # 压缩通知（空字符串表示没压缩）


class Session:
    """管理 Agent 的完整生命周期。

    gateway 的使用方式：
        session = Session.from_config(config)
        result = session.handle_input(user_input)
        # result.reply → 发给用户
        # result.is_exit → 该退出循环了
    """

    def __init__(
        self,
        agent: Agent,
        config: dict[str, Any],
        skills: dict | None = None,
    ) -> None:
        self.agent = agent
        self.config = config
        self.skills: dict = skills or {}
        self._feishu_initialized = False
        # 多个模型的 LLM 客户端 {model_name: LLMClient}
        self.llm_clients: dict[str, Any] = {}
        self._current_model_name: str = ""

    # ── 工厂方法 ──

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> Session:
        """从配置字典创建完整初始化的 Session。

        包含：安装默认技能 → 加载记忆/技能 → 创建 LLM → 创建 Agent → 初始化飞书。
        """
        _install_default_skills()

        core_memory = memory_mgr.load_core()
        skills = skills_mgr.load_all_skills()

        # 构建多模型客户端字典
        llm_clients: dict[str, Any] = {}

        # 先加入 config.llm（当前激活模型）
        primary_llm = _create_llm(config)
        primary_name = config["llm"]["model"]
        llm_clients[primary_name] = primary_llm

        # 加入 config.models 列表中的模型（跳过已添加的主模型）
        primary_api_key = config["llm"].get("api_key", "")
        primary_base_url = config["llm"].get("base_url", "")
        for model_cfg in config.get("models", []):
            name = model_cfg["name"]
            if name not in llm_clients:
                llm_clients[name] = _create_llm_from_model_config(
                    model_cfg,
                    fallback_api_key=primary_api_key,
                    fallback_base_url=primary_base_url,
                )

        compaction_cfg = _build_compaction_config(config)
        agent = Agent(primary_llm, compaction_config=compaction_cfg)
        agent.set_context(core_memory=core_memory)
        agent.skills = skills

        session = cls(agent=agent, config=config, skills=skills)
        session.llm_clients = llm_clients
        session._current_model_name = primary_name
        session.init_feishu()
        return session

    # ── 核心入口 ──

    def handle_input(self, user_input: str) -> HandleResult:
        """处理一条用户输入，返回 HandleResult。

        自动判断是命令还是自然语言，内部处理压缩。
        """
        if user_input.startswith("/"):
            return self._handle_command(user_input)

        # 自然语言
        try:
            reply = self.agent.run(user_input)
        except Exception as e:
            return HandleResult(reply=f"[错误] {e}")

        # 压缩
        compaction_msg = ""
        try:
            cr = self.agent.maybe_compact()
            if cr is not None:
                if cr.success:
                    compaction_msg = f"[上下文压缩] 已完成，归档 {cr.archived_count} 条内容。"
                else:
                    compaction_msg = f"[上下文压缩] 失败: {cr.error}"
        except Exception:
            pass

        return HandleResult(reply=reply, compaction_msg=compaction_msg)

    # ── 生命周期 ──

    def init_feishu(self) -> bool:
        """初始化飞书客户端。"""
        feishu_cfg = self.config.get("feishu", {})
        app_id = feishu_cfg.get("app_id", "").strip()
        app_secret = feishu_cfg.get("app_secret", "").strip()
        if not app_id or not app_secret:
            self._feishu_initialized = False
            return False
        try:
            from src.feishu import client as feishu_client
            feishu_client.init_client(app_id=app_id, app_secret=app_secret)
            self._feishu_initialized = True
            return True
        except Exception:
            self._feishu_initialized = False
            return False

    @property
    def feishu_ready(self) -> bool:
        return self._feishu_initialized

    def save_summary(self) -> None:
        """保存会话摘要（退出时调用）。"""
        try:
            summary = self.agent.generate_session_summary()
            if summary.strip():
                memory_mgr.save_session_summary(summary)
        except Exception:
            pass

    def start_feishu_listener(self) -> None:
        """启动飞书长连接监听（阻塞）。"""
        feishu_cfg = self.config.get("feishu", {})
        app_id = feishu_cfg.get("app_id", "").strip()
        app_secret = feishu_cfg.get("app_secret", "").strip()

        if not app_id or not app_secret:
            raise RuntimeError("飞书未配置，请在 config.yaml 中填写 feishu.app_id 和 feishu.app_secret")

        from src.feishu.listener import FeishuListener
        listener = FeishuListener(app_id=app_id, app_secret=app_secret, session=self)
        listener.start()

    # ── 命令路由 ──

    def _handle_command(self, cmd: str) -> HandleResult:
        """处理 / 开头的命令。"""
        parts = cmd.strip().split()
        if not parts:
            return HandleResult(is_command=True)

        command = parts[0].lower()

        if command == "/exit":
            return HandleResult(is_exit=True, is_command=True)

        if command == "/help":
            return HandleResult(reply=HELP_TEXT, is_command=True)

        if command == "/config":
            return HandleResult(reply=self._format_config(), is_command=True)

        if command == "/memory":
            return HandleResult(reply=self._handle_memory(parts), is_command=True)

        if command == "/skills":
            return HandleResult(reply=self._handle_skills(parts), is_command=True)

        if command == "/feishu":
            return HandleResult(reply=self._handle_feishu(parts), is_command=True)

        if command == "/update":
            return HandleResult(reply=self._handle_update(parts), is_command=True)

        if command == "/serve":
            # /serve 是特殊命令，由 gateway 层处理（因为会阻塞）
            return HandleResult(reply="__SERVE__", is_command=True)

        if command == "/model":
            return HandleResult(reply=self._handle_model(parts), is_command=True)

        return HandleResult(
            reply=f"未知命令：{command}，输入 /help 查看帮助。",
            is_command=True,
        )

    def _format_config(self) -> str:
        """脱敏后格式化配置。"""
        import yaml

        safe_config = dict(self.config)
        llm_cfg = safe_config.get("llm", {})
        if llm_cfg.get("api_key"):
            safe_config["llm"] = dict(llm_cfg)
            key = safe_config["llm"]["api_key"]
            safe_config["llm"]["api_key"] = key[:6] + "..." + key[-4:] if len(key) > 10 else "***"
        feishu_cfg = safe_config.get("feishu", {})
        if feishu_cfg.get("app_secret"):
            safe_config["feishu"] = dict(feishu_cfg)
            safe_config["feishu"]["app_secret"] = "***"
        return yaml.dump(safe_config, allow_unicode=True, default_flow_style=False)

    def _handle_memory(self, parts: list[str]) -> str:
        sub = parts[1] if len(parts) > 1 else "show"

        if sub == "show":
            return memory_mgr.show_core()

        if sub == "add":
            if len(parts) < 3:
                return "用法: /memory add <text>"
            text = " ".join(parts[2:])
            return memory_mgr.add_memory(text)

        if sub == "search":
            if len(parts) < 3:
                return "用法: /memory search <keyword>"
            keyword = " ".join(parts[2:])
            return memory_mgr.search_memory(keyword)

        if sub == "forget":
            if len(parts) < 3:
                return "用法: /memory forget <keyword>"
            keyword = " ".join(parts[2:])
            return memory_mgr.forget_memory(keyword)

        return "用法: /memory [show|add <text>|search <keyword>|forget <keyword>]"

    def _handle_skills(self, parts: list[str]) -> str:
        sub = parts[1] if len(parts) > 1 else "list"

        if sub == "list":
            return skills_mgr.list_skills(self.skills)

        if sub == "show":
            if len(parts) < 3:
                return "用法: /skills show <name>"
            return skills_mgr.show_skill(parts[2], self.skills)

        if sub == "create":
            if len(parts) < 3:
                return "用法: /skills create <name>"
            name = parts[2]
            desc = " ".join(parts[3:]) if len(parts) > 3 else ""
            result = skills_mgr.create_skill(name, description=desc)
            # 重新加载技能
            self.skills.clear()
            self.skills.update(skills_mgr.load_all_skills())
            return result

        return "用法: /skills [list|show <name>|create <name>]"

    def _handle_feishu(self, parts: list[str]) -> str:
        if len(parts) < 2:
            return "用法: /feishu [send <id> <msg>|read <chat_id>]"

        try:
            from src.feishu import client as feishu_client
            feishu_client.get_client()
        except RuntimeError as e:
            return f"[飞书] {e}"

        sub = parts[1]
        if sub == "send":
            if len(parts) < 4:
                return "用法: /feishu send <receive_id> <消息内容>"
            receive_id = parts[2]
            text = " ".join(parts[3:])
            return feishu_client.tool_feishu_send({
                "receive_id": receive_id,
                "text": text,
            })

        if sub == "read":
            if len(parts) < 3:
                return "用法: /feishu read <chat_id>"
            return feishu_client.tool_feishu_read({
                "container_id": parts[2],
                "page_size": 10,
            })

        return "用法: /feishu [send <id> <msg>|read <chat_id>]"

    def _handle_model(self, parts: list[str]) -> str:
        """处理 /model 命令。"""
        if len(parts) == 1:
            # /model → 显示当前模型和可用模型列表
            lines = [f"当前模型：{self._current_model_name}", "可用模型："]
            for name in self.llm_clients:
                marker = " ← 当前" if name == self._current_model_name else ""
                lines.append(f"  - {name}{marker}")
            return "\n".join(lines)

        if parts[1].lower() == "all":
            # /model all <question> → 并行查询所有模型
            question = " ".join(parts[2:])
            if not question.strip():
                return "用法: /model all <问题内容>"

            results: dict[str, str] = {}

            def query_model(name: str, client: Any) -> tuple[str, str]:
                try:
                    tmp = client.clone()
                    # clone() 深拷贝了完整历史，但 /model all 只需要 system prompt + question
                    # 保留 messages[0]（system prompt），丢弃其余历史
                    if tmp.messages:
                        tmp.messages = [tmp.messages[0]]
                    tmp.add_user_message(question)
                    resp = tmp.chat(tools=None)
                    answer = resp.choices[0].message.content or "（无内容）"
                    return name, answer
                except Exception as e:
                    return name, f"[请求失败: {e}]"

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=len(self.llm_clients)
            ) as executor:
                futures = {
                    executor.submit(query_model, name, client): name
                    for name, client in self.llm_clients.items()
                }
                try:
                    for future in concurrent.futures.as_completed(futures, timeout=90):
                        name, answer = future.result()
                        results[name] = answer
                except concurrent.futures.TimeoutError:
                    # 超时的模型标记为未响应
                    for name in self.llm_clients:
                        if name not in results:
                            results[name] = "[请求超时]"

            # 按模型名字典序输出，便于对比
            lines = []
            for name in sorted(results):
                lines.append(f"=== {name} ===")
                lines.append(results[name])
                lines.append("")
            return "\n".join(lines).strip()

        # /model <name> → 切换模型（方案B：迁移对话历史到新 client）
        target_name = parts[1]
        if target_name not in self.llm_clients:
            available = ", ".join(sorted(self.llm_clients.keys()))
            return f"未知模型：{target_name}，可用模型：{available}"

        new_llm = self.llm_clients[target_name]
        self._current_model_name = target_name
        # 通过 agent.switch_llm 统一处理引用同步和历史迁移
        self.agent.switch_llm(new_llm)
        return f"已切换到模型：{target_name}"

    def _handle_update(self, parts: list[str]) -> str:
        from src.selfupdate import updater

        if len(parts) < 2:
            return "用法: /update <需求描述> 或 /update rollback 或 /update list"

        sub = parts[1]
        if sub == "rollback":
            return updater.run_rollback()
        if sub == "list":
            return updater.list_update_branches()

        description = " ".join(parts[1:])
        return updater.run_update(description, self.agent.llm)


# ── 模块级辅助函数（Session 内部使用，不暴露给 gateway） ──


def _install_default_skills() -> None:
    """将内置技能复制到用户目录（首次运行）。"""
    default_skills_dir = Path(__file__).resolve().parent.parent.parent / "config" / "default_skills"
    try:
        skills_mgr.install_default_skills(default_skills_dir)
    except Exception:
        pass


def _create_llm(config: dict[str, Any]) -> LLMClient:
    """从配置创建 LLMClient（使用 config.llm 部分）。"""
    llm_cfg = config["llm"]
    return LLMClient(
        api_key=llm_cfg["api_key"],
        base_url=llm_cfg["base_url"],
        model=llm_cfg["model"],
        supports_native_tool_calling=llm_cfg.get("native_tool_calling", True),
    )


def _create_llm_from_model_config(
    model_cfg: dict[str, Any],
    fallback_api_key: str = "",
    fallback_base_url: str = "",
) -> LLMClient:
    """从单个模型的配置字典创建 LLMClient。
    
    config 中的模型条目以 `name` 字段标识模型名。
    api_key / base_url 缺失时 fallback 到主模型配置。
    """
    api_key = model_cfg.get("api_key", "") or fallback_api_key
    base_url = model_cfg.get("base_url", "") or fallback_base_url
    if not base_url:
        raise ValueError(
            f"模型 {model_cfg.get('name', '?')} 缺少 base_url 配置"
        )
    return LLMClient(
        api_key=api_key,
        base_url=base_url,
        model=model_cfg["name"],  # config 用 name 字段标识模型
        supports_native_tool_calling=model_cfg.get("native_tool_calling", True),
    )


def _build_compaction_config(config: dict[str, Any]) -> CompactionConfig | None:
    """从配置字典构建 CompactionConfig。"""
    c = config.get("compaction", {})
    if not c:
        return CompactionConfig()
    return CompactionConfig(
        trigger_threshold=c.get("trigger_threshold", 0.8),
        end_threshold=c.get("end_threshold", 0.3),
        context_window=c.get("context_window", 131072),
        max_iterations=c.get("max_iterations", 3),
        enable_archive=c.get("enable_archive", True),
    )
