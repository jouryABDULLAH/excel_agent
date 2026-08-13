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

from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError

from excel_agent.agent import GAVE_UP, RECURSION_LIMIT, new_thread
from excel_agent.tracing import asked, record


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


class Session:
    """One conversation with the agent.

    Ask it a question and it gives back the events of that turn, in the order
    they happened: the tools the model reached for, and the answer it settled
    on. 
    """

    def __init__(self, agent, stream_text: bool = False, name: str = "agent"):
        self.agent = agent
        self.stream_text = stream_text
        self.thread_id = new_thread()
        # What the agent answering is called in a trace. Only tracing reads
        # it, and factory.agent_name() is where the names come from.
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
        }

        # Everything this turn does is recorded against the name of the agent
        # answering it, and a subagent's work against that name and its own.
        # The question and the answer bracket the tool calls, so a file reads
        # as what was asked, what was done, and what came back.
        with asked(self.name):
            record({"event": "asked", "question": question})

            spoken = ""
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
                            for call in getattr(message, "tool_calls", None) or []:
                                yield ToolCall(call["name"], dict(call["args"]))
                            # Only what the model said. A tool's result is a
                            # message with content too, so taking any of them
                            # meant that a turn the model ended in silence came
                            # back carrying the last tool's output as though it
                            # were the answer.
                            if isinstance(message, AIMessage) and message.content:
                                spoken = str(message.content)
            except GraphRecursionError:
                record({"event": "gave_up", "text": GAVE_UP})
                yield Answer(GAVE_UP)
                return

            record({"event": "answered", "text": spoken})
            yield Answer(spoken)


def rendered(call: ToolCall) -> str:
    """A tool call written out the way a person reads it."""
    arguments = ", ".join(f"{name}={value!r}" for name, value in call.arguments.items())
    return f"{call.name}({arguments})"