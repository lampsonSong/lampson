"""CLI 命令补全。"""
from typing import Optional, List
from prompt_toolkit.completion import Completer, Completion, WordCompleter
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

# 可用命令列表
COMMANDS = [
    ("help", "显示帮助信息"),
    ("exit", "退出程序"),
    ("quit", "退出程序"),
    ("new", "开始新会话"),
    ("resume", "恢复之前的会话（需指定 session ID）"),
    ("compact", "手动触发上下文压缩"),
    ("context-size", "显示当前上下文大小"),
    ("status", "显示系统状态"),
    ("config", "显示当前配置"),
    ("update", "检查并执行自更新"),
    ("skills", "查看和管理技能"),
    ("memory", "查看和管理记忆"),
    ("feishu", "飞书相关操作"),
]

# 去重后的命令
UNIQUE_COMMANDS = list(dict.fromkeys(COMMANDS))

COMMAND_COMPLETER = WordCompleter(
    [f"/{cmd[0]}" for cmd in UNIQUE_COMMANDS],
    meta_dict={f"/{cmd[0]}": cmd[1] for cmd in UNIQUE_COMMANDS},
    sentence=True,
)


class LamixCompleter(Completer):
    """Lamix 命令补全器。
    
    输入 / 时弹出命令列表，支持：
    - 上下键选择
    - Tab 补全
    - Enter 确认
    """
    
    def __init__(self, history: Optional[List[str]] = None):
        self.history = history or []

    def get_completions(self, document, complete_event):
        text = document.text
        word = document.get_word_before_cursor()

        # 如果以 / 开头，提供命令补全
        if text.startswith("/"):
            for cmd, desc in UNIQUE_COMMANDS:
                # 匹配当前输入
                cmd_prefix = f"/{cmd}"
                if cmd_prefix.startswith(text):
                    yield Completion(
                        cmd_prefix,
                        start_position=-len(text),
                        display=f"[bold cyan]/{cmd}[/bold cyan]",
                        display_meta=f"dim][{desc}[/dim]",
                    )
        # 否则提供历史补全
        elif word:
            seen = set()
            for hist in reversed(self.history):
                if hist and hist.lower().startswith(word.lower()) and hist not in seen:
                    seen.add(hist)
                    display_text = hist[:60] + ("..." if len(hist) > 60 else "")
                    yield Completion(
                        hist,
                        start_position=-len(word),
                        display=display_text,
                        display_meta="历史",
                    )


def create_key_bindings() -> KeyBindings:
    """创建按键绑定。"""
    kb = KeyBindings()
    
    @kb.add(Keys.ControlC)
    def _(event):
        """Ctrl+C 清空当前输入。"""
        event.current_buffer.text = ""
        event.current_buffer.cursor_position = 0
    
    @kb.add(Keys.Escape, eager=True)
    def _(event):
        """Escape 键清空输入并取消补全。"""
        event.current_buffer.text = ""
        event.current_buffer.cursor_position = 0
    
    return kb
