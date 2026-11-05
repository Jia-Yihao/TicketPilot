"""文档一致性守卫：文档与代码的对应关系必须成立。

TicketPilot 的叙事是「诚实标注」——每个对外承诺都要能在仓库里找到落点。
手抄的清单迟早会和代码分家，所以这里的判据只看**对应关系**，不保管数字：

* 每个 OpenAPI 操作在接口说明里都有自己那一行，例外必须逐条写明理由；
* `.env.example` 的每条赋值都能落到一个 `Settings` 字段，写错就说明配置静默失效；
* 解析口径本身也被钉住：注释行里的 `NAME=` 不算赋值，坏行必须显式失败。

配对与归一都是纯函数，可以单独测；真实文档与真实 schema 的比对在下面各自的用例里。
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import tempfile
from typing import Any

import pytest

from scripts.build_manual import source_digest

ROOT = pathlib.Path(__file__).resolve().parents[1]

HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


# --------------------------------------------------------------------------
# 从代码里现算的事实。文档只引用结论，数字不手抄。
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


ENV_EXAMPLE_FILE = ROOT / ".env.example"

# 环境变量对账的口径（把原先的手抄数字换成现算）：
# - 「有效赋值」= 去掉缩进后非空、不以 `#` 开头、且形如 `NAME=` 的行；
#   注释行里出现的 `NAME=`（如 `# RATE_LIMIT_STORE=redis`、`#   AGENT_MODEL=...`）
#   一律不计入——它们是示例，不是模板里的实际赋值。
# - 「归一」= 两侧名字都转大写后再比。`Settings` 的字段是小写蛇形，
#   `.env.example` 是大写变量名，pydantic-settings 按 `case_sensitive=False`
#   匹配；直接拿原始字符串比会得出「79 条全违规」的假阳性（上一轮踩过的坑），
#   所以归一规则本身也被下面的口径测试钉住。
# - 除空行与注释外，任何连 `NAME=` 形状都不是的行都会让解析直接失败，
#   不许静默漏读把对账读成好看的数。
_ENV_ASSIGNMENT_LINE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _parse_env_example(text: str) -> set[str]:
    """把 `.env.example` 文本解析成大写归一的赋值名集合（口径见上面注释）。"""

    assigned: set[str] = set()
    unparsed: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _ENV_ASSIGNMENT_LINE.match(stripped):
            assigned.add(stripped.split("=", 1)[0].upper())
        else:
            unparsed.append(line)
    assert not unparsed, (
        f".env.example 里有既非注释又非 `NAME=` 赋值的行，"
        f"对账口径必须先解释它才能继续：{unparsed!r}"
    )
    return assigned


def _env_example_assignments() -> set[str]:
    return _parse_env_example(ENV_EXAMPLE_FILE.read_text(encoding="utf-8"))


def _settings_field_names() -> set[str]:
    # 只取类定义的字段名表，不实例化 Settings，也不读任何 .env，保持纯函数。
    from app.core.config import Settings

    return {name.upper() for name in Settings.model_fields}


def _env_reverse_violations() -> set[str]:
    """`.env.example` 里赋了值却没有对应 `Settings` 字段的名单（必须为空）。"""

    return _env_example_assignments() - _settings_field_names()


# --------------------------------------------------------------------------
# 测试
# --------------------------------------------------------------------------


def test_source_digest_ignores_line_endings() -> None:
    """钉住「源文件摘要与行位无关」：同一内容分别写成 CRLF 与 LF，摘要必须逐字相等。"""

    text = "line one\nline two\n"
    normalized = text.encode("utf-8")
    with tempfile.TemporaryDirectory() as directory:
        crlf = pathlib.Path(directory) / "crlf.md"
        lf = pathlib.Path(directory) / "lf.md"
        crlf.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
        lf.write_bytes(normalized)
        expected = hashlib.sha256(normalized).hexdigest()
        assert source_digest(crlf) == source_digest(lf) == expected


ENDPOINT_DOC_FILE = ROOT / "docs" / "guide" / "接口说明.md"

# 逐条覆盖的显式例外字典：键是 OpenAPI 的 (method, path)，值必须写清原因。
# 键集与实测未覆盖集合的相等关系由下面的测试断言守着——补了文档就得删条目，
# 加了新端点不写文档就得进字典，否则守卫直接红。
OPENAPI_ENDPOINT_DOC_EXCEPTIONS: dict[tuple[str, str], str] = {
}

_DOC_METHOD_TOKEN = re.compile(
    r"\b(" + "|".join(verb.upper() for verb in sorted(HTTP_VERBS)) + r")\b"
)
_DOC_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9_{}./-]+")
_DOC_HEADING_LINE = re.compile(r"^#{2,4} ")
_DOC_METHOD_TOKEN_ALTS = "|".join(verb.upper() for verb in sorted(HTTP_VERBS))
# 正文行只认「同一个反引号 span 内相邻的 `VERB path`」，不再做整行交叉乘积，
# 否则会把文档并未逐条声明的 (方法, 路径) 对读成已覆盖。
_DOC_PROSE_LANDING = re.compile(
    r"`(?P<m>" + _DOC_METHOD_TOKEN_ALTS + r")[ \t]+(?P<p>/[A-Za-z0-9_{}./-]+)[ \t]*`"
)


def _normalize_doc_endpoint_path(token: str) -> str:
    """接口说明允许 `/memory/...` 这类省掉 Base URL 的速记写法。"""

    if token.startswith("/api/v1") or token.startswith("/health"):
        return token
    return f"/api/v1{token}"


def _doc_endpoint_landing_points(text: str) -> set[tuple[str, str]]:
    """从接口说明里抠出所有 (方法, 路径) 落点。

    标题行（`^#{2,4} `）保留合并写法：`GET / PUT / DELETE `path`` 交叉展开，
    单段尾段（如 `/cancel`）继承同行的方法并挂到同行完整路径的父路径下。
    正文行只认同一个反引号 span 内相邻的 `VERB path`（如 `POST /memory/events`），
    不做整行交叉乘积——交叉乘积会生成文档并未逐条声明的 (方法, 路径) 对，
    把真实缺口静默读成 0。
    两条路径都要求路径 token 以 `/` 开头，非 `/` 开头的 token（如 `app/...`、
    `1/2/4/8`）不是路径分支，直接丢弃。
    """

    landing: set[tuple[str, str]] = set()
    for line in text.splitlines():
        if _DOC_HEADING_LINE.match(line):
            methods = _DOC_METHOD_TOKEN.findall(line)
            if not methods:
                continue
            paths: set[str] = set()
            tails: set[str] = set()
            for token in _DOC_TOKEN_SPLIT.split(line):
                candidate = token.rstrip("/")
                if not candidate.startswith("/"):
                    continue
                if candidate.count("/") == 1 and len(candidate) > 1:
                    tails.add(candidate)
                elif candidate.count("/") >= 2:
                    paths.add(_normalize_doc_endpoint_path(candidate))
            for method in methods:
                for path in paths:
                    landing.add((method, path))
                for tail in tails:
                    for path in paths:
                        parent = path.rsplit("/", 1)[0]
                        landing.add((method, f"{parent}{tail}"))
        else:
            for match in _DOC_PROSE_LANDING.finditer(line):
                path = _normalize_doc_endpoint_path(match.group("p").rstrip("/"))
                landing.add((match.group("m"), path))
    return landing


def test_every_openapi_operation_has_a_landing_point_in_the_api_doc() -> None:
    """OpenAPI 每个 (method, path) 要么在接口说明有逐条落点，要么在例外字典里。"""

    schema = _openapi_schema()
    operations: set[tuple[str, str]] = set()
    for path, item in schema.get("paths", {}).items():
        for method in item:
            if method.lower() in HTTP_VERBS:
                operations.add((method.upper(), path))

    landing = _doc_endpoint_landing_points(
        ENDPOINT_DOC_FILE.read_text(encoding="utf-8")
    )
    uncovered = operations - landing

    stale = set(OPENAPI_ENDPOINT_DOC_EXCEPTIONS) - uncovered
    assert not stale, (
        f"例外字典里的 {sorted(stale)} 在接口说明里已能找到落点，"
        "例外条目必须同步删除（防例外字典腐烂）"
    )
    missing = uncovered - set(OPENAPI_ENDPOINT_DOC_EXCEPTIONS)
    assert not missing, (
        "这些 OpenAPI 端点在接口说明的逐条落点与例外字典里都找不到："
        f"{sorted(missing)}"
    )


def test_endpoint_landing_pairing_is_span_scoped_but_heading_expansion_stays() -> None:
    """正文行只在同一反引号 span 内配对；标题行的交叉展开与尾段继承保持不变。"""

    prose = _doc_endpoint_landing_points("`GET /a` 与 `POST /b`")
    assert ("GET", "/api/v1/a") in prose
    assert ("POST", "/api/v1/b") in prose
    # 交叉乘积会凭空造出文档没声明的组合，必须读不出落点
    assert ("POST", "/api/v1/a") not in prose
    assert ("GET", "/api/v1/b") not in prose

    heading = _doc_endpoint_landing_points("### GET / PUT / DELETE `/x/y`")
    assert ("GET", "/api/v1/x/y") in heading
    assert ("PUT", "/api/v1/x/y") in heading
    assert ("DELETE", "/api/v1/x/y") in heading

    tail = _doc_endpoint_landing_points(
        "### POST `/x/y/confirm` | `/cancel`"
    )
    assert ("POST", "/api/v1/x/y/confirm") in tail
    assert ("POST", "/api/v1/x/y/cancel") in tail


def test_every_env_example_assignment_maps_to_a_settings_field() -> None:
    """反向硬断言：`.env.example` 的每条有效赋值都必须命中一个 `Settings` 字段。

    方向是「赋值 → 字段」：模板里写了变量名而配置类没有这个字段，说明读数
    要么静默失效要么文档在骗人，必须红。正向（字段 → 赋值）当前有既有缺口，
    按分派单口径**不做成门禁**，只做 `env_fields_without_assignment_count`
    读数事实，见上面 `_fact_*` 的口径注释。
    """

    violations = _env_reverse_violations()
    assert not violations, (
        "这些 `.env.example` 赋值在 `Settings` 里没有对应字段（大写归一后）："
        f"{sorted(violations)}"
    )


def test_env_assignment_parsing_normalizes_case_and_skips_comments() -> None:
    """钉住对账口径：注释行里的 `NAME=` 不算赋值；名字大写归一；坏行必须报错。"""

    parsed = _parse_env_example("FOO=1\n# BAR=2\n  baz=3\n\n#   FOO=other\n")
    assert parsed == {"FOO", "BAZ"}, "注释行不得计入有效赋值，名字必须大写归一"
    # 静默漏读会把缺口读成好看的数，解析不了的非注释行必须显式失败。
    with pytest.raises(AssertionError):
        _parse_env_example("this line is neither a comment nor an assignment")
