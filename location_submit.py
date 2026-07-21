from pathlib import Path
from typing import Any

import streamlit.components.v1 as components


_COMPONENT = components.declare_component(
    "location_submit",
    path=str(Path(__file__).parent / "location_submit_component"),
)


def location_submit_button(*, label: str, key: str) -> dict[str, Any] | None:
    """Render a submit button that returns browser coordinates when clicked."""
    return _COMPONENT(label=label, key=key, default=None)
