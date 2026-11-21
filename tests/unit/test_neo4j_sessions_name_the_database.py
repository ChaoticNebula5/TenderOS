"""Plan item 100: every Neo4j session names its database.

The tests' own Neo4j has one database, tenderos-test, and tests/conftest.py
refuses a run whose NEO4J_DATABASE does not end in -test. That only protects
the stack's graph if no session falls back to a server's default database: a
session opened without `database=` goes to the default, which on the stack's
server is the real graph. So every `<driver>.session(...)` call and every
`<driver>.execute_query(...)` call in the code, the scripts and the tests must
name the database, and the shared helper must name the configured one.

Read from the source, not from a run, so a call no test executes is checked
too. Every `.session(...)` call in these directories is a Neo4j one today
(SQLAlchemy sessions come from factories here, never from `.session(...)`);
a new non-Neo4j one would be listed below and has to name itself.
"""

import ast
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOTS = ("apps", "packages", "scripts", "tests")
SKIPPED_FOLDERS = {"node_modules", ".venv", "__pycache__", "dist", "build"}
DRIVER = "packages/tenderos_backend/src/tenderos_backend/graph_projection/driver.py"
# The keyword each call takes its database by (the driver's own names).
DATABASE_KEYWORD = {"session": "database", "execute_query": "database_"}


def _python_files() -> list[Path]:
    paths = []
    for root in ROOTS:
        for folder, subfolders, names in os.walk(REPO / root):
            subfolders[:] = [d for d in subfolders if d not in SKIPPED_FOLDERS]
            paths += [Path(folder) / name for name in names if name.endswith(".py")]
    return sorted(paths)


def _calls() -> list[tuple[str, int, str, ast.Call]]:
    found = []
    for path in _python_files():
        rel = path.relative_to(REPO).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in DATABASE_KEYWORD
            ):
                found.append((rel, node.lineno, node.func.attr, node))
    return found


def _database(call: ast.Call, method: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == DATABASE_KEYWORD[method]:
            return keyword.value
    return None


def test_every_neo4j_session_names_its_database():
    unnamed = []
    for rel, line, method, call in _calls():
        value = _database(call, method)
        if value is None or (isinstance(value, ast.Constant) and value.value is None):
            unnamed.append(f"{rel}:{line} .{method}(...) names no database")

    assert unnamed == [], "\n".join(unnamed)


def test_the_shared_helper_names_the_configured_database():
    # Not vacuous: the scan sees the one call every app path goes through
    # (graph_projection/driver.py's get_session), and it names the setting.
    helper = [
        ast.unparse(_database(call, method))
        for rel, _line, method, call in _calls()
        if rel == DRIVER and method == "session" and _database(call, method) is not None
    ]

    assert helper == ["settings.neo4j_database"]
