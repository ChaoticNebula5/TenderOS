"""Neo4j values made safe for a JSON response.

Plan item 75. The projector writes Python dates and datetimes as node
properties (a tender's submission_date, a certification's valid_until, a
project's start and end dates, a selection's selected_at). Neo4j hands them
back as its own types (neo4j.time.Date, DateTime, Time, Duration), which are
not subclasses of Python's, so Pydantic cannot serialize them: the graph
trace on a tender with a submission date answered 500 (observed in the item
66 walk).

Applied by the graph routes to what they return, not inside graph_service:
the reanalysis job reads graph_service.preview_impact's rows itself, and
nothing there should change type under it.
"""

from typing import Any

from neo4j.time import Date, DateTime, Duration, Time


def json_safe(value: Any) -> Any:
    """`value` with every Neo4j temporal value inside it, at any depth of
    dicts and lists, replaced:

    - Date, DateTime, Time: the Python date, datetime or time
      (`to_native()`), so the response formats them exactly as the rest of
      the API formats its own dates. Neo4j keeps nanoseconds and Python
      microseconds, so a DateTime loses its last three digits; the projector
      only ever writes Python values, which have none.
    - Duration: its ISO 8601 string (`P1Y2M3DT4S`). Python's timedelta has
      no months, so there is no exact native value to hand Pydantic.

    Anything else is returned as it is. A tuple comes back as a list, which
    is what JSON makes of it anyway.

    The temporal checks come before the tuple check: Duration is a tuple
    subclass (months, days, seconds, nanoseconds), and checked the other way
    round it came back as a list of four numbers (caught by its unit test).
    """
    if isinstance(value, (Date, DateTime, Time)):
        return value.to_native()
    if isinstance(value, Duration):
        return value.iso_format()
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value
