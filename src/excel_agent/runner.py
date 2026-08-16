"""One conversation turn exposed as plain application events.

The runner is the boundary between the LangChain/LangGraph agent and clients
such as the CLI and Streamlit UI.

Model text remains model text. Structured tool artifacts are carried
separately and never replace the model's final answer.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from excel_agent.model import (
    GAVE_UP,
    RECURSION_LIMIT,
    new_thread,
)


# ---------------------------------------------------------------------------
# Public events
# ---------------------------------------------------------------------------

# data classes for the UI
@dataclass
class ToolCall:
    """A tool the model asked to run."""

    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class Text:
    """One streamed piece of model text."""

    text: str


@dataclass
class Artifact:
    """Structured application data produced while completing the turn.

    Artifacts are not answers. They are deterministic data that a client may
    choose to render specially, such as spreadsheet rows or search matches.
    """

    data: dict


@dataclass
class Answer:
    """The final user-facing model answer."""

    text: str


# ---------------------------------------------------------------------------
# Artifact handling
# ---------------------------------------------------------------------------


def _render_artifacts(
    artifact: object,
) -> list[dict]:
    """Return inner tool artifacts only when they should be displayed."""
    if not isinstance(artifact, dict):
        return []

    if not artifact.get("render_data"):
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
        if isinstance(item, dict)
    ]

def _merge_inspect_artifacts(
    artifacts: list[dict],
) -> list[dict]:
    """Merge consecutive inspect_sheet pages from the same paginated read."""
    result: list[dict] = []

    for artifact in artifacts:
        if artifact.get("operation") != "inspect_sheet":
            result.append(artifact)
            continue

        current = {
            **artifact,
            "rows": list(
                artifact.get("rows")
                or []
            ),
        }

        if not result:
            result.append(current)
            continue

        previous = result[-1]

        if previous.get("operation") != "inspect_sheet":
            result.append(current)
            continue

        previous_last = previous.get(
            "last_returned_row"
        )

        current_first = artifact.get(
            "first_returned_row"
        )

        continuous = (
            isinstance(previous_last, int)
            and isinstance(current_first, int)
            and current_first == previous_last + 1
        )

        same_read = (
            previous.get("spreadsheet")
            == artifact.get("spreadsheet")
            and previous.get("sheet")
            == artifact.get("sheet")
            and previous.get("columns")
            == artifact.get("columns")
            and continuous
        )

        if not same_read:
            result.append(current)
            continue

        previous_rows = previous.setdefault(
            "rows",
            [],
        )

        previous_rows.extend(
            artifact.get("rows")
            or []
        )

        previous["last_returned_row"] = (
            artifact.get(
                "last_returned_row"
            )
        )

        previous["returned_rows"] = len(
            previous_rows
        )

        previous["has_more"] = artifact.get(
            "has_more",
            False,
        )

        previous["next_start_row"] = (
            artifact.get(
                "next_start_row"
            )
        )

        # Keep these aligned with the latest page in case the final
        # page carries the authoritative values.
        previous["last_data_row"] = (
            artifact.get(
                "last_data_row",
                previous.get("last_data_row"),
            )
        )

        previous["total_data_rows"] = (
            artifact.get(
                "total_data_rows",
                previous.get("total_data_rows"),
            )
        )

        previous["charts"] = artifact.get(
            "charts",
            previous.get("charts", []),
        )

    return result

# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class Session:
    """One persisted conversation with the orchestrator."""

    def __init__(
        self,
        agent,
        stream_text: bool = False,
        name: str = "orchestrator",
    ):
        self.agent = agent
        self.stream_text = stream_text
        self.thread_id = new_thread()
        self.name = name

    def reset(self) -> None:
        """Start a fresh conversation thread."""
        self.thread_id = new_thread()

    def ask(
        self,
        question: str,
    ) -> Iterator[
        ToolCall
        | Text
        | Artifact
        | Answer
    ]:
        """Run one user turn and emit application-level events.

        Important contract:

        - AIMessage content determines the final Answer.
        - Tool artifacts NEVER replace that Answer.
        - Structured read artifacts are emitted independently as Artifact
          events for clients that want deterministic rendering.
        """
        config = {
            "configurable": {
                "thread_id": self.thread_id,
            },
            "recursion_limit": RECURSION_LIMIT,
            "run_name": self.name,
        }

        final_answer = ""

        # Preserve artifacts in execution order. This is a list, not one
        # "candidate", because a full-sheet read may require several pages.
        artifacts: list[dict] = []

        # Prevent duplicate Artifact events if Stream updates expose the same
        # ToolMessage more than once.
        seen_artifacts: set[int] = set()

        try:
            for mode, payload in self.agent.stream(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": question,
                        }
                    ]
                },
                config=config,
                stream_mode=[
                    "updates",
                    "messages",
                ],
            ):
                # -----------------------------------------------------------
                # Streaming model tokens
                # -----------------------------------------------------------
                if mode == "messages":
                    token, _ = payload

                    if (
                        self.stream_text
                        and token.content
                    ):
                        yield Text(
                            str(token.content)
                        )

                    continue

                # -----------------------------------------------------------
                # State updates
                # -----------------------------------------------------------
                for update in payload.values():
                    if not isinstance(
                        update,
                        dict,
                    ):
                        continue

                    messages = (
                        update.get("messages")
                        or []
                    )

                    for message in messages:

                        # Tool calls requested by an AI message.
                        for call in (
                            getattr(
                                message,
                                "tool_calls",
                                None,
                            )
                            or []
                        ):
                            yield ToolCall(
                                call["name"],
                                dict(
                                    call["args"]
                                ),
                            )

                        # Final/narrative model content.
                        #
                        # Keep the latest non-empty AI answer, but never
                        # substitute tool output for it.
                        if (
                            isinstance(
                                message,
                                AIMessage,
                            )
                            and message.content
                        ):
                            final_answer = str(
                                message.content
                            )

                        # Structured data returned by delegated tools.
                        if isinstance(
                            message,
                            ToolMessage,
                        ):
                            outer_artifact = (
                                message.artifact
                            )

                            if (
                                outer_artifact
                                is None
                            ):
                                continue

                            # The same object may be surfaced in more than one
                            # streaming update.
                            identity = id(
                                outer_artifact
                            )

                            if (
                                identity
                                in seen_artifacts
                            ):
                                continue

                            seen_artifacts.add(
                                identity
                            )

                            for artifact in _render_artifacts(
                                outer_artifact
                            ):
                                artifacts.append(
                                    artifact
                                )

        except GraphRecursionError:
            yield Answer(GAVE_UP)
            return

        # Artifacts come before the final response so a UI can render the
        # deterministic data and then the conversational explanation.
        artifacts = _merge_inspect_artifacts(
            artifacts
        )

        if final_answer:
            yield Answer(final_answer)

        for artifact in artifacts:
            yield Artifact(
                artifact
            )


def rendered(
    call: ToolCall,
) -> str:
    """Render a tool call for CLI/debugging displays."""
    arguments = ", ".join(
        f"{name}={value!r}"
        for name, value
        in call.arguments.items()
    )

    return (
        f"{call.name}"
        f"({arguments})"
    )