                   ┌────────────────────────┐
                   │     Orchestrator       │
                   │   planning + routing   │
                   └───────────┬────────────┘
                               │
          ┌──────────┬─────────┼─────────┬──────────┐
          ▼          ▼         ▼         ▼          ▼
    file_manager   analyst  row_editor structure  chart_maker
          │          │         │         │          │
      Drive tools   reads    row tools  column/    chart tools
          │                             format
          │
          └─ resolve_spreadsheet_choice
                       │
                       ▼
               delegate wrapper
                       │
                       ▼
              OrchestratorState