"""Generate a Graphviz rendering of ``ElsStateMachine``.

Usage:
    uv run python scripts/generate_els_state_diagram.py [--output PATH]
                                                        [--layout LR|TB]
                                                        [--long-conditions]
                                                        [--show-stop-edges]

Requirements:
    - ``graphviz`` Python package (dev dependency; ``uv sync``)
    - ``dot`` binary from the system Graphviz package
      (e.g. ``sudo apt install graphviz`` on Debian/WSL,
      ``brew install graphviz`` on macOS)

The script reconstructs the same states and transitions used by
``rcp.dispatchers.els_state_machine.ElsStateMachine`` and feeds them into
``transitions.extensions.diagrams.HierarchicalGraphMachine`` so a diagram can
be rendered without a running Kivy ``MainApp``.

Readability choices (all tunable via CLI flags):
  * Default layout is top-to-bottom (``rankdir=TB``); the state machine is
    deeper than it is wide once transitions are drawn, so TB is more compact
    than the graphviz default of LR.
  * Condition / guard names are shortened for display by default (e.g.
    ``is_not_retract_enabled`` → ``!retract``). The underlying callback names
    are not resolved (the machine never triggers), so this is purely a label
    transformation.
  * The global ``stop`` trigger (``'*' → idle``) fans out 9 near-identical
    edges that clutter the layout. By default they are omitted from the
    diagram and replaced with a footer note. Pass ``--show-stop-edges`` to
    render them.
"""
from __future__ import annotations

import argparse
import copy
from pathlib import Path

from transitions.extensions.diagrams import HierarchicalGraphMachine
from transitions.extensions.nesting import NestedState

# Switch the nested-state separator from the default ``_`` to ``.`` so that
# flat state names containing underscores (``set_start_z``, ``engage_half_nut``,
# ``depth_reached``, …) are not auto-split into unintended cluster hierarchies.
# Must be set BEFORE importing ElsStateMachine so the class's ``states`` list
# is parsed under the new separator.
NestedState.separator = "."

from rcp.dispatchers.els_state_machine import ElsStateMachine  # noqa: E402


# The only genuinely nested state in ElsStateMachine is ``returning`` with
# four children. In production, transitions reference the children as
# ``returning_waiting`` etc. (underscore separator). For the diagram we
# switched to ``.`` so flat state names are not mangled, so we rewrite the
# legitimately-nested references to dotted form.
_NESTED_RENAME = {
    "returning_waiting": "returning.waiting",
    "returning_retracting": "returning.retracting",
    "returning_preloading": "returning.preloading",
    "returning_adjusting": "returning.adjusting",
}

# Short display forms for guard/condition callback names. Keeps the rendered
# edge labels compact. Transitions resolves condition strings lazily at
# trigger time, so relabeling here is safe — the machine is never triggered.
_COND_SHORT = {
    "is_retract_enabled": "retract",
    "is_not_retract_enabled": "!retract",
    "is_wizard_enabled": "wizard",
    "is_not_wizard_enabled": "!wizard",
    "is_retract_or_wizard_enabled": "retract|wizard",
    "_is_stop_position_set": "stop_set",
    "_is_valid_stop_position": "stop_valid",
    "_is_at_final_depth": "final_depth",
    "_is_cross_slide_retracted": "x_retracted",
    "_check_valid_start_position": "start_valid",
    "_check_spindle_turning_forward": "spindle_fwd",
    "_check_spindle_speed_for_pitch": "speed_ok",
}


class _StubModel:
    """Minimal model used purely to host the graph machine for diagramming.

    ``ElsStateMachine.__init__`` reaches into ``MainApp.get_running_app()`` and
    the live ``servo`` device, neither of which exist when running this script
    outside the Kivy app. Instead of instantiating it, we read its class-level
    ``states`` attribute and call its ``_build_transitions`` method as an
    unbound method — it returns a literal list and does not touch ``self``.
    String-named callbacks in the transitions (guards, before-hooks, etc.)
    are resolved lazily by ``transitions`` at trigger time, so the stub does
    not need to implement them for the diagram to render.
    """


def _transform_transitions(
    transitions_list: list[dict],
    *,
    shorten_conditions: bool,
    drop_stop_edges: bool,
) -> list[dict]:
    """Apply the readability transformations to the transitions list."""
    out: list[dict] = []
    for t in copy.deepcopy(transitions_list):
        if drop_stop_edges and t.get("trigger") == "stop" and t.get("source") == "*":
            continue

        # Nested-state reference rewrite (always applied).
        for key in ("source", "dest"):
            val = t.get(key)
            if isinstance(val, str):
                t[key] = _NESTED_RENAME.get(val, val)
            elif isinstance(val, list):
                t[key] = [_NESTED_RENAME.get(v, v) for v in val]

        # Condition / guard label shortening.
        if shorten_conditions:
            for key in ("conditions", "unless"):
                val = t.get(key)
                if isinstance(val, str):
                    t[key] = _COND_SHORT.get(val, val)
                elif isinstance(val, list):
                    t[key] = [_COND_SHORT.get(v, v) for v in val]

        out.append(t)
    return out


def build_machine(
    *,
    layout: str,
    shorten_conditions: bool,
    drop_stop_edges: bool,
) -> _StubModel:
    stub = _StubModel()
    machine = HierarchicalGraphMachine(
        model=stub,
        states=ElsStateMachine.states,
        transitions=_transform_transitions(
            ElsStateMachine._build_transitions(stub),
            shorten_conditions=shorten_conditions,
            drop_stop_edges=drop_stop_edges,
        ),
        initial="idle",
        ignore_invalid_triggers=True,
        graph_engine="graphviz",
        title="ElsStateMachine"
        + (" — stop trigger: any state → idle" if drop_stop_edges else ""),
        show_conditions=True,
        show_state_attributes=False,
    )
    # Override the default LR layout with configurable rankdir and some
    # tighter spacing so the graph packs better.
    machine.machine_attributes = dict(
        machine.machine_attributes,
        rankdir=layout,
        ranksep="0.45",
        nodesep="0.35",
        splines="spline",
    )
    return stub


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/diagrams/els_state_machine"),
        help=(
            "Output path WITHOUT extension. Both .png and .svg will be written. "
            "Default: docs/diagrams/els_state_machine"
        ),
    )
    parser.add_argument(
        "--layout",
        choices=["TB", "LR"],
        default="TB",
        help="Graph layout direction (default: TB top-to-bottom).",
    )
    parser.add_argument(
        "--long-conditions",
        action="store_true",
        help="Keep full condition callback names instead of the short display forms.",
    )
    parser.add_argument(
        "--show-stop-edges",
        action="store_true",
        help="Render the global 'stop' trigger edges from every state to idle.",
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    stub = build_machine(
        layout=args.layout,
        shorten_conditions=not args.long_conditions,
        drop_stop_edges=not args.show_stop_edges,
    )
    graph = stub.get_graph()

    png_path = args.output.with_suffix(".png")
    svg_path = args.output.with_suffix(".svg")
    graph.draw(str(png_path), prog="dot", format="png")
    graph.draw(str(svg_path), prog="dot", format="svg")
    print(f"Wrote {png_path}")
    print(f"Wrote {svg_path}")


if __name__ == "__main__":
    main()
