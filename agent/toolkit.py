"""Toolkit layer: turn plain Python methods into OpenAI function-calling tools.

Inspired by agno's `Toolkit`/`Function` design: a tool is just a typed,
documented Python method. Its JSON schema is derived from the signature and
the Google-style docstring, so the schema can never drift out of sync with
the implementation the way a hand-written schema table does.
"""
from __future__ import annotations

import inspect
import json
import re
import typing
from dataclasses import dataclass, field

_JSON_TYPES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}

_ARGS_HEADER_RE = re.compile(r"^\s*(Args|Arguments|Parameters)\s*:\s*$")
_ARG_LINE_RE = re.compile(r"^\s*(\w+)\s*(?:\([^)]*\))?\s*:\s*(.*)$")


def tool(func):
    """Mark a method as an agent tool. Its schema is built from the signature."""
    func._is_agent_tool = True
    return func


def parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    """Split a Google-style docstring into a summary and per-argument docs.

    Args:
        doc: The raw ``__doc__`` of a tool function, or None.

    Returns:
        A (description, {arg_name: arg_description}) pair.
    """
    if not doc:
        return "", {}

    lines = inspect.cleandoc(doc).splitlines()
    summary: list[str] = []
    args: dict[str, str] = {}
    current: str | None = None
    in_args = False

    for line in lines:
        if _ARGS_HEADER_RE.match(line):
            in_args = True
            current = None
            continue
        if in_args:
            # Any other "Section:" header (Returns:, Raises:, ...) ends the block.
            if re.match(r"^\s*\w+\s*:\s*$", line) and not _ARG_LINE_RE.match(line):
                break
            match = _ARG_LINE_RE.match(line)
            if match:
                current = match.group(1)
                args[current] = match.group(2).strip()
            elif current and line.strip():
                args[current] += " " + line.strip()
        else:
            summary.append(line)

    return " ".join(s.strip() for s in summary if s.strip()), args


def _json_type(annotation) -> dict:
    if annotation is inspect.Parameter.empty:
        return {"type": "string"}
    origin = typing.get_origin(annotation)
    if origin is not None:
        # Optional[X] / X | None -> the schema of X.
        non_none = [a for a in typing.get_args(annotation) if a is not type(None)]
        if origin in (typing.Union, getattr(__import__("types"), "UnionType", None)) and non_none:
            return _json_type(non_none[0])
        return {"type": _JSON_TYPES.get(origin, "string")}
    return {"type": _JSON_TYPES.get(annotation, "string")}


@dataclass
class Function:
    """One callable tool plus the JSON schema the model sees for it."""

    name: str
    description: str
    entrypoint: typing.Callable
    parameters: dict = field(default_factory=dict)

    @classmethod
    def from_callable(cls, func: typing.Callable) -> "Function":
        description, arg_docs = parse_docstring(func.__doc__)
        signature = inspect.signature(func)
        # `from __future__ import annotations` leaves annotations as strings,
        # so resolve them to real types before mapping them to JSON types.
        try:
            hints = typing.get_type_hints(func)
        except Exception:  # noqa: BLE001 - fall back to the raw annotations
            hints = {}

        properties: dict[str, dict] = {}
        required: list[str] = []
        for param_name, param in signature.parameters.items():
            if param_name in ("self", "cls"):
                continue
            schema = _json_type(hints.get(param_name, param.annotation))
            if param_name in arg_docs:
                schema["description"] = arg_docs[param_name]
            if param.default is inspect.Parameter.empty:
                required.append(param_name)
            else:
                schema["default"] = param.default
            properties[param_name] = schema

        return cls(
            name=func.__name__,
            description=description,
            entrypoint=func,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        )

    def to_schema(self) -> dict:
        """Render this function in OpenAI `tools=[...]` format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def run(self, arguments_json: str) -> str:
        try:
            args = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError as exc:
            return f"ERROR: invalid tool arguments JSON: {exc}"
        if not isinstance(args, dict):
            return "ERROR: tool arguments must be a JSON object"

        allowed = set(inspect.signature(self.entrypoint).parameters)
        unknown = set(args) - allowed
        if unknown:
            return f"ERROR: unknown argument(s) for {self.name}: {', '.join(sorted(unknown))}"
        try:
            return str(self.entrypoint(**args))
        except TypeError as exc:
            return f"ERROR: bad arguments for {self.name}: {exc}"
        except Exception as exc:  # noqa: BLE001 - the model should see the failure
            return f"ERROR: {self.name} failed: {type(exc).__name__}: {exc}"


class Toolkit:
    """Base class: every ``@tool``-decorated method becomes a Function."""

    def __init__(self):
        self.functions: dict[str, Function] = {}
        for name in dir(type(self)):
            attribute = getattr(type(self), name, None)
            if callable(attribute) and getattr(attribute, "_is_agent_tool", False):
                self.functions[name] = Function.from_callable(getattr(self, name))

    @property
    def schemas(self) -> list[dict]:
        return [f.to_schema() for f in self.functions.values()]

    def call(self, name: str, arguments_json: str) -> str:
        function = self.functions.get(name)
        if function is None:
            return f"ERROR: unknown tool '{name}'"
        return function.run(arguments_json)
