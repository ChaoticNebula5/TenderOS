"""A column's Python-side default and its server default agree.

Plan item 40 declared the migrations' server defaults on the models while
keeping each column's Python-side default, so the ORM still sends the
value on every insert instead of reading the server's back afterwards.
That states one default twice in one model. The schema diff test compares
the models' server defaults with the migrations', never with the Python
value beside them, so a change to only one of the two would pass it. This
test closes that: for every column with both a scalar Python default
(str, bool or int, not a callable) and a server default, the two must
mean the same value. No database.
"""

from sqlalchemy import Column, False_, TextClause, True_
from tenderos_backend.db.models import Base

# Columns whose server default this test cannot reduce to a plain value,
# each with the reason. Empty today: every scalar pair compares cleanly.
_NOT_COMPARABLE: dict[str, str] = {}

_UNREADABLE = object()


def _sql_literal(sql: str, python_type: type) -> object:
    """The value a plain SQL literal stands for, read as python_type."""
    if len(sql) >= 2 and sql[0] == sql[-1] == "'":
        return sql[1:-1].replace("''", "'") if python_type is str else _UNREADABLE
    if python_type is bool:
        return {"true": True, "false": False}.get(sql.lower(), _UNREADABLE)
    if python_type is int:
        try:
            return int(sql)
        except ValueError:
            return _UNREADABLE
    return _UNREADABLE


def _server_value(column: Column, python_type: type) -> object:
    arg = column.server_default.arg  # type: ignore[union-attr]
    if isinstance(arg, str):
        # A plain string server_default is quoted by SQLAlchemy as a
        # literal of the column's type: "0" on an Integer is the number 0.
        if python_type is str:
            return arg
        return _sql_literal(arg, python_type)
    if isinstance(arg, True_):
        return True
    if isinstance(arg, False_):
        return False
    if isinstance(arg, TextClause):
        return _sql_literal(arg.text, python_type)
    return _UNREADABLE


def _scalar_pairs() -> list[tuple[str, object, Column]]:
    pairs = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            default = column.default
            if default is None or column.server_default is None:
                continue
            if not default.is_scalar or not isinstance(default.arg, (str, bool, int)):
                continue
            pairs.append((f"{table.name}.{column.name}", default.arg, column))
    return pairs


def test_every_scalar_python_default_matches_its_server_default():
    pairs = _scalar_pairs()
    assert pairs, "found no column with both defaults; the scan itself is broken"

    mismatches = []
    for key, python_value, column in pairs:
        if key in _NOT_COMPARABLE:
            continue
        server_value = _server_value(column, type(python_value))
        if server_value is _UNREADABLE:
            mismatches.append(
                f"{key}: server default {column.server_default.arg!r} "  # type: ignore[union-attr]
                f"cannot be read as a {type(python_value).__name__}; compare it by hand "
                f"and list it in _NOT_COMPARABLE with the reason"
            )
        elif type(server_value) is not type(python_value) or server_value != python_value:
            mismatches.append(
                f"{key}: Python default {python_value!r}, server default {server_value!r}"
            )

    assert not mismatches, "Python and server defaults disagree:\n" + "\n".join(mismatches)


def test_every_listed_exception_still_names_a_compared_column():
    keys = {key for key, _, _ in _scalar_pairs()}
    assert set(_NOT_COMPARABLE) <= keys, sorted(set(_NOT_COMPARABLE) - keys)
