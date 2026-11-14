"""Plan item 73: no gate can pause a run without a screen.

Before, two of the seven gate types (finding_resolution and cost_budget)
could pause a run with nowhere in the web app to decide them: observed in
the item 66 walk, 118 dev runs sat at the finding gate. This test ties
three lists together, so adding a gate on one side without the other fails
CI's test job:

1. the gate types the graph's nodes actually open: every literal passed as
   build_gate_payload's gate_type, found by reading the node modules'
   syntax trees, not listed by hand;
2. orchestration.gates.GATE_TYPES, the canonical list (and the API's
   response classes keyed by it);
3. the web app's gate screen registry, apps/web/src/gates/gateScreens.ts,
   whose keys are read between its two marker lines.

That a registry entry really renders its panel is the web side's own test
(gates/gateScreens.test.tsx); that the registry covers every gate type in
the web's GateResponse union is enforced by `tsc -b`.
"""

import ast
import re
from pathlib import Path

from tenderos_api.routes.gates import _GATE_TYPE_TO_RESPONSE_CLASS
from tenderos_backend.orchestration import nodes as nodes_package
from tenderos_backend.orchestration.gates import GATE_TYPES

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REGISTRY = REPO_ROOT / "apps" / "web" / "src" / "gates" / "gateScreens.ts"
BEGIN = "// gate-screens:begin"
END = "// gate-screens:end"


def _gate_types_opened_by_nodes() -> dict[str, list[str]]:
    """{gate_type: [node module file names]} for every build_gate_payload
    call in orchestration/nodes. Fails on a call whose gate_type is not a
    string literal: a computed one could name any gate, and this check
    would silently miss it."""
    found: dict[str, list[str]] = {}
    for path in sorted(Path(nodes_package.__file__).parent.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "build_gate_payload"
            ):
                continue
            # build_gate_payload(gate_id, gate_type, workflow_run_id, ...)
            arg = node.args[1] if len(node.args) > 1 else None
            for keyword in node.keywords:
                if keyword.arg == "gate_type":
                    arg = keyword.value
            assert isinstance(arg, ast.Constant) and isinstance(arg.value, str), (
                f"{path.name}:{node.lineno}: build_gate_payload's gate_type is not a "
                "string literal, so this test cannot tell which gate it opens"
            )
            found.setdefault(arg.value, []).append(path.name)
    return found


def _registry_keys() -> list[str]:
    text = REGISTRY.read_text(encoding="utf-8")
    assert text.count(BEGIN) == 1 and text.count(END) == 1, (
        f"{REGISTRY.name} must hold exactly one '{BEGIN}' and one '{END}' line"
    )
    block = text.split(BEGIN, 1)[1].split(END, 1)[0]
    return re.findall(r"^  ([a-z_]+): \{", block, flags=re.MULTILINE)


def test_every_gate_type_the_nodes_open_is_a_canonical_gate_type():
    opened = _gate_types_opened_by_nodes()

    assert set(opened) == set(GATE_TYPES), (
        f"opened by nodes but not in GATE_TYPES: {sorted(set(opened) - set(GATE_TYPES))}; "
        f"in GATE_TYPES but opened by no node: {sorted(set(GATE_TYPES) - set(opened))}"
    )


def test_the_api_has_a_response_for_every_gate_type():
    assert set(_GATE_TYPE_TO_RESPONSE_CLASS) == set(GATE_TYPES)


def test_the_web_registry_has_a_screen_for_every_gate_type_and_no_other():
    keys = _registry_keys()

    assert len(keys) == len(set(keys)), f"a gate type is listed twice: {keys}"
    assert set(keys) == set(GATE_TYPES), (
        f"gate types with no screen in {REGISTRY.name}: {sorted(set(GATE_TYPES) - set(keys))}; "
        f"screens for no gate type: {sorted(set(keys) - set(GATE_TYPES))}"
    )
