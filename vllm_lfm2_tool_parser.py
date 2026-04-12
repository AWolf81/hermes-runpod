import ast
import json
import re
from collections.abc import Sequence

from vllm.entrypoints.chat_utils import make_tool_call_id
from vllm.entrypoints.openai.protocol import (
    ChatCompletionRequest,
    DeltaFunctionCall,
    DeltaMessage,
    DeltaToolCall,
    ExtractedToolCallInformation,
    FunctionCall,
    ToolCall,
)
from vllm.logger import init_logger
from vllm.tokenizers import TokenizerLike
from vllm.tool_parsers.abstract_tool_parser import ToolParser, ToolParserManager

logger = init_logger(__name__)


@ToolParserManager.register_module(["lfm2", "liquid_lfm2"])
class LFM2ToolParser(ToolParser):
    """
    Parser for Liquid LFM2.x tool-call delimiters:
      <|tool_call_start|> ... <|tool_call_end|>

    Payload styles supported:
    - Python call: skill_view(skill_name="repo-view")
    - Python list: [tool_a(x=1), tool_b(y=2)]
    - JSON object/list with {"name": ..., "arguments": ...}
    """

    def __init__(self, tokenizer: TokenizerLike):
        super().__init__(tokenizer)
        self.tool_call_start_token = "<|tool_call_start|>"
        self.tool_call_end_token = "<|tool_call_end|>"
        self.tool_block_regex = re.compile(
            r"<\|tool_call_start\|>\s*(?P<payload>.*?)\s*<\|tool_call_end\|>",
            re.DOTALL,
        )

        self._emitted_block_count = 0
        self._pending_messages: list[DeltaMessage] = []

    def adjust_request(self, request: ChatCompletionRequest) -> ChatCompletionRequest:
        request = super().adjust_request(request)
        if request.tools and request.tool_choice != "none":
            # Keep special tokens so parser can see <|tool_call_*|> delimiters.
            request.skip_special_tokens = False
        return request

    @staticmethod
    def _function_name(func_node: ast.AST) -> str | None:
        if isinstance(func_node, ast.Name):
            return func_node.id
        if isinstance(func_node, ast.Attribute):
            parts: list[str] = []
            current: ast.AST = func_node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            if parts:
                return parts[0]
        return None

    @staticmethod
    def _to_python(node: ast.AST):
        try:
            return ast.literal_eval(node)
        except Exception:
            if isinstance(node, ast.Name):
                return node.id
            return ast.unparse(node)

    def _parse_python_calls(self, payload: str) -> list[dict]:
        payload = payload.strip()
        if not payload:
            return []

        parsed = ast.parse(payload, mode="eval").body

        call_nodes: list[ast.Call]
        if isinstance(parsed, ast.Call):
            call_nodes = [parsed]
        elif isinstance(parsed, ast.List) and all(
            isinstance(element, ast.Call) for element in parsed.elts
        ):
            call_nodes = list(parsed.elts)
        else:
            raise ValueError("Unsupported LFM2 tool-call payload format")

        out: list[dict] = []
        for call_node in call_nodes:
            function_name = self._function_name(call_node.func)
            if not function_name:
                continue

            kwargs: dict = {}
            for kw in call_node.keywords:
                if kw.arg is None:
                    unpacked = self._to_python(kw.value)
                    if isinstance(unpacked, dict):
                        kwargs.update(unpacked)
                    else:
                        kwargs["_kwargs"] = unpacked
                else:
                    kwargs[kw.arg] = self._to_python(kw.value)

            positional_args = [self._to_python(arg) for arg in call_node.args]
            if positional_args:
                if not kwargs and len(positional_args) == 1 and isinstance(
                    positional_args[0], dict
                ):
                    arguments = positional_args[0]
                else:
                    arguments = dict(kwargs)
                    arguments["_args"] = positional_args
            else:
                arguments = kwargs

            out.append({"name": function_name, "arguments": arguments})

        return out

    def _normalize_json_calls(self, payload: str) -> list[dict]:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return []

        items = data if isinstance(data, list) else [data]
        out: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            arguments = item.get("arguments", {})
            if not isinstance(arguments, dict):
                arguments = {"value": arguments}
            out.append({"name": name, "arguments": arguments})
        return out

    def _parse_payload(self, payload: str) -> list[dict]:
        payload = payload.strip()
        if not payload:
            return []

        json_calls = self._normalize_json_calls(payload)
        if json_calls:
            return json_calls

        return self._parse_python_calls(payload)

    @staticmethod
    def _to_tool_call(call: dict) -> ToolCall:
        return ToolCall(
            type="function",
            function=FunctionCall(
                name=call["name"],
                arguments=json.dumps(call["arguments"], ensure_ascii=False),
            ),
        )

    def _to_delta_message(self, call: dict) -> DeltaMessage:
        self.current_tool_id += 1
        tool_index = self.current_tool_id
        arguments_json = json.dumps(call["arguments"], ensure_ascii=False)

        # Keep vLLM streaming state in sync so finish-time arg checks work.
        self.prev_tool_call_arr.append(
            {"name": call["name"], "arguments": call["arguments"]}
        )
        self.streamed_args_for_tool.append(arguments_json)

        return DeltaMessage(
            tool_calls=[
                DeltaToolCall(
                    index=tool_index,
                    id=make_tool_call_id(),
                    type="function",
                    function=DeltaFunctionCall(
                        name=call["name"],
                        arguments=arguments_json,
                    ).model_dump(exclude_none=True),
                )
            ]
        )

    def extract_tool_calls(
        self,
        model_output: str,
        request: ChatCompletionRequest,
    ) -> ExtractedToolCallInformation:
        matches = list(self.tool_block_regex.finditer(model_output))
        if not matches:
            return ExtractedToolCallInformation(
                tools_called=False,
                tool_calls=[],
                content=model_output,
            )

        parsed_calls: list[dict] = []
        for match in matches:
            payload = match.group("payload")
            try:
                parsed_calls.extend(self._parse_payload(payload))
            except Exception:
                logger.exception("Failed to parse LFM2 tool payload: %s", payload)

        if not parsed_calls:
            return ExtractedToolCallInformation(
                tools_called=False,
                tool_calls=[],
                content=model_output,
            )

        content_chunks: list[str] = []
        cursor = 0
        for match in matches:
            if match.start() > cursor:
                content_chunks.append(model_output[cursor : match.start()])
            cursor = match.end()
        if cursor < len(model_output):
            content_chunks.append(model_output[cursor:])

        content = "".join(content_chunks).strip() or None
        tool_calls = [self._to_tool_call(call) for call in parsed_calls]

        return ExtractedToolCallInformation(
            tools_called=True,
            tool_calls=tool_calls,
            content=content,
        )

    def extract_tool_calls_streaming(
        self,
        previous_text: str,
        current_text: str,
        delta_text: str,
        previous_token_ids: Sequence[int],
        current_token_ids: Sequence[int],
        delta_token_ids: Sequence[int],
        request: ChatCompletionRequest,
    ) -> DeltaMessage | None:
        if not previous_text:
            self._emitted_block_count = 0
            self._pending_messages = []
            self.prev_tool_call_arr = []
            self.streamed_args_for_tool = []
            self.current_tool_id = -1
            self.current_tool_name_sent = False

        if self._pending_messages:
            return self._pending_messages.pop(0)

        # No active tool call delimiters seen: pass text through.
        if self.tool_call_start_token not in current_text:
            return DeltaMessage(content=delta_text)

        matches = list(self.tool_block_regex.finditer(current_text))

        # Delimiter started, but no complete block yet. Suppress raw tool text.
        if not matches:
            return None

        # Emit any plain-text prefix before the first tool block once.
        if self._emitted_block_count == 0:
            first_start = matches[0].start()
            if first_start > 0 and self.tool_call_start_token not in previous_text:
                prefix = current_text[:first_start]
                if prefix:
                    return DeltaMessage(content=prefix)

        if len(matches) <= self._emitted_block_count:
            # All complete blocks were already emitted. Suppress delimiter chatter.
            return None

        match = matches[self._emitted_block_count]
        self._emitted_block_count += 1

        payload = match.group("payload")
        try:
            calls = self._parse_payload(payload)
        except Exception:
            logger.exception("Failed to parse streaming LFM2 tool payload: %s", payload)
            return None

        if not calls:
            return None

        messages = [self._to_delta_message(call) for call in calls]
        first_message = messages[0]
        if len(messages) > 1:
            self._pending_messages.extend(messages[1:])
        return first_message
