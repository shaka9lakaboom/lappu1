"""Pydantic model -> provider JSON schema.

Providers accept a subset of JSON Schema. References are inlined and only the
keywords documented as supported by the Gemini structured-output API are kept
(unsupported ones such as `pattern` or `maxLength` have unspecified behaviour).
Array bounds (`minItems`/`maxItems`) are dropped too: combined with enums they make
Gemini reject the request as too complex (HTTP 400 "invalid argument", observed live
on gemini-3.7-flash and gemini-3.8-flash).
The gateway still validates every response against the full Pydantic model, so
a dropped constraint is enforced there and a violation goes through the repair.
"""

from typing import Any

from pydantic import BaseModel

_KEEP_KEYS = {
    "type",
    "description",
    "properties",
    "required",
    "enum",
    "format",
    "minimum",
    "maximum",
    "items",
    "prefixItems",
    "anyOf",
}


def provider_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})

    def resolve(node: Any, depth: int = 0) -> Any:
        if depth > 32:
            raise ValueError(f"schema for {model.__name__} is too deeply nested or recursive")
        if isinstance(node, list):
            return [resolve(item, depth + 1) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            merged = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
            return resolve(merged, depth + 1)
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "const":
                out["enum"] = [value]
            elif key == "properties":
                out[key] = {name: resolve(prop, depth + 1) for name, prop in value.items()}
            elif key in _KEEP_KEYS:
                out[key] = resolve(value, depth + 1)
        return out

    return resolve(schema)
