"""文档一致性守卫：文档里写的数字，必须等于代码现算出来的数字。

TicketPilot 的叙事是「诚实标注」——每个数字都要能跑出来。
但只要数字是手抄进 Markdown 的，它迟早会和代码分家：
接口加了、迁移加了、表加了，文档里的「45 个操作 / 13 条迁移 / 24 张表」不会自己变。

这个守卫把那件事变成测试：

* `docs/fact-baseline.yaml` 只写「哪个文件的哪一行有个数字」和「用哪个正则把它抠出来」，
  **不写数字本身**——数字的唯一定义在代码里。
* 守卫现算代码里的事实（OpenAPI 规模、迁移链、表数、文件数、收集到的用例数……），
  再和文档里写的值逐条比。
* 对不上就失败，并指出是哪个文件的哪一行。

另外还守两件和数字相关的事：

* 说明书（docx）是 `docs/spec/项目规格.md` 的渲染结果，两者必须同步；
* 说明书本身要干净：没有 U+FFFD、没有私有区字符、没有退化成 `<w:sym>` 的符号。
"""

from __future__ import annotations

import ast
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST_FILE = ROOT / "docs" / "fact-baseline.yaml"
SPEC_FILE = ROOT / "docs" / "spec" / "项目规格.md"
MANUAL_FILE = ROOT / "docs" / "spec" / "TicketPilot项目说明书.docx"
RECORD_FILE = ROOT / "docs" / "spec" / "manual-build.json"

WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
PRIVATE_USE = re.compile(r"[\ue000-\uf8ff\U000f0000-\U0010ffff]")
HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


# --------------------------------------------------------------------------
# 事实的现算实现。数字只在这里出现一次。
# --------------------------------------------------------------------------


def _openapi_schema() -> dict[str, Any]:
    from app.core.config import Settings
    from app.main import create_app

    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        auto_create_tables=False,
    )
    application = create_app(settings)
    return application.openapi()


def _fact_openapi_paths() -> int:
    return len(_openapi_schema().get("paths", {}))


def _fact_openapi_operations() -> int:
    schema = _openapi_schema()
    operations = 0
    for methods in schema.get("paths", {}).values():
        operations += sum(1 for verb in methods if verb.lower() in HTTP_VERBS)
    return operations


def _alembic_revisions() -> tuple[list[str], set[str]]:
    revisions: list[str] = []
    parents: set[str] = set()
    for path in sorted((ROOT / "alembic" / "versions").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
                value = node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
                value = node.value
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id not in {"revision", "down_revision"}:
                    continue
                if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                    continue
                if target.id == "revision":
                    revisions.append(value.value)
                else:
                    parents.add(value.value)
    return revisions, parents


def _fact_alembic_revisions() -> int:
    revisions, _ = _alembic_revisions()
    return len(revisions)


def _fact_alembic_head() -> str:
    revisions, parents = _alembic_revisions()
    heads = [revision for revision in revisions if revision not in parents]
    assert len(heads) == 1, f"迁移链的 head 不唯一：{heads}"
    return heads[0]


def _fact_sql_tables() -> int:
    import app.models  # noqa: F401  (注册 SQLAlchemy metadata)
    from app.db.base import Base

    return len(Base.metadata.tables)


def _python_files(directory: pathlib.Path) -> int:
    return sum(1 for path in directory.rglob("*.py") if "__pycache__" not in path.parts)


def _fact_app_py_files() -> int:
    return _python_files(ROOT / "app")


def _fact_mypy_files() -> int:
    # `python -m mypy app` 检查的就是 app/ 下的这些文件。
    return _python_files(ROOT / "app")


def _fact_test_py_files() -> int:
    return _python_files(ROOT / "tests")


def _fact_test_collected() -> int:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise AssertionError(
            "pytest --collect-only 失败，无法现算收集到的用例数：\n"
            + completed.stdout[-2000:]
            + completed.stderr[-2000:]
        )
    match = re.search(r"(\d+) tests? collected", completed.stdout)
    assert match, f"没能从 pytest 输出里读出收集到的用例数：\n{completed.stdout[-2000:]}"
    return int(match.group(1))


def _fact_adr_count() -> int:
    return len(list((ROOT / "docs" / "adr").glob("ADR-*.md")))


def _dataset_cases(name: str) -> int:
    payload = json.loads((ROOT / "evals" / name).read_text(encoding="utf-8"))
    return len(payload["cases"])


def _fact_golden_cases() -> int:
    return _dataset_cases("golden_set.json")


def _fact_support_cases() -> int:
    return _dataset_cases("support_set.json")


def _fact_offline_cases() -> int:
    return _fact_golden_cases() + _fact_support_cases()


def _fact_console_pages() -> int:
    return len(list((ROOT / "web" / "src" / "pages").glob("*.tsx")))


COMPUTERS: dict[str, Callable[[], object]] = {
    "openapi_paths": _fact_openapi_paths,
    "openapi_operations": _fact_openapi_operations,
    "alembic_revisions": _fact_alembic_revisions,
    "alembic_head": _fact_alembic_head,
    "sql_tables": _fact_sql_tables,
    "app_py_files": _fact_app_py_files,
    "mypy_files": _fact_mypy_files,
    "test_py_files": _fact_test_py_files,
    "test_collected": _fact_test_collected,
    "adr_count": _fact_adr_count,
    "golden_cases": _fact_golden_cases,
    "support_cases": _fact_support_cases,
    "offline_cases": _fact_offline_cases,
    "console_pages": _fact_console_pages,
}


# --------------------------------------------------------------------------
# 清单
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    file: str
    anchor: str
    pattern: str
    group: int
    in_manual: bool


@dataclass(frozen=True)
class Fact:
    name: str
    label: str
    claims: tuple[Claim, ...]


def _load_manifest() -> dict[str, Fact]:
    raw = yaml.safe_load(MANIFEST_FILE.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), "事实清单必须是一份映射"
    facts: dict[str, Fact] = {}
    for name, body in raw["facts"].items():
        claims = tuple(
            Claim(
                file=str(item["file"]),
                anchor=str(item["anchor"]),
                pattern=str(item["pattern"]),
                group=int(item.get("group", 1)),
                in_manual=bool(item.get("in_manual", False)),
            )
            for item in body["claims"]
        )
        facts[str(name)] = Fact(
            name=str(name),
            label=str(body.get("label", name)),
            claims=claims,
        )
    return facts


# --------------------------------------------------------------------------
# 取出文档里的数字
# --------------------------------------------------------------------------


def _claim_line(claim: Claim) -> tuple[int, str]:
    path = ROOT / claim.file
    assert path.is_file(), f"事实清单指向的文件不存在：{claim.file}"
    lines = path.read_text(encoding="utf-8").splitlines()
    hits = [(index + 1, line) for index, line in enumerate(lines) if claim.anchor in line]
    assert len(hits) == 1, (
        f"{claim.file} 里的锚点 {claim.anchor!r} 命中 {len(hits)} 行，"
        "必须恰好命中一行（锚点要能唯一定位这句话）"
    )
    return hits[0]


def _claim_value(claim: Claim) -> str:
    number, line = _claim_line(claim)
    match = re.search(claim.pattern, line)
    assert match, (
        f"{claim.file}:{number} 上找不到 {claim.pattern!r} 对应的数字：{line.strip()!r}"
    )
    assert claim.group <= len(match.groups()), (
        f"{claim.file}:{number} 的正则只有 {len(match.groups())} 个捕获组，"
        f"但清单里写的是第 {claim.group} 组"
    )
    return match.group(claim.group)


def _strip_markdown(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = text.replace("**", "").replace("`", "")
    return text.strip()


def _rendered_pieces(line: str) -> list[str]:
    """一行 markdown 在说明书里应该出现的文本片段。"""

    text = line.strip()
    if text.startswith("|"):
        cells = [cell.strip() for cell in text.strip("|").split("|")]
        return [piece for piece in (_strip_markdown(cell) for cell in cells) if piece]
    text = re.sub(r"^>\s*", "", text)
    text = re.sub(r"^[-*+]\s+", "", text)
    text = re.sub(r"^\d+\.\s+", "", text)
    piece = _strip_markdown(text)
    return [piece] if piece else []


# --------------------------------------------------------------------------
# 说明书（docx）
# --------------------------------------------------------------------------


def _docx_paragraphs(path: pathlib.Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        document = archive.read("word/document.xml")
    root = ET.fromstring(document)
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{WORD_NS}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{WORD_NS}t"))
        paragraphs.append(text)
    return paragraphs


def _docx_xml_parts(path: pathlib.Path) -> dict[str, str]:
    parts: dict[str, str] = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.endswith(".xml"):
                parts[name] = archive.read(name).decode("utf-8")
    return parts


def _pandoc() -> str | None:
    return shutil.which("pandoc")


def _pandoc_version(pandoc: str) -> str:
    completed = subprocess.run(
        [pandoc, "--version"], capture_output=True, text=True, check=False
    )
    first = (completed.stdout or completed.stderr).splitlines()
    if not first:
        return "unknown"
    parts = first[0].split()
    return parts[1] if len(parts) > 1 else first[0].strip()


def _build_record() -> dict[str, Any]:
    assert RECORD_FILE.is_file(), (
        f"缺少 {RECORD_FILE.relative_to(ROOT)}——"
        "说明书构建记录不在库里。跑 python scripts/build_manual.py（或 make manual）生成。"
    )
    payload = json.loads(RECORD_FILE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), "说明书构建记录必须是一份 JSON 映射"
    return payload


# --------------------------------------------------------------------------
# 测试
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def manifest() -> dict[str, Fact]:
    return _load_manifest()


@pytest.fixture(scope="module")
def manual_paragraphs() -> list[str]:
    assert MANUAL_FILE.is_file(), f"说明书不存在：{MANUAL_FILE}"
    return _docx_paragraphs(MANUAL_FILE)


def test_manifest_only_points_at_documented_facts(manifest: dict[str, Fact]) -> None:
    """清单里的每个事实都要有现算实现，锚点必须唯一且不含数字。"""

    unknown = sorted(set(manifest) - set(COMPUTERS))
    assert not unknown, (
        f"事实清单里有没实现的现算函数：{unknown}；可用的是 {sorted(COMPUTERS)}"
    )
    unused = sorted(set(COMPUTERS) - set(manifest))
    assert not unused, f"这些事实现算了却没人核对：{unused}"

    for fact in manifest.values():
        assert fact.claims, f"事实 {fact.name} 没有登记任何说法"
        for claim in fact.claims:
            assert not any(char.isdigit() for char in claim.anchor), (
                f"{claim.file} 的锚点 {claim.anchor!r} 里带数字——"
                "锚点必须只定位位置，不能顺带把事实值复制一份"
            )
            _claim_line(claim)


def test_manifest_contains_no_literal_numbers(manifest: dict[str, Fact]) -> None:
    """清单里不许出现字面数字——它只能是「去哪里核对」的索引。"""

    offenders: list[str] = []
    for fact in manifest.values():
        for claim in fact.claims:
            pattern = claim.pattern.replace("[0-9a-f]", "").replace(r"\d", "")
            if re.search(r"\d", pattern):
                offenders.append(f"{fact.name} -> {claim.file}: {claim.pattern}")
    assert not offenders, (
        "事实清单的正则里带了字面数字，等于多了一份事实源：\n  " + "\n  ".join(offenders)
    )


def test_documented_numbers_match_the_code(manifest: dict[str, Fact]) -> None:
    """文档里写的数字，必须等于代码现算出来的数字。"""

    problems: list[str] = []
    for fact in manifest.values():
        actual = COMPUTERS[fact.name]()
        for claim in fact.claims:
            number, line = _claim_line(claim)
            try:
                documented = _claim_value(claim)
            except AssertionError as error:
                problems.append(f"{claim.file}:{number}  {fact.label}：{error}")
                continue
            if documented != str(actual):
                problems.append(
                    f"{claim.file}:{number}  {fact.label}：文档写 {documented}，"
                    f"代码现算是 {actual}\n    {line.strip()}"
                )
    assert not problems, "文档与代码对不上：\n  " + "\n  ".join(problems)


def test_every_fact_has_at_least_two_claims(manifest: dict[str, Fact]) -> None:
    """一个事实至少要在两个地方被写出来——只写一处的地方最容易忘更新。"""

    thin = [
        f"{fact.name}（只有 {len(fact.claims)} 处说法）"
        for fact in manifest.values()
        if len(fact.claims) < 2
    ]
    assert not thin, "这些事实只在文档里出现了一处：\n  " + "\n  ".join(thin)


def test_manual_contains_the_claimed_lines(
    manifest: dict[str, Fact], manual_paragraphs: list[str]
) -> None:
    """标了 in_manual 的说法，必须原样出现在说明书里。"""

    manual_text = "\n".join(manual_paragraphs)
    problems: list[str] = []
    for fact in manifest.values():
        for claim in fact.claims:
            if not claim.in_manual:
                continue
            number, line = _claim_line(claim)
            for piece in _rendered_pieces(line):
                if piece not in manual_text:
                    problems.append(
                        f"{claim.file}:{number}  这句话在说明书里找不到：{piece!r}"
                    )
    assert not problems, (
        "说明书里没有这些说法（说明书可能没跟着规格重生成）：\n  " + "\n  ".join(problems)
    )


def test_manual_build_record_matches_the_specification() -> None:
    """说明书必须是从当前的 `docs/spec/项目规格.md` 渲染出来的。

    这一条不依赖 pandoc：构建记录里存着源文件的 sha256，
    规格改了没重生成说明书，这里就会红。
    """

    record = _build_record()
    digest = hashlib.sha256(SPEC_FILE.read_bytes()).hexdigest()
    assert record.get("source_sha256") == digest, (
        "说明书落后于规格：docs/spec/项目规格.md 改过之后没有重生成说明书。\n"
        f"  记录里的源文件摘要：{record.get('source_sha256')}\n"
        f"  当前规格的摘要　：{digest}\n"
        "  修法：python scripts/build_manual.py（或 make manual）"
    )
    assert record.get("source") == SPEC_FILE.relative_to(ROOT).as_posix(), (
        f"构建记录指向的源文件是 {record.get('source')!r}，不是规格本身"
    )


def test_manual_is_in_sync_with_the_specification(manual_paragraphs: list[str]) -> None:
    """说明书必须是 `docs/spec/项目规格.md` 的渲染结果。

    只有本机 pandoc 版本和构建记录里的版本一致时才逐段比对——
    不同版本的 pandoc 渲染细节可能不同，那不是「说明书过期」，
    那种情况下上面的摘要检查仍然有效。
    """

    pandoc = _pandoc()
    if pandoc is None:
        pytest.skip("pandoc 不在 PATH 上，跳过「说明书与规格同步」检查")
    record = _build_record()
    local_version = _pandoc_version(pandoc)
    if local_version != record.get("pandoc"):
        pytest.skip(
            f"本机 pandoc {local_version} 与构建记录里的 {record.get('pandoc')} 不是同一个版本，"
            "逐段比对不成立；规格摘要检查仍然有效"
        )
    with tempfile.TemporaryDirectory() as directory:
        rebuilt = pathlib.Path(directory) / "manual.docx"
        completed = subprocess.run(
            [pandoc, str(SPEC_FILE), "-f", "gfm", "-t", "docx", "-o", str(rebuilt)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert completed.returncode == 0, (
            f"pandoc 渲染规格失败：\n{completed.stdout[-2000:]}\n{completed.stderr[-2000:]}"
        )
        fresh = _docx_paragraphs(rebuilt)

    committed_text = [text.strip() for text in manual_paragraphs if text.strip()]
    fresh_text = [text.strip() for text in fresh if text.strip()]
    for index, (old, new) in enumerate(zip(committed_text, fresh_text, strict=False)):
        if old != new:
            pytest.fail(
                "说明书与规格不同步：改完 docs/spec/项目规格.md 没有重生成说明书。\n"
                f"  第一个不一致的段落 #{index}：\n"
                f"    说明书：{old[:200]!r}\n"
                f"    现渲染：{new[:200]!r}\n"
                "  修法：python scripts/build_manual.py（或 make manual）"
            )
    assert len(committed_text) == len(fresh_text), (
        "说明书与规格不同步：段落数量不同"
        f"（说明书 {len(committed_text)} 段，现渲染 {len(fresh_text)} 段）。"
        " 修法：python scripts/build_manual.py（或 make manual）"
    )


def test_manual_is_text_clean(manual_paragraphs: list[str]) -> None:
    """说明书不能有 U+FFFD、私有区字符，也不能把符号退化成 `<w:sym>`。"""

    parts = _docx_xml_parts(MANUAL_FILE)
    fffd = sum(text.count("\ufffd") for text in parts.values())
    assert fffd == 0, f"说明书里有 {fffd} 个 U+FFFD 替换字符（编码往返坏了）"

    private = [text for text in manual_paragraphs if PRIVATE_USE.search(text)]
    assert not private, f"说明书里出现了私有区字符：{private[:3]}"

    document = parts.get("word/document.xml", "")
    assert "<w:sym" not in document, (
        "说明书里出现了 <w:sym>——符号被退化成 Word 符号域，"
        "复制出来会变成乱码（U+2610 复选框就是这么丢的）"
    )

    source = SPEC_FILE.read_text(encoding="utf-8")
    if "\u2610" in source:
        assert any("\u2610" in text for text in manual_paragraphs), (
            "规格里画了复选框（U+2610），说明书里却没有——复选框在渲染时丢了"
        )


def test_manual_file_is_a_readable_docx() -> None:
    """说明书必须在库里，且是一个能打开的 docx。"""

    assert MANUAL_FILE.is_file()
    with zipfile.ZipFile(MANUAL_FILE) as archive:
        names = archive.namelist()
    assert "word/document.xml" in names, "说明书不是一个完整的 docx"
    assert len(_docx_paragraphs(MANUAL_FILE)) > 100, "说明书正文段落太少，多半是渲染坏了"
