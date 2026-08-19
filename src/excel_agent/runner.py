"""Expose one agent turn as simple application-level events.

The runner is the boundary between LangChain/LangGraph and clients such as
the CLI and Streamlit. Clients do not need to understand AIMessage,
ToolMessage, graph state, or checkpoints.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import (
    AIMessage,
    ToolMessage,
)
from langgraph.types import Command
from langgraph.errors import (
    GraphRecursionError,
)

from excel_agent.config import START_SPREADSHEET
from excel_agent.graph.state import DECISION_NAMES
from excel_agent.model import (
    CUT_OFF,
    GAVE_UP,
    RECURSION_LIMIT,
    new_thread,
)


@dataclass
class ToolCall:
    """A tool call that happened during the turn."""

    name: str
    arguments: dict = field(
        default_factory=dict
    )


@dataclass
class Text:
    """One streamed piece of model text."""

    text: str


@dataclass
class Artifact:
    """Structured data intended for deterministic rendering."""

    data: dict


@dataclass
class Answer:
    """The final user-facing natural-language answer."""

    text: str


@dataclass
class Approval:
    """A tool waiting to be allowed to run.

    Nothing emits one yet. It is part of the contract now so that a paused turn
    has a shape the front ends already understand, rather than runner, ui and
    cli all being reopened the day approval is switched on.
    """

    tool: str
    arguments: dict = field(
        default_factory=dict
    )
    id: str = ""


Event = (
    ToolCall
    | Text
    | Artifact
    | Answer
    | Approval
)


def _render_artifacts(
    artifact: object,
) -> list[dict]:
    """Return inner artifacts only when their subagent marked them for display."""
    if not isinstance(
        artifact,
        dict,
    ):
        return []

    if not artifact.get(
        "render_data"
    ):
        return []

    tool_artifacts = artifact.get(
        "tool_artifacts"
    )

    if not isinstance(
        tool_artifacts,
        list,
    ):
        return []

    return [
        item
        for item in tool_artifacts
        if isinstance(
            item,
            dict,
        )
    ]


def _merge_inspect_artifacts(
    artifacts: list[dict],
) -> list[dict]:
    """Merge continuous inspect_sheet pages from the same logical read."""
    result: list[dict] = []

    for artifact in artifacts:
        if (
            artifact.get("operation")
            != "inspect_sheet"
        ):
            result.append(
                artifact
            )
            continue

        current = {
            **artifact,
            "rows": list(
                artifact.get("rows")
                or []
            ),
        }

        if not result:
            result.append(
                current
            )
            continue

        previous = result[-1]

        if (
            previous.get("operation")
            != "inspect_sheet"
        ):
            result.append(
                current
            )
            continue

        previous_last = (
            previous.get(
                "last_returned_row"
            )
        )

        current_first = (
            artifact.get(
                "first_returned_row"
            )
        )

        continuous = (
            isinstance(
                previous_last,
                int,
            )
            and isinstance(
                current_first,
                int,
            )
            and current_first
            == previous_last + 1
        )

        same_read = (
            previous.get(
                "spreadsheet"
            )
            == artifact.get(
                "spreadsheet"
            )
            and previous.get(
                "sheet"
            )
            == artifact.get(
                "sheet"
            )
            and previous.get(
                "headers"
            )
            == artifact.get(
                "headers"
            )
            and continuous
        )

        if not same_read:
            result.append(
                current
            )
            continue

        previous_rows = (
            previous.setdefault(
                "rows",
                [],
            )
        )

        previous_rows.extend(
            artifact.get("rows")
            or []
        )

        previous[
            "last_returned_row"
        ] = artifact.get(
            "last_returned_row"
        )

        previous[
            "returned_rows"
        ] = len(
            previous_rows
        )

        previous[
            "has_more"
        ] = artifact.get(
            "has_more",
            False,
        )

        previous[
            "next_start_row"
        ] = artifact.get(
            "next_start_row"
        )

        previous[
            "last_data_row"
        ] = artifact.get(
            "last_data_row",
            previous.get(
                "last_data_row"
            ),
        )

        previous[
            "total_data_rows"
        ] = artifact.get(
            "total_data_rows",
            previous.get(
                "total_data_rows"
            ),
        )

        previous[
            "charts"
        ] = artifact.get(
            "charts",
            previous.get(
                "charts",
                [],
            ),
        )

    return result


class Session:
    """One persisted conversation with the orchestrator."""

    def __init__(
        self,
        agent,
        stream_text: bool = False,
        name: str = "orchestrator",
    ):
        self.agent = agent
        self.stream_text = (
            stream_text
        )
        self.thread_id = (
            new_thread()
        )
        self.name = name

        if START_SPREADSHEET:
            self.use(
                START_SPREADSHEET
            )

    @property
    def _where(self) -> dict:
        """Which thread this session's state lives under."""
        return {
            "configurable": {
                "thread_id": (
                    self.thread_id
                ),
            },
        }

    def in_use(self) -> str | None:
        """The spreadsheet this conversation is working on, if any.

        Kept in the conversation's own state rather than in a module global,
        so that two browser sessions in one process are working on two
        spreadsheets rather than fighting over one.
        """
        state = self.agent.get_state(
            self._where
        )

        return state.values.get(
            "spreadsheet_name"
        )

    def use(
        self,
        name: str,
        spreadsheet_id: str | None = None,
    ) -> None:
        """Work on this spreadsheet for the rest of the conversation."""
        self.agent.update_state(
            self._where,
            {
                "spreadsheet_id": (
                    spreadsheet_id
                ),
                "spreadsheet_name": (
                    name
                ),
            },
        )

    def reset(self) -> None:
        """Start another conversation thread, on no spreadsheet.

        A new thread has no state, so the spreadsheet goes with the
        conversation that chose it rather than outliving it. The one named in
        the environment is not put back either: it says which file to open on,
        and that was the conversation being left behind.
        """
        self.thread_id = (
            new_thread()
        )

    def ask(
        self,
        question: str,
    ) -> Iterator[Event]:
        """Run one turn and emit application-level events."""
        yield from self._run(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": question,
                    }
                ]
            }
        )

    def resume(
        self,
        decisions: dict | None = None,
    ) -> Iterator[Event]:
        """Carry on a turn that stopped to ask permission.

        Nothing pauses a turn yet, because no tool is wired to interrupt, so
        this continues a graph that was not waiting for anything. It exists now
        so the shape of a paused turn is settled while the contract is being
        frozen. Filled in when HumanInTheLoopMiddleware lands.
        """
        yield from self._run(
            Command(
                resume=decisions or {}
            )
        )

    def _run(
        self,
        payload,
    ) -> Iterator[Event]:
        """Stream one graph run, and turn what it emits into events."""
        config = {
            "configurable": {
                "thread_id": (
                    self.thread_id
                ),
            },
            "recursion_limit": (
                RECURSION_LIMIT
            ),
            "run_name": (
                self.name
            ),
        }

        final_answer = ""
        cut_off = False
        artifacts: list[dict] = []

        try:
            for _, mode, payload in (
                self.agent.stream(
                    payload,
                    config=config,
                    stream_mode=[
                        "updates",
                        "messages",
                    ],
                    subgraphs=True,
                )
            ):
                if mode == "messages":
                    token, _ = payload

                    if (
                        self.stream_text
                        and token.content
                    ):
                        yield Text(
                            str(
                                token.content
                            )
                        )

                    continue

                for update in (
                    payload.values()
                ):
                    if not isinstance(
                        update,
                        dict,
                    ):
                        continue

                    written = update.get(
                        "final_answer"
                    )

                    if written:
                        final_answer = str(written)
                        cut_off = False

                    messages = (
                        update.get(
                            "messages"
                        )
                        or []
                    )

                    for message in messages:
                        # Outer orchestrator tool calls.
                        for call in (
                            getattr(
                                message,
                                "tool_calls",
                                None,
                            )
                            or []
                        ):
                            if call["name"] in DECISION_NAMES:
                                continue

                            yield ToolCall(
                                name=call[
                                    "name"
                                ],
                                arguments=dict(
                                    call.get(
                                        "args"
                                    )
                                    or {}
                                ),
                            )

                        # Keep model text separate from tool output.
                        if isinstance(
                            message,
                            AIMessage,
                        ):
                            if message.content:
                                final_answer = (
                                    str(
                                        message.content
                                    )
                                )

                                cut_off = False

                            # Nothing said and the model stopped because it
                            # hit its length limit is not the same as nothing
                            # to say, and the two used to look identical.
                            elif (
                                (
                                    message.response_metadata
                                    or {}
                                ).get(
                                    "finish_reason"
                                )
                                == "length"
                            ):
                                cut_off = True

                        if not isinstance(
                            message,
                            ToolMessage,
                        ):
                            continue

                        outer_artifact = (
                            message.artifact
                        )

                        if outer_artifact is None:
                            continue

                        for artifact in (
                            _render_artifacts(
                                outer_artifact
                            )
                        ):
                            artifacts.append(
                                artifact
                            )

        except GraphRecursionError:
            yield Answer(
                GAVE_UP
            )
            return

        artifacts = (
            _merge_inspect_artifacts(
                artifacts
            )
        )

        # Emitted even when empty, so a client always gets one end-of-turn
        # answer and decides for itself what silence should look like.
        yield Answer(
            CUT_OFF
            if cut_off
            and not final_answer
            else final_answer
        )

        for artifact in artifacts:
            yield Artifact(
                artifact
            )


def rendered(
    call: ToolCall,
) -> str:
    """Render a tool call for CLI/UI debug displays."""
    arguments = ", ".join(
        f"{name}={value!r}"
        for name, value
        in call.arguments.items()
    )

    return (
        f"{call.name}"
        f"({arguments})"
    )