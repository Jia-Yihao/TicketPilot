"""守卫的反向验证：故意破坏一次，确认守卫真的会拦。

用法（用仓库的虚拟环境跑，pytest 必须可用）::

    python scripts/guard_negative_check.py

守卫本身是「会红」的断言，但没跑过反例的守卫只是**看起来**会红。
这个脚本给两个守卫各造几处破坏，每次都断言：

* 破坏之后，对应的守卫用例**失败**（而且失败原因就是那条规则）；
* 还原之后，守卫重新**全绿**。

脚本不碰仓库里的任何文件：它先把要用的子树复制到临时目录，
在副本上制造破坏，跑副本里的守卫用例。真实仓库始终是只读的。

破坏项：

==========================================  ==========================================
破坏                                           应当失败的用例
==========================================  ==========================================
新增未声明的层目录                              test_every_layer_directory_is_declared
低层反向依赖高层（core -> services）              test_dependencies_only_point_strictly_down
明确禁止的依赖（api -> tools）                   test_forbidden_edges_do_not_exist
模块级 import 环                               test_no_module_level_import_cycles
登记腐化（代码里那条边没了，登记还在）             test_registered_exceptions_are_still_used
文档数字漂移（README 的路径数改错）                test_documented_numbers_match_the_code
==========================================  ==========================================

退出码 0 表示每一处破坏都被抓到、还原后全部恢复绿色。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARCH_GUARD = "tests/test_architecture_guard.py"
DOC_GUARD = "tests/test_docs_consistency.py"

# 副本要带的东西：守卫自己 + 它们现算事实时要读的子树。
COPY_TREES = ("app", "tests", "docs", "evals", "alembic", "scripts")
COPY_FILES = ("pyproject.toml", "README.md", "CONTRIBUTING.md", ".env.example")

PROBE_LAYER = "guard_probe_layer"
OBSERVABILITY_IMPORT = "from app.observability.redaction import digest, redact\n"


@dataclass(frozen=True)
class Case:
    """一处破坏，以及它应当让哪个守卫用例失败。"""

    name: str
    guard: str
    expected: str
    break_code: Callable[[pathlib.Path], None]


def _safe(text: str) -> str:
    """控制台编码未必能显示 pytest 输出里的每个字符，替换掉而不是崩掉。"""

    encoding = sys.stdout.encoding or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding, errors="replace")


def _python() -> str:
    """守卫一律用仓库的虚拟环境跑，避免拿系统的 pytest 去测。"""

    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv.exists():
        return str(venv)
    return sys.executable


def _make_workspace(target: pathlib.Path) -> pathlib.Path:
    """把守卫需要的子树复制一份出来。"""

    target.mkdir(parents=True, exist_ok=True)
    for name in COPY_TREES:
        shutil.copytree(
            ROOT / name,
            target / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.db"),
        )
    shutil.copytree(
        ROOT / "web" / "src",
        target / "web" / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for name in COPY_FILES:
        shutil.copy2(ROOT / name, target / name)
    return target


def _run_guard(workspace: pathlib.Path, guard: str) -> tuple[int, list[str], str]:
    """在副本里跑守卫，返回（退出码, 失败的用例名, 输出）。"""

    completed = subprocess.run(
        [
            _python(),
            "-m",
            "pytest",
            guard,
            "-o",
            "addopts=",
            "-q",
            "--tb=line",
            "-p",
            "no:cacheprovider",
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = completed.stdout + completed.stderr
    failed = re.findall(r"^FAILED\s+\S+::(\S+)", output, flags=re.MULTILINE)
    return completed.returncode, failed, output


def _break_undeclared_layer(workspace: pathlib.Path) -> None:
    """加一层没在 `tiers` 里声明的目录。"""

    directory = workspace / "app" / PROBE_LAYER
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "__init__.py").write_text("", encoding="utf-8")


def _break_upward_dependency(workspace: pathlib.Path) -> None:
    """低层（core）反向依赖高层（services）。"""

    path = workspace / "app" / "core" / "_guard_probe_up.py"
    path.write_text("from app.services import catalog  # noqa: F401\n", encoding="utf-8")


def _break_forbidden_edge(workspace: pathlib.Path) -> None:
    """出现 `forbidden` 里明确禁止的边（api -> tools）。"""

    path = workspace / "app" / "api" / "_guard_probe_forbidden.py"
    path.write_text("from app.tools import registry  # noqa: F401\n", encoding="utf-8")


def _break_import_cycle(workspace: pathlib.Path) -> None:
    """模块级 import 环（导入时就会炸）。"""

    left = workspace / "app" / "core" / "_guard_probe_x.py"
    right = workspace / "app" / "core" / "_guard_probe_y.py"
    left.write_text("from app.core import _guard_probe_y  # noqa: F401\n", encoding="utf-8")
    right.write_text("from app.core import _guard_probe_x  # noqa: F401\n", encoding="utf-8")


def _break_stale_exception(workspace: pathlib.Path) -> None:
    """让一条已登记的例外失去依据：把代码里那条边删掉，登记还留着。"""

    module = workspace / "app" / "core" / "guard.py"
    original = module.read_text(encoding="utf-8")
    if OBSERVABILITY_IMPORT not in original:
        raise SystemExit(
            "app/core/guard.py 里那条 observability 的 import 没找到，"
            "反向验证脚本需要跟着改（代码改了，反例也要改）"
        )
    module.write_text(
        original.replace(OBSERVABILITY_IMPORT, "", 1),
        encoding="utf-8",
        newline="",
    )


def _break_documented_number(workspace: pathlib.Path) -> None:
    """把 README 里的路径数改错一个。"""

    readme = workspace / "README.md"
    original = readme.read_text(encoding="utf-8")
    match = re.search(r"共 \d+ 个操作（(\d+) 条路径", original)
    if match is None:
        raise SystemExit("README 里那句 OpenAPI 规模没找到，反向验证脚本需要跟着改")
    wrong = str(int(match.group(1)) + 1)
    readme.write_text(
        original[: match.start(1)] + wrong + original[match.end(1) :],
        encoding="utf-8",
        newline="",
    )


CASES = (
    Case(
        name="新增未声明的层目录",
        guard=ARCH_GUARD,
        expected="test_every_layer_directory_is_declared",
        break_code=_break_undeclared_layer,
    ),
    Case(
        name="低层反向依赖高层（core -> services）",
        guard=ARCH_GUARD,
        expected="test_dependencies_only_point_strictly_down",
        break_code=_break_upward_dependency,
    ),
    Case(
        name="明确禁止的依赖（api -> tools）",
        guard=ARCH_GUARD,
        expected="test_forbidden_edges_do_not_exist",
        break_code=_break_forbidden_edge,
    ),
    Case(
        name="模块级 import 环",
        guard=ARCH_GUARD,
        expected="test_no_module_level_import_cycles",
        break_code=_break_import_cycle,
    ),
    Case(
        name="登记腐化（代码里那条边没了，登记还在）",
        guard=ARCH_GUARD,
        expected="test_registered_exceptions_are_still_used",
        break_code=_break_stale_exception,
    ),
    Case(
        name="文档数字漂移（README 的路径数改错）",
        guard=DOC_GUARD,
        expected="test_documented_numbers_match_the_code",
        break_code=_break_documented_number,
    ),
)


def _slug(name: str) -> str:
    return re.sub(r"[^0-9a-zA-Z]+", "-", name).strip("-").lower()[:40]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--keep",
        action="store_true",
        help="保留临时副本（排查用），默认跑完就删",
    )
    args = parser.parse_args(argv)

    if not (ROOT / ".venv" / "Scripts" / "python.exe").exists():
        print(
            "找不到 .venv，请用仓库的虚拟环境跑：.venv/Scripts/python.exe "
            "scripts/guard_negative_check.py",
            file=sys.stderr,
        )
        return 2

    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="guard-negative-") as directory:
        base = pathlib.Path(directory)

        print(f"基准目录：{ROOT}")
        print("一、干净副本上的基线（应当全绿）")
        workspace = _make_workspace(base / "baseline")
        code, failed, output = _run_guard(workspace, ARCH_GUARD)
        if code != 0 or failed:
            failures.append("基线：架构依赖守卫在干净副本上就没有全绿")
            print(f"  [FAIL] 失败用例：{failed}")
            print(_safe(output[-2000:]))
        else:
            print("  [ OK ] 架构依赖守卫全绿")
        code, failed, output = _run_guard(workspace, DOC_GUARD)
        if code != 0 or failed:
            failures.append("基线：文档一致性守卫在干净副本上就没有全绿")
            print(f"  [FAIL] 失败用例：{failed}")
            print(_safe(output[-2000:]))
        else:
            print("  [ OK ] 文档一致性守卫全绿")

        print("二、逐处破坏（每处都应当被抓到）")
        for index, case in enumerate(CASES):
            workspace = _make_workspace(base / f"case-{index}-{_slug(case.name)}")
            case.break_code(workspace)
            code, failed, output = _run_guard(workspace, case.guard)
            if code == 0:
                failures.append(f"{case.name}：守卫没有报错")
                print(f"  [FAIL] {case.name} —— 守卫没有报错（期望 {case.expected} 失败）")
            elif case.expected not in failed:
                failures.append(f"{case.name}：失败的用例不是 {case.expected}")
                print(f"  [FAIL] {case.name} —— 期望 {case.expected} 失败，实际失败：{failed}")
                print(_safe(output[-2000:]))
            else:
                print(f"  [ OK ] {case.name} —— {case.expected} 如期失败")

        if args.keep:
            print(f"三、临时副本保留在：{base}")
        else:
            print("三、临时副本已清理")

    if failures:
        print("\n=== 反向验证失败 ===")
        for item in failures:
            print(f" - {item}")
        return 1
    print("\n反向验证全部通过：每一处破坏都被抓到，守卫确实会拦。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
