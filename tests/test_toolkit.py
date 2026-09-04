"""Tests for the schema-from-signature toolkit layer."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json

from agent.toolkit import Toolkit, parse_docstring, tool


class Sample(Toolkit):
    @tool
    def greet(self, name: str, times: int = 1, loud: bool = False) -> str:
        """Greet someone by name.

        Args:
            name: Who to greet.
            times: How many times to repeat the greeting.
            loud: Whether to shout.
        """
        text = " ".join([f"hello {name}"] * times)
        return text.upper() if loud else text

    @tool
    def boom(self) -> str:
        """Always fails."""
        raise ValueError("kaboom")

    def not_a_tool(self) -> str:
        """Should never be exposed to the model."""
        return "nope"


def test_parse_docstring_splits_summary_and_args():
    summary, args = parse_docstring(Sample.greet.__doc__)
    assert summary == "Greet someone by name."
    assert args["name"] == "Who to greet."
    assert args["loud"] == "Whether to shout."


def test_only_decorated_methods_become_tools():
    assert set(Sample().functions) == {"greet", "boom"}


def test_schema_types_and_required_come_from_the_signature():
    params = Sample().functions["greet"].parameters
    assert params["properties"]["name"] == {"type": "string", "description": "Who to greet."}
    assert params["properties"]["times"]["type"] == "integer"
    assert params["properties"]["times"]["default"] == 1
    assert params["properties"]["loud"]["type"] == "boolean"
    assert params["required"] == ["name"]
    assert params["additionalProperties"] is False


def test_schema_is_openai_tool_shaped():
    schema = next(s for s in Sample().schemas if s["function"]["name"] == "greet")
    assert schema["type"] == "function"
    assert schema["function"]["description"] == "Greet someone by name."


def test_call_dispatches_and_applies_defaults():
    assert Sample().call("greet", json.dumps({"name": "ali"})) == "hello ali"
    assert Sample().call("greet", json.dumps({"name": "ali", "times": 2})) == "hello ali hello ali"


def test_bad_input_is_reported_to_the_model_not_raised():
    kit = Sample()
    assert kit.call("greet", "{not json").startswith("ERROR: invalid tool arguments JSON")
    assert kit.call("nope", "{}").startswith("ERROR: unknown tool")
    assert "unknown argument" in kit.call("greet", json.dumps({"name": "a", "zzz": 1}))
    assert "bad arguments" in kit.call("greet", "{}")
    assert "kaboom" in kit.call("boom", "{}")
