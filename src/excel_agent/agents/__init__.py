"""The specialists, and the tools each one holds."""

from excel_agent.agents import (
    analyst,
    chart_maker,
    file_manager,
    row_editor,
    structure_editor,
)

SPECIALISTS = (
    file_manager,
    analyst,
    row_editor,
    structure_editor,
    chart_maker,
)
