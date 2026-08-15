"""One turn, told as plain events.

This is the line between the agent and whatever is talking to the user. 
A caller gets dataclasses holding strings and dicts,
and can print them, send them as JSON, or draw them, without knowing what a
message or a checkpointer is.

The conversation itself is not carried by the caller. A Session holds the name
of a thread, and the agent keeps everything said under it.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from excel_agent.model import GAVE_UP, RECURSION_LIMIT, new_thread


@dataclass
class ToolCall:
    """A tool the model asked for, and what it asked with."""

    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class Text:
    """A piece of the model's answer as it is being written.

    Only sent when a Session is asked for it. The pieces spell out the same
    words the Answer holds at the end, so show one or the other: printing both
    says everything twice.
    """

    text: str


@dataclass
class Answer:
    """The finished answer, at the end of the turn."""

    text: str


def rendered_tool_results(artifact: dict) -> str | None:
    """Turn preserved subagent tool results into user-displayable text."""
    results = artifact.get("tool_results")

    if not results:
        return None

    displayed = []

    for result in results:
        if isinstance(result, str):
            displayed.append(result)
        else:
            displayed.append(str(result))

    return "\n\n".join(displayed)


class Session:
    """One conversation with the agent.

    Ask it a question and it gives back the events of that turn, in the order
    they happened: the tools the model reached for, and the answer it settled
    on. 
    """

    def __init__(self, agent, stream_text: bool = False, name: str = "orchestrator"):
        self.agent = agent
        self.stream_text = stream_text
        self.thread_id = new_thread()
        # What the agent answering is called in a trace. Only tracing reads it.
        self.name = name

    def reset(self) -> None:
        """Forget the conversation by starting another one.

        The old thread is left where it is and never read again.
        """
        self.thread_id = new_thread()

    def ask(self, question: str) -> Iterator[ToolCall | Text | Answer]:
        """Put one question, and give back what happened as it happens."""
        config = {
            "configurable": {"thread_id": self.thread_id},
            "recursion_limit": RECURSION_LIMIT,
            "run_name": self.name,
        }

        spoken = ""

        # If the final piece of work in this turn is an analyst call, keep the
        # analyst's exact tool output here. It can then be returned directly
        # instead of asking the orchestrator to reproduce it.
        analyst_result: str | None = None

        try:
            for mode, payload in self.agent.stream(
                {"messages": [{"role": "user", "content": question}]},
                config=config,
                stream_mode=["updates", "messages"],
            ):
                if mode == "messages":
                    token, _ = payload

                    if self.stream_text and token.content:
                        yield Text(str(token.content))

                    continue

                for update in payload.values():
                    if not isinstance(update, dict):
                        continue

                    for message in update.get("messages") or []:

                        # A new tool call means any previous analyst result was
                        # only an intermediate step in a larger workflow.
                        calls = getattr(message, "tool_calls", None) or []

                        if calls:
                            analyst_result = None

                            for call in calls:
                                yield ToolCall(
                                    call["name"],
                                    dict(call["args"]),
                                )

                        # The delegate tool's artifact is not shown to the
                        # orchestrator model, but it is available here.
                        if isinstance(message, ToolMessage):
                            artifact = getattr(message, "artifact", None)

                            if (
                                isinstance(artifact, dict)
                                and artifact.get("subagent") == "analyst"
                            ):
                                preserved = rendered_tool_results(artifact)

                                if preserved:
                                    analyst_result = preserved

                        if (
                            isinstance(message, AIMessage)
                            and message.content
                        ):
                            spoken = str(message.content)

        except GraphRecursionError:
            yield Answer(GAVE_UP)
            return

        # If the analyst was the last operational step, its preserved tool
        # result is the authoritative user-facing data. Do not make the
        # orchestrator's regenerated version replace it.
        if analyst_result is not None:
            yield Answer(analyst_result)
            return

        yield Answer(spoken)


def rendered(call: ToolCall) -> str:
    """A tool call written out the way a person reads it."""
    arguments = ", ".join(f"{name}={value!r}" for name, value in call.arguments.items())
    return f"{call.name}({arguments})"