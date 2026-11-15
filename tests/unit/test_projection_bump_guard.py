"""Plan item 95's guard: a writer of projected data can't silently skip the
state-version bump.

Two static checks over the source (no database):
1. Every repository method that writes a projected model (or wraps one that
   does) is classified in tests/projection_inputs.py: PROJECTED_WRITERS, or
   NOT_PROJECTED_WRITES with a reason. A new write method fails here until
   someone decides which it is.
2. Every function in the app's source that calls a PROJECTED_WRITERS method
   also moves the bid's version (a BUMP_CALLS call in its body), or is in
   ALLOWED_WITHOUT_BUMP with a reason, or is a helper in
   ALLOWED_BECAUSE_CALLERS_BUMP whose named callers each call it and bump, and
   which nothing else calls (so a new caller of the helper fails here too).

What it does not check: that the bump is on the right branch of the right
function, or only when a projected value changed. The per-writer behaviour
tests (tests/integration/test_graph_bumps.py) check that.

The checks take parsed source, so the synthetic cases at the bottom run the
same code on small snippets and show each rule fires.
"""

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from projection_inputs import (
    ALLOWED_BECAUSE_CALLERS_BUMP,
    ALLOWED_WITHOUT_BUMP,
    BUMP_CALLS,
    NOT_PROJECTED_WRITES,
    PROJECTED_MODELS,
    PROJECTED_WRITERS,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "packages" / "tenderos_backend" / "src"
REPOSITORIES = BACKEND / "tenderos_backend" / "db" / "repositories"
UNIT_OF_WORK = BACKEND / "tenderos_backend" / "db" / "unit_of_work.py"
# The app's own source: the backend package, the api and the worker.
SCANNED_ROOTS = (BACKEND, REPO_ROOT / "apps" / "api" / "src", REPO_ROOT / "apps" / "worker" / "src")

_SESSION_WRITES = frozenset({"add", "add_all", "delete", "merge"})
_STATEMENT_WRITES = frozenset({"setattr", "update", "delete", "insert", "pg_insert"})


@dataclass(frozen=True)
class Source:
    path: str  # relative, forward slashes
    tree: ast.Module


def _parse(root: Path, files: Iterable[Path]) -> list[Source]:
    return [
        Source(f.relative_to(root).as_posix(), ast.parse(f.read_text(encoding="utf-8")))
        for f in files
    ]


# ----------------------------------------------------------------------
# Check 1: repository write methods on projected models
# ----------------------------------------------------------------------


def _writes_directly(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in _SESSION_WRITES
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "_session"
            ):
                return True
            if isinstance(func, ast.Name) and func.id in _STATEMENT_WRITES:
                return True
        if isinstance(node, ast.Assign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and not (
                    isinstance(target.value, ast.Name) and target.value.id == "self"
                ):
                    return True
    return False


def projected_repository_writers(sources: list[Source]) -> set[str]:
    """Class.method for every repository method that writes and names a
    projected model, plus every method that calls such a method on self (a
    wrapper like set_verified -> update_fields), to a fixed point."""
    methods: dict[str, ast.AST] = {}
    for source in sources:
        for cls in (n for n in source.tree.body if isinstance(n, ast.ClassDef)):
            for fn in cls.body:
                if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                    methods[f"{cls.name}.{fn.name}"] = fn
    writers = {
        name
        for name, fn in methods.items()
        if _writes_directly(fn)
        and {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)} & PROJECTED_MODELS
    }
    changed = True
    while changed:
        changed = False
        for name, fn in methods.items():
            if name in writers:
                continue
            cls = name.split(".")[0]
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "self"
                    and f"{cls}.{node.func.attr}" in writers
                ):
                    writers.add(name)
                    changed = True
                    break
    return writers


# ----------------------------------------------------------------------
# Check 2: callers of projected writers bump
# ----------------------------------------------------------------------


def unit_of_work_attributes(tree: ast.Module) -> dict[str, str]:
    """uow attribute name -> repository class, from UnitOfWork.__aenter__."""
    attributes = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Attribute)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id.endswith("Repository")
        ):
            attributes[node.targets[0].attr] = node.value.func.id
    return attributes


def _functions(source: Source) -> Iterable[tuple[str, ast.AST]]:
    for node in ast.walk(source.tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield f"{source.path}::{node.name}", node


def _calls_writer(fn: ast.AST, attributes: dict[str, str], writers: frozenset[str]) -> list[str]:
    found = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if isinstance(owner, ast.Attribute) and owner.attr in attributes:
                qualified = f"{attributes[owner.attr]}.{node.func.attr}"
                if qualified in writers:
                    found.append(f"{owner.attr}.{node.func.attr}")
    return found


def _called_names(fn: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def unbumped_writers(
    sources: list[Source],
    attributes: dict[str, str],
    writers: frozenset[str],
    allowed: dict[str, str],
    helpers: dict[str, frozenset[str]],
) -> list[str]:
    """Every problem found, as a sentence; empty when the rule holds."""
    problems: list[str] = []
    functions = dict(fn for source in sources for fn in _functions(source))
    for name, fn in functions.items():
        calls = _calls_writer(fn, attributes, writers)
        if not calls or _called_names(fn) & BUMP_CALLS or name in allowed or name in helpers:
            continue
        problems.append(
            f"{name} writes projected data ({', '.join(sorted(set(calls)))}) and moves no "
            "state version: bump it when a projected value changes (plan item 95), or "
            "classify it in tests/projection_inputs.py with a reason"
        )
    for helper, named_callers in helpers.items():
        helper_name = helper.split("::")[1]
        actual = {name for name, fn in functions.items() if helper_name in _called_names(fn)}
        for caller in sorted(actual - named_callers):
            problems.append(
                f"{caller} calls {helper_name}, which bumps nothing because its callers do; "
                "it is not one of the callers named in ALLOWED_BECAUSE_CALLERS_BUMP"
            )
        for caller in sorted(named_callers):
            fn = functions.get(caller)
            if fn is None or helper_name not in _called_names(fn):
                problems.append(
                    f"{caller} is named as a caller of {helper_name} but does not call it"
                )
            elif not _called_names(fn) & BUMP_CALLS:
                problems.append(
                    f"{caller} calls {helper_name}, which relies on its callers to bump, "
                    "and does not bump"
                )
    return problems


def _app_sources() -> list[Source]:
    sources = []
    for root in SCANNED_ROOTS:
        files = [
            f
            for f in root.rglob("*.py")
            if "repositories" not in f.parts and "migrations" not in f.parts
        ]
        sources.extend(_parse(root, files))
    return sources


# ----------------------------------------------------------------------
# On the real codebase
# ----------------------------------------------------------------------


def test_every_repository_write_on_a_projected_model_is_classified():
    found = projected_repository_writers(_parse(REPOSITORIES, REPOSITORIES.glob("*.py")))
    classified = PROJECTED_WRITERS | NOT_PROJECTED_WRITES.keys()
    assert not found - classified, (
        f"repository methods writing a projected model, not classified: {sorted(found - classified)}"
        ". Add each to PROJECTED_WRITERS (its callers must bump) or NOT_PROJECTED_WRITES "
        "(with why) in tests/projection_inputs.py"
    )
    assert not PROJECTED_WRITERS - found, (
        f"declared projected writers that no longer write: {sorted(PROJECTED_WRITERS - found)}"
    )


def test_every_caller_of_a_projected_writer_bumps_or_says_why_not():
    attributes = unit_of_work_attributes(ast.parse(UNIT_OF_WORK.read_text(encoding="utf-8")))
    sources = _app_sources()
    problems = unbumped_writers(
        sources, attributes, PROJECTED_WRITERS, ALLOWED_WITHOUT_BUMP, ALLOWED_BECAUSE_CALLERS_BUMP
    )
    assert not problems, "\n".join(problems)
    known = {name for source in sources for name, _ in _functions(source)}
    stale = (ALLOWED_WITHOUT_BUMP.keys() | ALLOWED_BECAUSE_CALLERS_BUMP.keys()) - known
    assert not stale, f"allowlisted functions that no longer exist: {sorted(stale)}"


# ----------------------------------------------------------------------
# The rules fire (synthetic source)
# ----------------------------------------------------------------------

_ATTRIBUTES = {"requirements": "RequirementRepository", "tenders": "TenderRepository"}
_WRITERS = frozenset({"RequirementRepository.update_fields"})


def _snippet(code: str) -> list[Source]:
    return [Source("svc.py", ast.parse(code))]


def test_a_writer_without_a_bump_is_reported_and_with_one_is_not():
    unbumped = "async def edit(uow, rid):\n    await uow.requirements.update_fields(rid)\n"
    bumped = unbumped + "    await uow.tenders.bump_state_version(rid)\n"
    assert unbumped_writers(_snippet(unbumped), _ATTRIBUTES, _WRITERS, {}, {})
    assert not unbumped_writers(_snippet(bumped), _ATTRIBUTES, _WRITERS, {}, {})
    assert not unbumped_writers(
        _snippet(unbumped), _ATTRIBUTES, _WRITERS, {"svc.py::edit": "a reason"}, {}
    )


_HELPER_CODE = (
    "async def edit_in_uow(uow, rid):\n"
    "    await uow.requirements.update_fields(rid)\n"
    "async def route(uow, rid):\n"
    "    await edit_in_uow(uow, rid)\n"
    "    await uow.tenders.bump_state_version(rid)\n"
)


def test_a_helper_whose_named_callers_bump_passes():
    helpers = {"svc.py::edit_in_uow": frozenset({"svc.py::route"})}
    assert not unbumped_writers(_snippet(_HELPER_CODE), _ATTRIBUTES, _WRITERS, {}, helpers)


def test_a_named_caller_that_does_not_bump_is_reported():
    code = _HELPER_CODE.replace("    await uow.tenders.bump_state_version(rid)\n", "")
    helpers = {"svc.py::edit_in_uow": frozenset({"svc.py::route"})}
    problems = unbumped_writers(_snippet(code), _ATTRIBUTES, _WRITERS, {}, helpers)
    assert any("svc.py::route" in p and "does not bump" in p for p in problems)


def test_a_new_caller_of_the_helper_is_reported():
    code = _HELPER_CODE + (
        "async def batch(uow, rid):\n"
        "    await edit_in_uow(uow, rid)\n"
        "    await uow.tenders.bump_state_version(rid)\n"
    )
    helpers = {"svc.py::edit_in_uow": frozenset({"svc.py::route"})}
    problems = unbumped_writers(_snippet(code), _ATTRIBUTES, _WRITERS, {}, helpers)
    assert any("svc.py::batch" in p and "not one of the callers" in p for p in problems)


def test_a_wrapper_of_a_projected_write_method_counts_as_a_writer():
    code = (
        "class RequirementRepository:\n"
        "    async def update_fields(self, rid, **fields):\n"
        "        requirement = await self._session.get(Requirement, rid)\n"
        "        setattr(requirement, 'x', 1)\n"
        "    async def set_verified(self, rid, v):\n"
        "        return await self.update_fields(rid, verified=v)\n"
        "    async def count(self):\n"
        "        return await self._session.scalar(select(Requirement))\n"
    )
    found = projected_repository_writers([Source("r.py", ast.parse(code))])
    assert found == {"RequirementRepository.update_fields", "RequirementRepository.set_verified"}
