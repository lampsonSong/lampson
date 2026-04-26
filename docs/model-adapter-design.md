# Model Adapter 设计方案

## 背景

MiniMax-M2.7-highspeed 配了 `native_tool_calling: true`，但实际返回的工具调用不是标准 OpenAI `tool_calls` 格式，而是 `<minimax:tool_call><invoke name="shell">...</invoke></minimax:tool_call>` 私有 XML 格式嵌在 `content` 里。导致 `message.tool_calls` 为 None，工具调用被当成普通文本发给用户。

根本原因：**LLMClient 和 Agent 里硬编码了两种模式（native / prompt-based），没有扩展点，每个新模型的特殊行为都得改核心代码。**

## 目标

1. **基础类** `BaseModelAdapter`：定义统一接口，Agent 只跟它打交道
2. **模型子类**：每个模型族继承基础类，适配自己的特性（tool calling 格式、system prompt 特殊要求、回复后处理等）
3. **自动选择**：根据 config 中的 model 名自动实例化对应 adapter
4. **零改动上层**：Agent、Session、Listener 不感知底层模型差异

## 架构

```
config.yaml                    src/core/adapters/
┌─────────────┐               ┌─────────────────────┐
│ llm:        │               │ __init__.py          │  <- create_adapter() 工厂
│   model:    │──┐            │ base.py              │  <- BaseModelAdapter
│   ...       │  │            │ openai_compat.py     │  <- OpenAICompatAdapter (GLM, DeepSeek, Qwen...)
└─────────────┘  │           │ minimax.py           │  <- MiniMaxAdapter
                 │            └─────────┬───────────┘
                 ▼                      │
          create_adapter(model_name)    │
                 │                      │
                 ▼                      ▼
         ┌──────────────────────────────────┐
         │      BaseModelAdapter            │
         │  ┌─────────────────────────┐     │
         │  │  LLMClient (不变)        │     │  <- 持有 OpenAI SDK client
         │  └─────────────────────────┘     │
         │                                  │
         │  + chat(messages, tools)         │  <- 统一调用入口
         │  + parse_tool_calls(response)    │  <- 子类重写
         │  + format_tool_result(...)       │  <- 子类重写
         │  + build_system_prompt(...)      │  <- 子类可重写
         │  + supports_native_tools -> bool │
         └──────────────────────────────────┘
                      │
          ┌───────────┼──────────────┐
          ▼           ▼              ▼
   OpenAICompat   MiniMax      (未来: Claude, Gemini...)
```

## BaseModelAdapter 接口

```python
# src/core/adapters/base.py

class ToolCall:
    """统一的工具调用表示。"""
    id: str              # tool_call 唯一标识
    name: str            # 工具名
    arguments: dict      # 解析后的参数
    raw_arguments: str   # 原始参数字符串

class LLMResponse:
    """统一的 LLM 响应表示。"""
    content: str | None         # 文本内容
    tool_calls: list[ToolCall]  # 解析后的工具调用列表
    finish_reason: str          # stop / tool_calls
    usage: Any                  # token 用量
    raw_response: Any           # 原始 OpenAI response 对象（供高级场景使用）

class BaseModelAdapter(ABC):
    """模型适配器基类。"""
    
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
    
    @property
    @abstractmethod
    def supports_native_tools(self) -> bool:
        """该模型是否支持原生 tool calling（用于决定是否注入 prompt-based 工具描述）。"""
    
    @abstractmethod
    def parse_response(self, response: ChatCompletion) -> LLMResponse:
        """从原始 API 响应解析出统一格式。子类必须实现。"""
    
    def format_tool_result(self, tool_call_id: str, result: str) -> dict:
        """格式化工具执行结果，追加到 messages。默认 OpenAI 格式。"""
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result,
        }
    
    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatCompletion:
        """发送请求。默认直接走 OpenAI SDK。子类可重写做特殊处理。"""
        kwargs = {"model": self.llm.model, "messages": messages}
        if tools and self.supports_native_tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return self.llm.client.chat.completions.create(**kwargs)
    
    def build_system_prompt_guidance(self) -> str:
        """模型特有的 system prompt 追加内容。默认空。"""
        return ""
```

## MiniMaxAdapter 实现

```python
# src/core/adapters/minimax.py

class MiniMaxAdapter(BaseModelAdapter):
    """MiniMax 模型适配器。
    
    MiniMax 的 "原生" tool calling 不是标准 OpenAI tool_calls，
    而是在 content 中用 <minimax:tool_call><invoke> XML 格式输出。
    需要手动解析 XML，手动构造 tool_result。
    """
    
    # MiniMax 实际支持原生工具调用，但格式不是 OpenAI 标准
    @property
    def supports_native_tools(self) -> bool:
        return True  # 确实支持，只是格式不同
    
    _TOOL_CALL_RE = re.compile(
        r"<minimax:tool_call>(.*?)</minimax:tool_call>",
        re.DOTALL,
    )
    _INVOKE_RE = re.compile(
        r'<invoke\s+name="(\w+)">(.*?)</invoke>',
        re.DOTALL,
    )
    _PARAM_RE = re.compile(
        r'<parameter\s+name="(\w+)">(.*?)</parameter>',
        re.DOTALL,
    )
    
    def parse_response(self, response: ChatCompletion) -> LLMResponse:
        message = response.choices[0].message
        content = message.content or ""
        
        # 先检查标准 OpenAI tool_calls
        if message.tool_calls:
            return LLMResponse(
                content=content,
                tool_calls=[self._parse_standard_tc(tc) for tc in message.tool_calls],
                finish_reason=response.choices[0].finish_reason,
                usage=response.usage,
                raw_response=response,
            )
        
        # 兜底：解析 MiniMax XML 格式
        tool_calls = self._parse_minimax_xml(content)
        finish = "tool_calls" if tool_calls else "stop"
        
        # 清理 content 中的 XML 标签（用户不需要看到）
        clean_content = self._strip_tool_call_xml(content) if tool_calls else content
        
        return LLMResponse(
            content=clean_content or None,
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=response.usage,
            raw_response=response,
        )
    
    def _parse_minimax_xml(self, content: str) -> list[ToolCall]:
        """从 content 中解析 <minimax:tool_call> 标签。"""
        calls = []
        for tc_match in self._TOOL_CALL_RE.finditer(content):
            tc_body = tc_match.group(1)
            invoke = self._INVOKE_RE.search(tc_body)
            if not invoke:
                continue
            name = invoke.group(1)
            args = {}
            for pm in self._PARAM_RE.finditer(invoke.group(2)):
                args[pm.group(1)] = pm.group(2).strip()
            calls.append(ToolCall(
                id=f"minimax_{len(calls)}",
                name=name,
                arguments=args,
                raw_arguments=json.dumps(args, ensure_ascii=False),
            ))
        return calls
    
    def _strip_tool_call_xml(self, content: str) -> str:
        """去掉 content 中的工具调用 XML，保留其他文本。"""
        cleaned = self._TOOL_CALL_RE.sub("", content)
        return cleaned.strip()
```

## OpenAICompatAdapter（默认）

```python
# src/core/adapters/openai_compat.py

class OpenAICompatAdapter(BaseModelAdapter):
    """兼容 OpenAI 标准格式的模型适配器。
    
    适用于：GLM、DeepSeek、Qwen、Yi 等兼容 OpenAI API 的模型。
    """
    
    @property
    def supports_native_tools(self) -> bool:
        return True
    
    def parse_response(self, response: ChatCompletion) -> LLMResponse:
        message = response.choices[0].message
        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                    raw_arguments=tc.function.arguments,
                ))
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=response.choices[0].finish_reason,
            usage=response.usage,
            raw_response=response,
        )
```

## 工厂函数

```python
# src/core/adapters/__init__.py

_MODEL_PATTERNS: dict[str, type[BaseModelAdapter]] = {}

def register_adapter(pattern: str, adapter_cls: type[BaseModelAdapter]):
    _MODEL_PATTERNS[pattern.lower()] = adapter_cls

# 默认注册
register_adapter("minimax", MiniMaxAdapter)
# 其他全部走 OpenAICompatAdapter

def create_adapter(llm_client: LLMClient) -> BaseModelAdapter:
    """根据 model 名自动选择 adapter。"""
    model_lower = llm_client.model.lower()
    for pattern, cls in _MODEL_PATTERNS.items():
        if pattern in model_lower:
            return cls(llm_client)
    return OpenAICompatAdapter(llm_client)  # 默认
```

## Agent 改动

`agent.py` 的 `_run_native` 改为通过 adapter 操作：

```python
class Agent:
    def __init__(self, llm, adapter, ...):
        self.adapter = adapter  # BaseModelAdapter 实例
        ...

    def _run_tool_loop(self) -> str:
        """统一工具调用主循环。不再分 _run_native / _run_prompt_based。"""
        for _ in range(self.max_tool_rounds):
            response = self.adapter.chat(self.llm.messages, tools=self._tools)
            parsed = self.adapter.parse_response(response)
            
            # 记录原始 assistant message 到历史
            self.llm.messages.append(response.choices[0].message.model_dump(exclude_none=True))
            
            if not parsed.tool_calls:
                return parsed.content or ""
            
            for tc in parsed.tool_calls:
                result = tool_registry.dispatch(tc.name, tc.arguments)
                tool_msg = self.adapter.format_tool_result(tc.id, result)
                self.llm.messages.append(tool_msg)
        
        return "[错误] 工具调用轮次超过限制"
```

## Session 改动

`session.py` 的 `_create_llm` 改为同时创建 adapter：

```python
def _create_llm(config):
    llm_cfg = config["llm"]
    llm = LLMClient(...)
    adapter = create_adapter(llm)
    return llm, adapter
```

## 文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/core/adapters/__init__.py` | 新建 | 工厂函数 + 注册表 |
| `src/core/adapters/base.py` | 新建 | BaseModelAdapter + ToolCall + LLMResponse |
| `src/core/adapters/minimax.py` | 新建 | MiniMax XML 格式解析 |
| `src/core/adapters/openai_compat.py` | 新建 | 默认 OpenAI 兼容适配器 |
| `src/core/agent.py` | 修改 | 删 `_run_native` / `_run_prompt_based`，合为 `_run_tool_loop`，通过 adapter 调用 |
| `src/core/llm.py` | 修改 | 删 `format_tools_prompt`、`_pending_tools` 等废弃代码，精简为纯 SDK 封装 |
| `src/core/session.py` | 修改 | `_create_llm` 返回 (llm, adapter)，Agent 构造注入 adapter |
| `tests/test_adapters.py` | 新建 | adapter 单元测试 |

## 不改动的部分

- `src/core/tools.py` - 工具注册和调度不变
- `src/core/skills_tools.py` - 工具 schema 不变
- `src/core/prompt_builder.py` - system prompt 构建不变（adapter 可追加模型特有内容，但现有层级不变）
- `src/feishu/listener.py` - 只跟 Session.handle_input 打交道，不变
- 所有 tools/*.py - 工具实现不变

## 验收标准

1. MiniMax 模型能正确解析 `<minimax:tool_call>` XML，执行对应工具
2. 多轮工具调用正常（如：先 search_skills → 再 shell → 再 file_read）
3. GLM 等标准模型走 OpenAICompatAdapter，行为不变
4. config 中 `native_tool_calling` 字段废弃，由 adapter 自行判断
5. 170 个现有测试全部通过
6. 新增 adapter 测试覆盖 MiniMax XML 解析 + 标准格式解析
