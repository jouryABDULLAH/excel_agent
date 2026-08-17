"""Agent nodes: one specialist per module, plus the supervisor that routes to them."""

from excel_agent.agents.analyst import build_analyst
from excel_agent.agents.chart_maker import build_chart_maker
from excel_agent.agents.file_manager import build_file_manager
from excel_agent.agents.row_editor import build_row_editor
from excel_agent.agents.structure_editor import build_structure_editor
from excel_agent.agents.supervisor import ROUTES, Route, build_supervisor

__all__ = [
    "ROUTES",
    "Route",
    "build_analyst",
    "build_chart_maker",
    "build_file_manager",
    "build_row_editor",
    "build_structure_editor",
    "build_supervisor",
]
