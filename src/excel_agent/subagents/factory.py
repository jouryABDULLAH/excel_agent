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
from excel_agent.tools.workbooks import list_workbooks
from excel_agent.tracing import caller, called_by, record

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
        record(
            {
                "event": "delegated",
                "by": caller(),
                "to": spec.name,
                "instruction": instruction,
            }
        )

        # Everything the subagent's tools do is recorded against its name
        # rather than the orchestrator's, so a trace says who did what.
        with called_by(spec.name):
            finished = agent.invoke(
                {"messages": [{"role": "user", "content": instruction}]},
                config={"recursion_limit": RECURSION_LIMIT},
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

    return create_agent(
        model,
        [list_workbooks, *delegates],
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
