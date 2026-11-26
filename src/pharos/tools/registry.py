"""Explicit registration and strict validation; never dispatch arbitrary Python."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, ValidationError


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ToolError(ValueError):
    pass


@dataclass(frozen=True)
class ToolResult:
    data: dict
    terminal: bool = False


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    arguments: type[Arguments]
    execute: Callable[[Arguments], Awaitable[ToolResult]]


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ToolError("duplicate_argument")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ToolError("invalid_number")


def _inline_schema(schema):
    """Expand local Pydantic references for OpenAI-compatible runtimes."""
    definitions = schema.get("$defs", {})
    def walk(value, depth=0):
        if depth > 30:
            raise ValueError("recursive_tool_schema")
        if isinstance(value, list):
            return [walk(item, depth + 1) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            ref = value["$ref"]
            if not ref.startswith("#/$defs/") or ref[8:] not in definitions:
                raise ValueError("invalid_tool_schema_reference")
            merged = {**definitions[ref[8:]], **{k: v for k, v in value.items() if k != "$ref"}}
            return walk(merged, depth + 1)
        return {k: walk(v, depth + 1) for k, v in value.items() if k not in ("$defs", "title")}
    return walk(schema)


class Registry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", tool.name) or tool.name in self._tools:
            raise ValueError("invalid_tool_registration")
        self._tools[tool.name] = tool

    def contains(self, name):
        return isinstance(name, str) and name in self._tools

    def definitions(self):
        return [{"type": "function", "function": {
            "name": t.name, "description": t.description,
            "parameters": _inline_schema(t.arguments.model_json_schema())}}
            for t in self._tools.values()]

    def validate(self, name, raw):
        if not self.contains(name):
            raise ToolError("unknown_tool")
        if not isinstance(raw, str) or len(raw.encode("utf-8", errors="replace")) > 4096:
            raise ToolError("invalid_arguments")
        try:
            parsed = json.loads(raw, object_pairs_hook=_unique, parse_constant=_invalid_constant)
            if not isinstance(parsed, dict):
                raise ToolError("invalid_arguments")
            return self._tools[name], self._tools[name].arguments.model_validate(parsed)
        except (ValueError, TypeError, ValidationError, RecursionError) as exc:
            raise ToolError("invalid_arguments") from exc
