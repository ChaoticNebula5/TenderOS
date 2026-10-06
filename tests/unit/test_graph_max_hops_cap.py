"""B17 Phase 0: core/config.py::Settings.graph_max_hops is bounded 1..6.

Build Guide D4 (:1940): "The code permits configuration up to 6 for
experiments, but the public application defaults to 4." Until this test,
the 6 existed only as a comment. graph_projection/queries.py::preview_impact
interpolates the value directly into a variable-length Cypher bound, so an
unchecked GRAPH_MAX_HOPS is the one input that sets traversal depth.

Tested through the real env-var path (monkeypatch.setenv + Settings()),
because that is how a deployment actually sets it - not only by passing
the field as a constructor argument.
"""

import pytest
from pydantic import ValidationError
from tenderos_backend.core.config import Settings


def test_default_is_d4s_public_default_of_four():
    # Read from the field declaration, not Settings(): .env also sets
    # GRAPH_MAX_HOPS=4, so a constructed instance would pass even if the
    # declared default changed.
    assert Settings.model_fields["graph_max_hops"].default == 4


@pytest.mark.parametrize("hops", ["1", "4", "6"])
def test_values_inside_d4s_range_are_accepted(monkeypatch, hops: str):
    monkeypatch.setenv("GRAPH_MAX_HOPS", hops)
    assert Settings().graph_max_hops == int(hops)


@pytest.mark.parametrize("hops", ["7", "50"])
def test_values_above_d4s_ceiling_of_six_are_rejected(monkeypatch, hops: str):
    monkeypatch.setenv("GRAPH_MAX_HOPS", hops)
    with pytest.raises(ValidationError, match="graph_max_hops"):
        Settings()


@pytest.mark.parametrize("hops", ["0", "-1"])
def test_values_below_one_are_rejected(monkeypatch, hops: str):
    # Not from D4 - a bound of 0 renders `[*1..0]`, which silently matches
    # nothing, so an impact preview would report "no impact" instead of
    # failing. See the comment on the field in core/config.py.
    monkeypatch.setenv("GRAPH_MAX_HOPS", hops)
    with pytest.raises(ValidationError, match="graph_max_hops"):
        Settings()
