"""代码锚点守卫：文档里点名的函数/符号，必须在代码里真的存在。

这是 `test_docs_consistency.py` 的姊妹守卫，守的是另一类假引用：

* 数字守卫（`fact-baseline.yaml`）管「文档抄的数字 == 代码现算的数字」。
* 本守卫管「文档点名的函数/类/字段/枚举成员 == 代码里真的定义了它」。

背景（本轮真踩到的坑）：`docs/下一步规划.md` 一度把退款写路径的处理器写成
`TaskRuntime._request_refund`，而真实函数是 `_handle_refund`——名字根本不存在，
测试却全绿。行号也整批漂移过。光靠「行号没越界」抓不住这种漂移，必须查符号是否存在。

沿用既有清单 + 守卫的模式：

* `docs/code-anchors.yaml` 登记「哪份文档的哪一处引用了哪个源码符号」，
  **锚点里不写行号、不抄数字**（对齐 `test_manifest_contains_no_literal_numbers` 的精神）；
* 本模块用 AST 现算源码里定义的限定名（模块级定义 + 类级成员），
  再逐条比对：文档点名的符号 == 代码里存在的定义名。
* 另外追加一条低成本通用检查：扫描 `docs/**/*.md` 里形如 `` `path.py:NN`` /
  `` `path.py:NN-MM` `` 的锚点，断言文件存在、行号区间不越界；前瞻文档可逐条豁免。

符号存在性用 AST 判断，不用「字符串 in 判断」——后者会把注释与正文里的同名词当成存在。
"""

from __future__ import annotations

import ast
import pathlib
import re
from dataclasses import dataclass

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST_FILE = ROOT / "docs" / "code-anchors.yaml"

# 通用行号扫描只认这些顶层目录开头的仓库相对路径，避免把 `runtime.py:296` 这种
# 裸文件名（多处同名，有歧义）或 `services/agent/...` 这种缺前缀的简写当成断言。
REPO_TOP_DIRS = ("app", "evals", "scripts", "tests", "web", "alembic")

# 形如 `app/services/runtime.py` / `...runtime.py:587` / `...runtime.py:581-587`
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_ANCHOR = re.compile(
    r"^(?P<path>[A-Za-z0-9_./\-]+\.py)"
    r"(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?$"
)
_FENCE = re.compile(r"```.*?```", re.S)


# --------------------------------------------------------------------------
# 清单结构
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Anchor:
    id: str
    doc: str
    anchor: str
    source_file: str
    symbol: str
    reason: str


@dataclass(frozen=True)
class Exemption:
    file: str
    doc: str
    reason: str


@dataclass(frozen=True)
class Manifest:
    anchors: tuple[Anchor, ...]
    exemptions: tuple[Exemption, ...]


def _load_manifest() -> Manifest:
    raw = yaml.safe_load(MANIFEST_FILE.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), "代码锚点清单必须是一份映射"
    anchors = tuple(
        Anchor(
            id=str(item["id"]),
            doc=str(item["doc"]),
            anchor=str(item["anchor"]),
            source_file=str(item["source_file"]),
            symbol=str(item["symbol"]),
            reason=str(item["reason"]),
        )
        for item in raw.get("anchors", [])
    )
    exemptions = tuple(
        Exemption(
            file=str(item["file"]),
            doc=str(item["doc"]),
            reason=str(item["reason"]),
        )
        for item in raw.get("exempt_files", [])
    )
    return Manifest(anchors=anchors, exemptions=exemptions)


# --------------------------------------------------------------------------
# 现算源码里定义的限定名
# --------------------------------------------------------------------------


def _defined_qualified_names(tree: ast.Module) -> set[str]:
    """模块级定义名 + 类级成员名（方法 / 字段 / 枚举成员）。"""

    names: set[str] = set()

    def add_defs(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(f"{prefix}{node.name}")
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(f"{prefix}{target.id}")
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(f"{prefix}{node.target.id}")

    add_defs(tree.body, "")
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            add_defs(node.body, f"{node.name}.")
    return names


def _source_defined_names(source_file: str) -> set[str]:
    path = ROOT / source_file
    assert path.is_file(), f"清单指向的源码文件不存在：{source_file}"
    return _defined_qualified_names(ast.parse(path.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------
# 定位文档里的那一行（沿用 test_docs_consistency 的锚点定位方式）
# --------------------------------------------------------------------------


def _anchor_line(anchor: Anchor) -> tuple[int, str]:
    path = ROOT / anchor.doc
    assert path.is_file(), f"清单指向的文档不存在：{anchor.doc}"
    lines = path.read_text(encoding="utf-8").splitlines()
    hits = [(index + 1, line) for index, line in enumerate(lines) if anchor.anchor in line]
    assert len(hits) == 1, (
        f"{anchor.doc} 里的锚点 {anchor.anchor!r} 命中 {len(hits)} 行，"
        "必须恰好命中一行（锚点要能唯一定位这一处引用）"
    )
    return hits[0]


def _symbol_leaf(symbol: str) -> str:
    """限定名的最后一段，例如 `TaskRuntime._handle_refund` -> `_handle_refund`。"""

    return symbol.rsplit(".", 1)[-1]


def _scan_doc_path_anchors() -> list[tuple[str, str, str, str | None, str | None]]:
    """返回 (文档, 原始锚点, 路径, 起始行, 结束行)，收**仓库相对全路径**的 `.py` 锚点。

    带行号（`` `path.py:NN` `` / `` `path.py:NN-MM` ``）的既查文件存在也查行号不越界；
    不带行号的裸全路径（`` `path.py` ``）只查文件存在——前瞻文档正是用这种形态描述尚未
    创建的模块，它同样不该被当成现状断言放行。

    只处理以已知顶层目录（app/… 等）开头的锚点。裸文件名（`runtime.py:296`）与缺前缀的
    简写（`services/runtime.py`）有歧义，交给符号清单，不在这条里强制。
    """

    found: list[tuple[str, str, str, str | None, str | None]] = []
    for doc in sorted((ROOT / "docs").rglob("*.md")):
        text = _FENCE.sub("", doc.read_text(encoding="utf-8"))
        for span in _INLINE_CODE.finditer(text):
            raw = span.group(1).strip()
            match = _ANCHOR.match(raw)
            if not match:
                continue
            path = match.group("path")
            # 只处理写成仓库相对全路径（带已知顶层目录前缀）的锚点。
            if not path.startswith(tuple(f"{d}/" for d in REPO_TOP_DIRS)):
                continue
            found.append(
                (
                    doc.relative_to(ROOT).as_posix(),
                    raw,
                    path,
                    match.group("start"),
                    match.group("end"),
                )
            )
    return found


# --------------------------------------------------------------------------
# 测试
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def manifest() -> Manifest:
    return _load_manifest()


def test_manifest_entries_are_wellformed(manifest: Manifest) -> None:
    """清单每条字段齐全，锚点唯一命中且不含数字（锚点只定位位置，不抄事实）。"""

    assert manifest.anchors, "代码锚点清单是空的——至少要登记这次踩到的坑"
    ids: set[str] = set()
    for anchor in manifest.anchors:
        assert anchor.id not in ids, f"清单里 id 重复：{anchor.id}"
        ids.add(anchor.id)
        assert anchor.reason.strip(), f"{anchor.id} 没有写清为什么要登记"
        assert anchor.source_file.endswith(".py"), f"{anchor.id} 的 source_file 不是 .py"
        assert not any(char.isdigit() for char in anchor.anchor), (
            f"{anchor.id} 的锚点 {anchor.anchor!r} 里带数字——"
            "锚点必须只定位位置，不能把行号抄进清单（行号会漂）"
        )
        _anchor_line(anchor)


def test_anchor_line_actually_mentions_the_symbol(manifest: Manifest) -> None:
    """锚点命中的那一行，确实把被登记的符号写了出来（绑定文档与符号，防串位）。"""

    problems: list[str] = []
    for anchor in manifest.anchors:
        number, line = _anchor_line(anchor)
        leaf = _symbol_leaf(anchor.symbol)
        if leaf not in line:
            problems.append(
                f"{anchor.doc}:{number} 的锚点 {anchor.anchor!r} 那一行没有出现 {leaf!r}：\n"
                f"    {line.strip()}"
            )
    assert not problems, "清单登记的符号与文档那行对不上：\n  " + "\n  ".join(problems)


def test_registered_symbols_exist_in_the_source(manifest: Manifest) -> None:
    """文档点名的符号，必须在声明的源码文件里真的被定义（AST 判定）。"""

    problems: list[str] = []
    for anchor in manifest.anchors:
        number, _line = _anchor_line(anchor)
        defined = _source_defined_names(anchor.source_file)
        if anchor.symbol not in defined:
            prefix = anchor.symbol.split(".")[0]
            nearby = sorted(name for name in defined if name.split(".")[0] == prefix)
            problems.append(
                f"{anchor.doc}:{number} 引用了 {anchor.symbol}（{anchor.source_file}），"
                f"但代码里没有这个定义——同名前缀下的定义名有："
                f"{nearby or '（无同名前缀）'}"
            )
    assert not problems, "文档里的代码符号引用是假的：\n  " + "\n  ".join(problems)


def test_source_files_in_manifest_exist(manifest: Manifest) -> None:
    """清单声明的每个源码文件路径都必须存在。"""

    missing = [
        anchor.source_file
        for anchor in manifest.anchors
        if not (ROOT / anchor.source_file).is_file()
    ]
    assert not missing, "清单里这些源码文件路径不存在：\n  " + "\n  ".join(missing)


def test_generic_path_anchors_are_real(manifest: Manifest) -> None:
    """低成本通用检查：`path.py[:NN[-MM]]` 锚点指向的文件要存在，带行号的还不越界。

    只覆盖写成仓库相对全路径（app/evals/scripts/tests/web/alembic 开头）的锚点；
    裸文件名与缺前缀的简写有歧义，交给符号清单，不在这条里强制。
    豁免在 `exempt_files` 逐条登记，且按 (文档, 文件) 二元组生效（见
    test_exemptions_are_specific_and_reasoned 与 test_exemptions_are_all_used）。
    """

    exempt = {(item.doc, item.file) for item in manifest.exemptions}
    problems: list[str] = []
    for doc, raw, path, start, end in _scan_doc_path_anchors():
        if (doc, path) in exempt:
            continue
        source = ROOT / path
        if not source.is_file():
            problems.append(f"{doc} 锚点 {raw!r} 指向的源码文件不存在")
            continue
        if start is None:
            continue
        total = len(source.read_text(encoding="utf-8").splitlines())
        low = int(start)
        high = int(end) if end else low
        if low > high:
            problems.append(f"{doc} 锚点 {raw!r} 行号区间倒置：{low}-{high}")
        elif low < 1 or high > total:
            problems.append(
                f"{doc} 锚点 {raw!r} 行号越界：{path} 只有 {total} 行，"
                f"却引用 {low}-{high}"
            )
    assert not problems, "文档里的路径/行号锚点有假：\n  " + "\n  ".join(problems)


def test_exemptions_are_specific_and_reasoned(manifest: Manifest) -> None:
    """前瞻文档的豁免必须逐条写明具体文件与理由，不许整目录/通配放行。"""

    for item in manifest.exemptions:
        assert item.reason.strip(), f"豁免 {item.file} 没有写理由"
        assert "*" not in item.file and not item.file.endswith("/"), (
            f"豁免 {item.file!r} 是通配或目录——豁免必须逐条到具体文件，不得整目录放行"
        )
        assert (ROOT / item.doc).is_file(), f"豁免声明的文档不存在：{item.doc}"


def test_exemptions_are_all_used(manifest: Manifest) -> None:
    """每条豁免必须真的挡住一个现存锚点——不许留永不命中的陈旧豁免。"""

    referenced = {(doc, path) for doc, _raw, path, _s, _e in _scan_doc_path_anchors()}
    unused = [
        f"{item.doc} → {item.file}"
        for item in manifest.exemptions
        if (item.doc, item.file) not in referenced
    ]
    assert not unused, (
        "这些豁免没有命中任何文档锚点，属陈旧登记，应删除或修正：\n  "
        + "\n  ".join(unused)
    )
