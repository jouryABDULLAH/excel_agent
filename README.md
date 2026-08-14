# excel_agent

Part of the first Evaluation at T2 (Thursday, 6th of August).

An agentic system driven by natural language that translates text into actions (add, edit, remove) for Excel sheets.

*Maybe:* Powered by an orchestrator and subagents

## Tracing

Runs go to [LangSmith](https://smith.langchain.com/). LangChain sends them
itself, so there is nothing to switch on in the code — set these and run:

```powershell
$env:LANGSMITH_TRACING = "true"
$env:LANGSMITH_API_KEY = "your-key-here"
$env:LANGSMITH_PROJECT = "excel-agent"
```

A turn arrives as a tree: the question at the root, named for whichever agent
answered it, each model call under that with the prompt it saw and what it
cost, and each tool call with its arguments and what it returned. Under
`--agents multi` a subagent's whole conversation sits inside the tool call
that handed it the work.