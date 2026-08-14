"""Factory for building the agents.

A subagent is wrapped as a tool the orchestrator can call with an instruction
in plain English. That keeps the orchestrator an ordinary agent, so the same
Session, the same events and the same command line drive either variant and
the two can be compared without being two programs.
"""

from langchain.agents import create_agent
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from excel_agent.agent import RECURSION_LIMIT, build_agent, build_model
from excel_agent.subagents.prompts import ORCHESTRATOR_PROMPT
from excel_agent.subagents.registry import SUBAGENTS, SubagentSpec
from excel_agent.config import BACKEND
from excel_agent.tools import select_tools

VARIANTS = ("single", "multi")

# What the agent at the top of each variant is called in a trace. Handed to a
# Session, so a turn is recorded against the name of whoever answered it.
ROOT_NAME = {"single": "agent", "multi": "orchestrator"}


def agent_name(variant: str) -> str:
    """What to call the agent at the top of one variant, for traces."""
    return ROOT_NAME.get(variant, "agent")


def as_tool(spec: SubagentSpec, model):
    """Wrap a subagent so the orchestrator can hand it work.

    The subagent keeps no conversation of its own: each instruction is answered
    on its own, and what was said before lives in the orchestrator's thread.
    Four more threads per turn would double the cost of a comparison that is
    about cost.
    """
    agent = create_agent(model, list(spec.tools), system_prompt=spec.system_prompt)

    @tool(spec.name, description=spec.description)
    def delegate(instruction: str) -> str:
        """Hand one piece of work to this subagent and return what it says."""
        finished = agent.invoke(
            {"messages": [{"role": "user", "content": instruction}]},
            # Named for the subagent, so a trace says who was handed the work
            # rather than showing a second "LangGraph" under the first.
            config={"recursion_limit": RECURSION_LIMIT, "run_name": spec.name},
        )
        messages = finished["messages"]
        used = [
            call["name"]
            for message in messages
            for call in getattr(message, "tool_calls", None) or []
        ]

        # What the tools returned goes up with the answer. A subagent writes as
        # though its reader watched it work, and the reader did not: given a
        # summary alone, an orchestrator asked for the data has nothing to
        # answer from, and fills the gap by inventing it.
        evidence = [
            str(message.content)
            for message in messages
            if isinstance(message, ToolMessage)
        ]

        answer = str(messages[-1].content)
        # an attempt to control hallucination
        if evidence:
            answer += "\n\nWhat the tools returned:\n" + "\n\n".join(evidence)
        if not used:
            answer += "\n\n(No tool was used. Nothing here was read from the file.)"
        return answer

    return delegate


def build_orchestrator():
    """Build the orchestrator and the subagents it hands work to.

    One model serves all of them, so the orchestrator and every subagent run
    on the same settings as the single agent they are being compared against.
    """
    model = build_model()
    delegates = [as_tool(spec, model) for spec in SUBAGENTS]

    # Picked out of the backend in use rather than imported by name. Both
    # backends have a tool called list_workbooks, and importing one of them
    # here gave the orchestrator the local one (/Data) while everything under it
    # talked to Drive.
    tools = {tool.name: tool for tool in select_tools(BACKEND)}

    # Choosing which file to work on is the orchestrator's own question, and
    # neither of these gives back a row number, so neither of them tempts it
    # into work that belongs to a subagent.
    choosing = [tools["list_workbooks"]]
    for name in ("find_spreadsheet", "use_spreadsheet"):
        if name in tools:
            choosing.append(tools[name])

    return create_agent(
        model,
        [*choosing, *delegates],
        system_prompt=ORCHESTRATOR_PROMPT,
        checkpointer=InMemorySaver(),
    )


def build(variant: str = "single"):
    """Build one of the two ways of working, by name.

    Both come back as something a Session can be handed, which is what lets
    the command line and the measurements stay the same either way.
    """
    if variant == "single":
        return build_agent()
    if variant == "multi":
        return build_orchestrator()

    raise ValueError(f'Unknown variant "{variant}". Use one of: {", ".join(VARIANTS)}.')
