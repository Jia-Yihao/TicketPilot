"""架构依赖守卫：把 `docs/architecture-guard.yaml` 里的约束真的跑起来。

这不是代码风格检查。它回答的是一个架构问题：

    **这个包还认不认自己写在文档里的分层？**

分层写在 `docs/architecture-guard.yaml` 里（唯一事实源），
这个模块负责把约束执行成断言。规则有六条：

1. `app/` 下每个含 `.py` 的目录都必须在 `tiers` 里声明——加层是做决定，不是顺手的事。
2. 跨层依赖只能严格向下（tier 大的指向 tier 小的）。反向依赖和同层跨层耦合
   必须登记在 `exceptions` 里，并写清为什么。
3. `forbidden` 里的边一律不许出现，哪怕方向是向下的。
4. 层图里不许有互环；每一对互环至少有一个方向必须登记在 `exceptions` 里。
5. 不许有模块级 import 环（这类环在 Python 运行时才炸，测试里先炸）。
6. 登记不许腐化：`exceptions` 与 `known_deviations` 里的每条边必须真的还在用。

失败信息一律带 `文件:行号`，让人能直接跳过去改。
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
POLICY_FILE = ROOT / "docs" / "architecture-guard.yaml"


@dataclass(frozen=True)
class Edge:
    """一条跨模块的 import 依赖。"""

    source_file: str
    line: int
    source_layer: str
    target_module: str
    target_layer: str


@dataclass(frozen=True)
class Policy:
    tiers: dict[str, int]
    roots: frozenset[str]
    exceptions: tuple[tuple[str, str, str], ...]
    forbidden: tuple[tuple[str, str, str], ...]
    known_deviations: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class ImportGraph:
    """`app/` 的 import 图：层之间的边 + 模块之间的边。"""

    edges: tuple[Edge, ...]
    layer_edges: dict[str, set[str]]
    module_edges: dict[str, set[str]]


def _module_name(path: pathlib.Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _layer_of_module(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) < 2 or parts[0] != "app":
        return None
    return parts[1]


def _layer_of_file(path: pathlib.Path) -> str:
    relative = path.relative_to(APP_DIR)
    if len(relative.parts) == 1:
        return relative.stem
    return relative.parts[0]


def _imported_modules(tree: ast.AST) -> Iterator[tuple[str, int]]:
    """树里所有指向 `app.*` 的 import（函数体内的也算，它们同样是依赖）。"""

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "app":
                    yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or not node.module:
                continue
            if node.module.split(".")[0] != "app":
                continue
            if node.module == "app":
                for alias in node.names:
                    yield f"app.{alias.name}", node.lineno
            else:
                yield node.module, node.lineno


def _top_level_imported_modules(tree: ast.Module, known_modules: set[str]) -> set[str]:
    """只在模块顶层求值的 import——只有这些会造成「导入即死锁」。

    父包这条边要特意去掉：`from app.models.approval import Approval` 在运行时会
    先执行 `app/models/__init__.py`，那是 Python 的导入机制，不是设计出来的依赖。
    把这条边算进去，`app.models` 与自己导入的每个模型模块之间都会变成「环」。
    """

    modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "app":
                    modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or not node.module:
                continue
            if node.module.split(".")[0] != "app":
                continue
            if node.module == "app":
                modules.update(f"app.{alias.name}" for alias in node.names)
                continue
            leaves = {
                f"{node.module}.{alias.name}"
                for alias in node.names
                if f"{node.module}.{alias.name}" in known_modules
            }
            if leaves:
                modules |= leaves
            else:
                modules.add(node.module)
    return modules


def _load_policy() -> Policy:
    raw = yaml.safe_load(POLICY_FILE.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), "架构策略必须是一份映射"
    tiers = {str(name): int(level) for name, level in raw["tiers"].items()}
    roots = frozenset(str(name) for name in raw.get("roots", []))
    exceptions = tuple(
        (str(item["from"]), str(item["to"]), str(item["why"]))
        for item in raw.get("exceptions", [])
    )
    forbidden = tuple(
        (str(item["from"]), str(item["to"]), str(item["why"]))
        for item in raw.get("forbidden", [])
    )
    known_deviations = tuple(
        (str(item["edge"]).split("->")[0].strip(), str(item["edge"]).split("->")[1].strip(),
         str(item["why"]))
        for item in raw.get("known_deviations", [])
    )
    return Policy(
        tiers=tiers,
        roots=roots,
        exceptions=exceptions,
        forbidden=forbidden,
        known_deviations=known_deviations,
    )


def _build_graph() -> ImportGraph:
    known_modules: set[str] = set()
    parsed: dict[str, ast.Module] = {}
    sources: dict[str, str] = {}
    for path in sorted(APP_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = _module_name(path)
        known_modules.add(module)
        parsed[module] = ast.parse(path.read_text(encoding="utf-8"))
        sources[module] = path.relative_to(ROOT).as_posix()

    edges: list[Edge] = []
    layer_edges: dict[str, set[str]] = {}
    module_edges: dict[str, set[str]] = {}

    for module, tree in parsed.items():
        source_file = sources[module]
        for target, line in _imported_modules(tree):
            target_layer = _layer_of_module(target)
            if target_layer is None:
                continue
            edges.append(
                Edge(
                    source_file=str(source_file),
                    line=line,
                    source_layer=_layer_of_module(module) or "",
                    target_module=target,
                    target_layer=target_layer,
                )
            )
            layer_edges.setdefault(_layer_of_module(module) or "", set()).add(target_layer)
        module_edges[module] = _top_level_imported_modules(tree, known_modules)

    return ImportGraph(
        edges=tuple(edges),
        layer_edges=layer_edges,
        module_edges=module_edges,
    )


@pytest.fixture(scope="module")
def policy() -> Policy:
    return _load_policy()


@pytest.fixture(scope="module")
def graph() -> ImportGraph:
    return _build_graph()


def _declared_layer_dirs() -> list[str]:
    return sorted(
        path.name
        for path in APP_DIR.iterdir()
        if path.is_dir() and any(path.glob("*.py"))
    )


def test_every_layer_directory_is_declared(policy: Policy) -> None:
    """规则 1：新增一层目录必须先在策略里声明，否则守卫直接失败。"""

    undeclared = [name for name in _declared_layer_dirs() if name not in policy.tiers]
    assert not undeclared, (
        "以下目录含 .py 但没有在 docs/architecture-guard.yaml 的 tiers 里声明："
        f"{undeclared}。加层要做决定：先写清它在分层里的位置，再改代码。"
    )


def test_dependencies_only_point_strictly_down(
    policy: Policy, graph: ImportGraph
) -> None:
    """规则 2：跨层依赖只能严格向下；反向与同层耦合必须登记在 exceptions。"""

    allowed = {(source, target) for source, target, _ in policy.exceptions}
    violations: list[str] = []
    for edge in graph.edges:
        source, target = edge.source_layer, edge.target_layer
        if source == target:
            continue
        if source in policy.roots or target in policy.roots:
            continue
        if source not in policy.tiers or target not in policy.tiers:
            continue
        if policy.tiers[source] > policy.tiers[target]:
            continue
        if (source, target) in allowed:
            continue
        kind = "反向依赖" if policy.tiers[source] < policy.tiers[target] else "同层跨层耦合"
        violations.append(
            f"{edge.source_file}:{edge.line}  {source} -> {target}"
            f"（{kind}，tier {policy.tiers[source]} -> {policy.tiers[target]}）"
        )
    assert not violations, (
        "出现了未登记的反向依赖 / 同层耦合：\n  "
        + "\n  ".join(violations)
        + "\n正解是改代码；确实要保留的，写进 docs/architecture-guard.yaml "
        "的 exceptions 并写清 why。"
    )


def test_forbidden_edges_do_not_exist(policy: Policy, graph: ImportGraph) -> None:
    """规则 3：forbidden 里的边一律不许出现，方向向下也不行。"""

    banned = {(source, target) for source, target, _ in policy.forbidden}
    hits: list[str] = []
    for edge in graph.edges:
        source, target = edge.source_layer, edge.target_layer
        if source == target or source in policy.roots:
            continue
        if (source, target) in banned:
            hits.append(f"{edge.source_file}:{edge.line}  {source} -> {target}")
    assert not hits, "出现了明确禁止的依赖：\n  " + "\n  ".join(hits)


def test_layer_graph_has_no_unregistered_mutual_pairs(
    policy: Policy, graph: ImportGraph
) -> None:
    """规则 4：层与层互相依赖时，至少有一个方向必须登记在 exceptions。"""

    registered = {(source, target) for source, target, _ in policy.exceptions}
    mutual: list[str] = []
    for source, targets in graph.layer_edges.items():
        if source in policy.roots:
            continue
        for target in targets:
            if target in policy.roots or source == target:
                continue
            if source not in graph.layer_edges:
                continue
            if source not in graph.layer_edges.get(target, set()):
                continue
            if (source, target) in registered or (target, source) in registered:
                continue
            mutual.append(f"{source} <-> {target}")
    assert not mutual, (
        "层图里出现了未登记的互环：\n  "
        + "\n  ".join(sorted(mutual))
        + "\n互环说明两层职责没分开：要么真的该拆，要么把其中一个方向登记进 exceptions 并写清 why。"
    )


def test_no_module_level_import_cycles(graph: ImportGraph) -> None:
    """规则 5：模块级 import 不许成环（Tarjan 强连通分量）。"""

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    counter = 0
    components: list[list[str]] = []

    def strongconnect(node: str) -> None:
        nonlocal counter
        index[node] = low[node] = counter
        counter += 1
        stack.append(node)
        on_stack.add(node)
        for neighbour in sorted(graph.module_edges.get(node, set())):
            if neighbour not in graph.module_edges:
                continue
            if neighbour not in index:
                strongconnect(neighbour)
                low[node] = min(low[node], low[neighbour])
            elif neighbour in on_stack:
                low[node] = min(low[node], index[neighbour])
        if low[node] == index[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                components.append(sorted(component))

    for module in sorted(graph.module_edges):
        if module not in index:
            strongconnect(module)

    assert not components, (
        "出现了模块级 import 环（导入时会直接炸）：\n  "
        + "\n  ".join(" <-> ".join(component) for component in components)
    )


def test_registered_exceptions_are_still_used(policy: Policy, graph: ImportGraph) -> None:
    """规则 6a：登记的反向依赖必须真的还在用；修好了就要删掉登记。"""

    present = {
        (edge.source_layer, edge.target_layer)
        for edge in graph.edges
        if edge.source_layer != edge.target_layer
    }
    stale = [
        f"{source} -> {target}（{why}）"
        for source, target, why in policy.exceptions
        if (source, target) not in present
    ]
    assert not stale, (
        "这些登记的反向依赖已经不存在了，请从 docs/architecture-guard.yaml 里删掉：\n  "
        + "\n  ".join(stale)
    )


def test_known_deviations_are_still_true(policy: Policy, graph: ImportGraph) -> None:
    """规则 6b：记录在案的偏离必须还是真的偏离，否则记录就是假的。"""

    present = {
        (edge.source_layer, edge.target_layer)
        for edge in graph.edges
        if edge.source_layer != edge.target_layer
    }
    stale = [
        f"{source} -> {target}（{why}）"
        for source, target, why in policy.known_deviations
        if (source, target) not in present
    ]
    assert not stale, (
        "这些记录在案的偏离已经不存在了，请从 docs/architecture-guard.yaml 里删掉：\n  "
        + "\n  ".join(stale)
    )


def test_every_registered_edge_has_a_reason(policy: Policy) -> None:
    """登记必须写清 why——写不清就说明它该被改掉，而不是被登记。"""

    for source, target, why in policy.exceptions + policy.forbidden + policy.known_deviations:
        assert why.strip(), f"登记 {source} -> {target} 没有写 why"
