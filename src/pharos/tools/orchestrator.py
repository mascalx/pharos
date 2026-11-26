"""Bounded native tool protocol, independent of geographic services."""
from __future__ import annotations

import asyncio
import json
import logging
import time

from .registry import ToolError
from ..ai import GatewayError

log = logging.getLogger(__name__)
MAX_INFERENCES = 4
MAX_CALLS = 6
TOTAL_SECONDS = 180
MAX_TOOL_RESULT_BYTES = 16384


def _preview(value, limit):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        return {"unserializable": True}
    return json.loads(raw) if len(raw) <= limit else {"truncated": True, "preview": raw[:limit]}


async def run(provider, model, messages, registry, *, thinking=None, timeout=TOTAL_SECONDS, tool_choice="auto"):
    start = time.monotonic()
    deadline = start + min(TOTAL_SECONDS, timeout)
    trace = []
    messages = list(messages)
    count = 0
    seen = set()
    executed = set()
    last_result = None
    definitions = registry.definitions()

    def done(status, result=None):
        return {"status": status, "result": result, "trace": trace,
                "elapsed_seconds": round(time.monotonic() - start, 3)}

    async def bounded(awaitable):
        return await asyncio.wait_for(awaitable, max(0.001, deadline - time.monotonic()))

    try:
        for _ in range(MAX_INFERENCES):
            tick = time.monotonic()
            try:
                response = await bounded(provider.complete_tools(
                    model, messages, definitions, thinking=thinking, tool_choice=tool_choice))
            finally:
                trace.append({"stage": "inference", "seconds": round(time.monotonic()-tick, 3)})
            message = response.get("message") if isinstance(response, dict) else None
            # Report only protocol fields, never headers, credentials or reasoning fields.
            trace[-1]["finish_reason"] = str(response.get("finish_reason", ""))[:80] if isinstance(response, dict) else ""
            if isinstance(message, dict):
                trace[-1]["content"] = str(message.get("content") or "")[:2000]
                trace[-1]["tool_calls"] = _preview(message.get("tool_calls"), 8192)
            if not isinstance(message, dict):
                return done("invalid_response", {"error": "invalid_message_envelope"})
            calls = message.get("tool_calls")
            if calls is None or calls == []:
                if response.get("finish_reason") in ("length", "tool_calls"):
                    return done("invalid_response", {"error": "missing_or_truncated_tool_calls"})
                return done("no_tool_call", last_result)
            if not isinstance(calls, list) or len(calls) != 1:
                return done("invalid_response", {"error": "expected_one_tool_call"})
            call = calls[0]
            if not isinstance(call, dict) or call.get("type") != "function":
                return done("invalid_response", {"error": "invalid_call_type"})
            call_id = call.get("id")
            function = call.get("function")
            if (not isinstance(call_id, str) or not 1 <= len(call_id) <= 256
                    or call_id in seen or not isinstance(function, dict)):
                return done("invalid_response", {"error": "invalid_or_duplicate_call_id"})
            seen.add(call_id)
            count += 1
            if count > MAX_CALLS:
                return done("limit", last_result)
            name, raw = function.get("name"), function.get("arguments")
            if not isinstance(name, str) or len(name) > 64 or not isinstance(raw, str) or len(raw) > 4096:
                return done("invalid_response", {"error": "invalid_call_arguments_envelope"})
            tick = time.monotonic()
            detail = {"stage": "tool", "name": name if registry.contains(name) else "unknown",
                      "arguments": raw[:4096], "validation": "pending"}
            try:
                tool, arguments = registry.validate(name, raw)
                detail["validation"] = "passed"
                fingerprint = (name, json.dumps(arguments.model_dump(), sort_keys=True, ensure_ascii=False))
                if fingerprint in executed:
                    raise ToolError("repeated_call: choose different filters or another tool")
                executed.add(fingerprint)
                result = await bounded(tool.execute(arguments))
                encoded = json.dumps(result.data, ensure_ascii=False, allow_nan=False)
                if len(encoded.encode("utf-8")) > MAX_TOOL_RESULT_BYTES:
                    return done("result_too_large")
                detail["result"] = _preview(result.data, 8192)
                detail["terminal"] = result.terminal
                if result.terminal:
                    return done("completed", result.data)
                last_result = result.data
            except ToolError as exc:
                detail["validation"] = "rejected" if detail["validation"] == "pending" else "execution_rejected"
                detail["error"] = str(exc)[:240]
                last_result = {"error": str(exc)}
                encoded = json.dumps(last_result)
            finally:
                detail["seconds"] = round(time.monotonic()-tick, 3)
                trace.append(detail)
            messages.append({"role": "assistant", "content": None,
                             "tool_calls": [{"id": call_id, "type": "function",
                                             "function": {"name": name, "arguments": raw}}]})
            messages.append({"role": "tool", "tool_call_id": call_id, "content": encoded})
        return done("limit", last_result)
    except (asyncio.TimeoutError, TimeoutError):
        return done("timeout")
    except GatewayError as exc:
        return done(str(exc))
    finally:
        log.info("AI tools: steps=%d elapsed=%.3fs", len(trace), time.monotonic()-start)
