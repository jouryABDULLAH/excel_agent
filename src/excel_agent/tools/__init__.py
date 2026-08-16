"""The tools the agent is allowed to use on Google spreadsheets."""

from excel_agent.tools.charts import (
    create_chart,
    delete_chart,
    update_chart,
)

from excel_agent.tools.columns import (
    delete_column,
    insert_column,
    move_column,
    rename_column,
    set_column_formula,
)
from excel_agent.tools.find import find_data
from excel_agent.tools.inspect import inspect_sheet

from excel_agent.tools.rows import (
    append_row,
    delete_row,
    insert_row,
    move_row,
    update_row,
)

from excel_agent.tools.spreadsheets import (
    find_spreadsheet,
    list_workbooks,
    use_spreadsheet,
)
from excel_agent.tools.stats import sheet_stats

from excel_agent.tools.style import (
    copy_format,
    format_range,
)
TOOLS = [
    list_workbooks,
    find_spreadsheet,
    use_spreadsheet,
    inspect_sheet,
    find_data,
    sheet_stats,
    
    append_row,
    delete_row,
    insert_row,
    move_row,
    update_row,

    insert_column,
    rename_column,
    delete_column,
    move_column,
    set_column_formula,

    create_chart,
    update_chart,
    delete_chart,

    format_range,
    copy_format,
]
