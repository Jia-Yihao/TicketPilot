from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import NotFoundError
from app.tools.base import ToolHandler, ToolManifest
from app.tools.executor import receipt_placeholders


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    manifest: ToolManifest
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, manifest: ToolManifest, handler: ToolHandler) -> None:
        if manifest.name in self._tools:
            raise ValueError(f"Tool already registered: {manifest.name}")
        _check_receipt(manifest)
        self._tools[manifest.name] = RegisteredTool(manifest=manifest, handler=handler)

    def require(self, name: str) -> RegisteredTool:
        tool = self._tools.get(name)
        if tool is None:
            raise NotFoundError(f"Tool not found: {name}.")
        return tool

    def manifests(self) -> list[ToolManifest]:
        return [tool.manifest for tool in self._tools.values()]


def _check_receipt(manifest: ToolManifest) -> None:
    """A receipt may only quote a field the result schema declares.

    The receipt is shown to the user as the tool's own word. If it could
    name a field the tool never returns, the sentence would be filled from
    somewhere else — and somewhere else is the model.
    """
    if not manifest.receipt:
        return
    declared = manifest.result_schema.get("properties", {})
    unknown = [
        name
        for name in receipt_placeholders(manifest.receipt)
        if name not in declared
    ]
    if unknown:
        raise ValueError(
            f"Tool {manifest.name} has a receipt that quotes undeclared "
            f"field(s): {', '.join(unknown)}."
        )
