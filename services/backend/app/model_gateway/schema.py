"""Pydantic model -> provider JSON schema.

Providers accept a subset of JSON Schema. References are inlined and keys the
provider may reject are dropped; the gateway still validates every response
against the full Pydantic model, so nothing is lost by simplifying here.
"""

from typing import Any

from pydantic import BaseModel

_DROP_KEYS = {"title", "default", "additionalProperties", "$defs", "definitions"}


def provider_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})

    def resolve(node: Any, depth: int = 0) -> Any:
        if depth > 32:
            raise ValueError(f"schema for {model.__name__} is too deeply nested or recursive")
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].rsplit("/", 1)[-1]
                merged = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
                return resolve(merged, depth + 1)
            out: dict[str, Any] = {}
            for key, value in node.items():
                if key in _DROP_KEYS:
                    continue
                if key == "const":
                    out["enum"] = [value]
                    continue
                if key == "properties":
                    out[key] = {name: resolve(prop, depth + 1) for name, prop in value.items()}
                    continue
                out[key] = resolve(value, depth + 1)
            return out
        if isinstance(node, list):
            return [resolve(item, depth + 1) for item in node]
        return node

    return resolve(schema)
