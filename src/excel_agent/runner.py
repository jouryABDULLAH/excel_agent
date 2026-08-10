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

from langgraph.errors import GraphRecursionError

from excel_agent.agent import GAVE_UP, RECURSION_LIMIT, new_thread


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

    def __init__(self, agent, stream_text: bool = False):
        self.agent = agent
        self.stream_text = stream_text
        self.thread_id = new_thread()

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
                        if message.content:
                            spoken = str(message.content)
        except GraphRecursionError:
            yield Answer(GAVE_UP)
            return

        yield Answer(spoken)


def rendered(call: ToolCall) -> str:
    """A tool call written out the way a person reads it."""
    arguments = ", ".join(f"{name}={value!r}" for name, value in call.arguments.items())
    return f"{call.name}({arguments})"