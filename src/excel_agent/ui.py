"""Streamlit browser front end for the spreadsheet agent.

Run with:

    streamlit run src/excel_agent/ui.py

The UI talks only to runner.Session. It does not know about LangGraph messages,
checkpointers, or subagent internals.

Streamlit reruns this file after interactions, so conversation state that must
survive a rerun lives in st.session_state.
"""

from collections.abc import Iterable

import streamlit as st

from excel_agent import browsing
from excel_agent.config import MODEL, build
from excel_agent.tools.rows import _runs
from excel_agent.runner import (
    Answer,
    Approval,
    Artifact,
    Session,
    ToolCall,
    rendered,
)
from excel_agent.graph.graph import build_graph
from excel_agent.model import build_model


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CACHE_SECONDS = 60

FADE_SECONDS = 0.25

# Where the sidebar picker keeps what was chosen. Named because both the reset
# and a spreadsheet chosen by the agent have to clear it: while it holds a
# value Streamlit draws that value, whatever the code passes as the index.
WORKBOOK_CHOICE = "workbook_choice"

NO_ANSWER = (
    "_This turn finished without a written response. Any actions shown above "
    "did run, so check the spreadsheet before trying again._"
)


PAGE_CSS = """
<style>

/* Give the conversation a little more room without making it dashboard-wide.
   The top padding clears Streamlit's own fixed toolbar: below about 3.5rem the
   title slides under it and its ascenders are cut off. */
.block-container {
    max-width: 1050px;
    padding-top: 4rem;
    padding-bottom: 5rem;
}

/* Compact application title. */
.excel-agent-title {
    font-size: 2rem;
    font-weight: 650;
    line-height: 1.15;
    margin-bottom: 0.15rem;
}

/* Muted spreadsheet context under the title. */
.excel-agent-context {
    color: rgba(128, 128, 128, 0.95);
    margin-bottom: 1.4rem;
}

/* Empty-state heading. */
.excel-agent-welcome {
    text-align: center;
    margin-top: 4rem;
    margin-bottom: 1.75rem;
}

.excel-agent-welcome h2 {
    margin-bottom: 0.4rem;
}

.excel-agent-welcome p {
    color: rgba(128, 128, 128, 0.95);
}

/* Keep suggestion buttons visually lighter than primary actions. */
.st-key-suggestions button {
    min-height: 3.25rem;
    text-align: left;
}

/* Fade starter suggestions away after one is selected. */
@keyframes suggestions-away {
    to {
        opacity: 0;
        transform: translateY(-6px);
    }
}

.st-key-suggestions.suggestions-leaving {
    animation: suggestions-away %.2fs ease-out forwards;
}

</style>
""" % FADE_SECONDS


# ---------------------------------------------------------------------------
# Cached browsing information
# ---------------------------------------------------------------------------


@st.cache_data(
    ttl=CACHE_SECONDS,
    show_spinner=False,
)
def workbooks() -> list[str]:
    """Return spreadsheets reachable by the application."""
    return browsing.workbooks()


@st.cache_data(
    ttl=CACHE_SECONDS,
    show_spinner=False,
)
def suggestions(reading: str | None) -> list[str]:
    """Return context-aware starter questions for the selected spreadsheet.

    `reading` is both the spreadsheet asked about and the cache key, so
    switching spreadsheets invalidates the cached result by construction.
    """
    return browsing.suggestions(reading)


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


def start() -> None:
    """Create a new agent session and empty UI transcript."""
    st.session_state.session = Session(build_graph(build_model()))

    st.session_state.transcript = []


def reset_conversation() -> None:
    """Start a fresh conversation, on no spreadsheet.

    A new conversation is a new thread, and the chosen spreadsheet lives in
    that thread's state, so it goes with it.

    The picker is cleared rather than set, so that the value the sidebar draws
    is worked out from what is in use rather than held over from the last
    conversation.
    """
    st.session_state.session.reset()
    st.session_state.transcript = []

    st.session_state.pop(WORKBOOK_CHOICE, None)

    # The starters belong to the spreadsheet that has just been let go of.
    suggestions.clear()


def ensure_state() -> None:
    """Build the agent once for this Streamlit browser session."""
    if "session" in st.session_state:
        return

    try:
        start()
    except RuntimeError as explanation:
        st.error(str(explanation))
        st.stop()


# ---------------------------------------------------------------------------
# Human-friendly activity labels
# ---------------------------------------------------------------------------


def activity_label(worker: str | None) -> str:
    """Describe what is happening without naming the machinery.

    Keyed on the specialist doing the work. It used to key on a tool named
    after a subagent; delegating is not a tool call any more.
    """
    labels = {
        "file_manager": "Finding the spreadsheet...",
        "analyst": "Reading the spreadsheet...",
        "row_editor": "Updating rows...",
        "structure_editor": "Updating the spreadsheet...",
        "chart_maker": "Working on the chart...",
    }

    return labels.get(
        worker, # type: ignore
        "Working on it...",
    ) # type: ignore


def action_label(count: int) -> str:
    """Heading for a collapsed action trace."""
    if count == 1:
        return "1 action"

    return f"{count} actions"


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def draw_header() -> None:
    """Draw the app title and selected-spreadsheet context."""
    st.markdown(
        f'<div class="excel-agent-title">'
        f"{browsing.TITLE}"
        f"</div>",
        unsafe_allow_html=True,
    )

    in_use = st.session_state.session.in_use()

    current = browsing.where(in_use)
    link = browsing.link(in_use)

    if link:
        context = (
            f"Working on **{current}**"
            # f" · [Open in Google Sheets ↗]({link})"
        )
    else:
        context = f"Working on **{current}**"

    st.markdown(
        f'<div class="excel-agent-context">'
        f"{context}"
        f"</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Tool trace
# ---------------------------------------------------------------------------

def draw_artifact(
    artifact: dict,
    box,
) -> None:
    """Render structured spreadsheet data without asking the model to rewrite it."""
    operation = artifact.get("operation")

    if operation == "inspect_sheet":
        rows = artifact.get("rows") or []
        columns = artifact.get("headers") or []

        if not rows or not columns:
            return

        table = []

        for item in rows:
            values = item.get("values") or {}

            table.append(
                {
                    "row": item.get("row"),
                    **{
                        column: values.get(column, "")
                        for column in columns
                    },
                }
            )

        box.dataframe(
            table,
            use_container_width=True,
            hide_index=True,
        )
        return

    if operation == "find_data":
        matches = artifact.get("matches") or []

        if not matches:
            return

        columns = list(
            (matches[0].get("values") or {}).keys()
        )

        table = []

        for item in matches:
            values = item.get("values") or {}

            table.append(
                {
                    "row": item.get("row"),
                    "matched in": item.get("matched_in"),
                    **{
                        column: values.get(column, "")
                        for column in columns
                    },
                }
            )

        box.dataframe(
            table,
            use_container_width=True,
            hide_index=True,
        )
        return

def draw_actions(
    calls: list[str],
    *,
    container=None,
) -> None:
    """Draw technical tool calls as optional secondary information."""
    if not calls:
        return

    target = container or st

    with target.expander(
        action_label(len(calls)),
        expanded=False,
    ):
        for call in calls:
            st.code(
                call,
                language="python",
                wrap_lines=True,
            )


# ---------------------------------------------------------------------------
# Turn rendering
# ---------------------------------------------------------------------------


def draw_turn(
    events: Iterable[ToolCall | Answer],
    box,
) -> dict:
    """Render one assistant turn while it executes.

    The user sees a simple progress label while work is happening. Exact tool
    calls are preserved and shown afterwards inside a collapsed expander.
    """
    
    calls: list[str] = []
    answer = ""
    artifacts: list[dict] = []
    waiting: list[dict] = []

    status_placeholder = box.empty()

    with status_placeholder.status(
        "Working on it...",
        expanded=False,
    ) as status:
        for event in events:
            if isinstance(event, ToolCall):
                calls.append(
                    rendered(event)
                )

                status.update(
                    label=activity_label(event.worker),
                )

            elif isinstance(event, Answer):
                answer = event.text

            elif isinstance(event, Artifact):
                artifacts.append(event.data)

            elif isinstance(event, Approval):
                waiting.append(
                    {
                        "tool": event.tool,
                        "arguments": event.arguments,
                        "id": event.id,
                        "task": event.task,
                    }
                )

        status.update(
            label="Done",
            state="complete",
            expanded=False,
        )

    status_placeholder.empty()

    said = answer.strip()

    if said:
        box.markdown(said)

    for artifact in artifacts:
        draw_artifact(
            artifact,
            box,
        )

    if not said and not artifacts and not waiting:
        box.markdown(NO_ANSWER)

    draw_actions(
        calls,
        container=box,
    )

    return {
        "role": "assistant",
        "text": said,
        "calls": calls,
        "artifacts": artifacts,
        "waiting": waiting,
    }


def asking_permission() -> dict | None:
    """The change waiting to be allowed, if the last turn stopped to ask."""
    if not st.session_state.transcript:
        return None

    last = st.session_state.transcript[-1]

    if last.get("role") == "assistant" and last.get("waiting"):
        return last

    return None


def _decisions(waiting: list[dict], decision: dict) -> dict:
    """The same answer given to every change the turn stopped on.

    One decision per change, grouped under the pause it belongs to: a model
    that asks for three rows at once is three changes, and a single decision
    would leave two of them unanswered.
    """
    pauses: dict[str, list[dict]] = {}

    for one in waiting:
        pauses.setdefault(one["id"], []).append(one)

    return {
        pause: {
            "decisions": [
                dict(decision) for _ in asked
            ]
        }
        for pause, asked in pauses.items()
    }


def _once(before: list[str], after: list[str]) -> list[str]:
    """The resumed half's actions, without the one it replayed.

    Resuming re-runs the specialist from the start, so the call that was
    approved is made again and would be listed twice for one change.
    """
    if before and after and after[0] == before[-1]:
        return after[1:]

    return after


def joined(paused: dict, said: dict) -> dict:
    """One turn again, after stopping to ask split it in two.

    The half before the question holds the actions but no answer, so left as
    a turn of its own it is drawn as one that finished without saying
    anything.
    """
    return {
        **paused,
        "text": "\n\n".join(
            one
            for one in (paused["text"], said["text"])
            if one
        ),
        "calls": paused["calls"] + _once(paused["calls"], said["calls"]),
        "artifacts": (
            paused["artifacts"] + said["artifacts"]
        ),
        # A turn can stop twice, when it has more than one change to make.
        "waiting": said["waiting"],
    }


def decide(decision: dict) -> None:
    """Answer the question the turn stopped on, and let it finish."""
    paused = st.session_state.transcript[-1]
    waiting = paused.get("waiting") or []

    paused["waiting"] = []

    with st.chat_message("assistant"):
        said = draw_turn(
            st.session_state.session.resume(
                _decisions(waiting, decision)
            ),
            st.container(),
        )

    st.session_state.transcript[-1] = joined(paused, said)

    st.rerun()


# What a cancelled change answers its tool call with. Shaped like every other
# tool failure, because that is the one refusal this model reliably reads as
# final: told "rejected, do not retry" as feedback, it retried; handed this as
# the call's result, it reported the outcome and stopped. Measured, not
# assumed.
REFUSED = (
    '{"ok": false, "error": "rejected_by_user", "message": '
    '"The user declined this change. Nothing was changed. '
    'Report this outcome; never retry the call."}'
)


def _shown(name: str, value) -> str:
    """One argument as a card line.

    Row numbers are written as ranges, the way a person says them. Anything
    else is shown as it is: rows also carries a value per row for fill_rows,
    and a card must never be the thing that breaks the page.
    """
    numbers = isinstance(value, list) and all(
        isinstance(one, int) for one in value
    )

    if name == "rows" and numbers:
        value = ", ".join(
            str(first) if first == last else f"{first}–{last}"
            for first, last in _runs(sorted(set(value)))
        )

    elif name == "rows" and isinstance(value, list):
        value = f"{len(value)} row(s), each with its own values"

    return f"**{name.replace('_', ' ').capitalize()}:** {value}"


def permission_card(one: dict) -> str:
    """The pending change, laid out as what, where and why."""
    arguments = one.get("arguments") or {}

    lines = [
        f"**Task:** {one['task']}" if one.get("task") else None,
        f"**Action:** {one['tool']}",
        *(
            _shown(name, value)
            for name, value in arguments.items()
            if name != "spreadsheet"
        ),
        "**Spreadsheet:** "
        + str(
            arguments.get("spreadsheet")
            or st.session_state.session.in_use()
            or "not chosen"
        ),
        "",
        "There is no undo.",
    ]

    return "\n\n".join(line for line in lines if line is not None)


def draw_permission(turn: dict) -> None:
    """Show what is about to change, and offer to allow or refuse it.

    Drawn from the transcript rather than inside the turn that asked, so the
    question is still there after a rerun rather than only in the run that
    produced it.
    """
    with st.chat_message("assistant"):
        for one in turn["waiting"]:
            st.warning(permission_card(one), icon="⚠️")

        allow, refuse = st.columns(2)

        if allow.button("Allow", type="primary", width="stretch"):
            decide({"type": "approve"})

        if refuse.button("Cancel", width="stretch"):
            decide({"type": "respond", "message": REFUSED})


def draw_transcript() -> None:
    """Redraw previous turns after Streamlit reruns the page."""
    for turn in st.session_state.transcript:
        role = turn["role"]

        with st.chat_message(role):

            if turn.get("text"):
                st.markdown(
                    turn["text"]
                )

            for artifact in (
                turn.get("artifacts")
                or []
            ):
                draw_artifact(
                    artifact,
                    st,
                )

            if role == "assistant":
                # The same rule draw_turn drew it by, so a turn reads the
                # same before and after a rerun.
                if (
                    not turn.get("text")
                    and not turn.get("artifacts")
                    and not turn.get("waiting")
                ):
                    st.markdown(NO_ANSWER)

                draw_actions(
                    turn.get("calls", [])
                )

# ---------------------------------------------------------------------------
# Empty state / suggestions
# ---------------------------------------------------------------------------


def draw_empty_state() -> str | None:
    """Show starter suggestions until the first conversation turn."""
    if st.session_state.transcript:
        return None

    st.markdown(
        """
        <div class="excel-agent-welcome">
            <h2>What would you like to do?</h2>
            <p>
                Ask about the spreadsheet, change its data,
                format it, or create a chart.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    picked = None

    holder = st.empty()

    with holder.container(
        key="suggestions",
    ):
        asks = suggestions(
            st.session_state.session.in_use()
        )

        for first in range(
            0,
            len(asks),
            2,
        ):
            columns = st.columns(2)

            for column, ask in zip(
                columns,
                asks[first : first + 2],
            ):
                if column.button(
                    ask,
                    key=f"suggestion-{first}-{ask}",
                    use_container_width=True,
                ):
                    picked = ask

    if picked:
        # Remove stale starter buttons before running the selected turn.
        holder.empty()

    return picked


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


def sidebar() -> None:
    """Draw spreadsheet and conversation controls."""
    with st.sidebar:
        st.subheader("Spreadsheet")

        # A dropped connection to Google must not take the page with it: the
        # chat still works, and the picker comes back on the next rerun.
        try:
            names = workbooks()
        except Exception:  # noqa: BLE001
            st.warning("Couldn't reach Google Drive — refresh to retry.")
            names = None

        in_use = st.session_state.session.in_use()

        if names is None:
            # Unreachable Drive: the warning above already said so.
            names = []

        elif not names:
            # An empty Drive, as opposed to an unreachable one.
            st.info(browsing.EMPTY)

        else:
            selected_index = (
                names.index(in_use)
                if in_use in names
                else None
            )

            chosen = st.selectbox(
                "Spreadsheet",
                options=names,
                index=selected_index,
                placeholder="Choose one, or ask",
                label_visibility="collapsed",
                key=WORKBOOK_CHOICE,
            )

            if (
                chosen
                and chosen != in_use
            ):
                try:
                    _, title = browsing.choose(chosen)

                except ValueError as explanation:
                    st.error(
                        str(explanation)
                    )

                else:
                    st.session_state.session.use(title)

                    # Workbook lists are still valid, but suggestions belong
                    # to the previously selected spreadsheet.
                    suggestions.clear()

                    st.rerun()

        # Resolved through Drive, so it fails the same way the listing does.
        try:
            link = browsing.link(in_use)
        except Exception:  # noqa: BLE001
            link = None

        if link:
            st.link_button(
                "Open in Google Sheets ↗",
                link,
                use_container_width=True,
            )

        st.divider()

        st.subheader("Conversation")

        if st.button(
            "＋ New conversation",
            use_container_width=True,
        ):
            reset_conversation()
            st.rerun()

        st.divider()

        st.caption("Model")
        st.code(
            MODEL,
            language=None,
        )

        running = build()

        if running:
            # A page open across a code change looks the same as one that is
            # not, so a fix that is not running reads as a fix that failed.
            st.caption(f"Build {running}")


# ---------------------------------------------------------------------------
# Main interaction
# ---------------------------------------------------------------------------


def handle_question(
    question: str,
) -> None:
    """Add one user question, run the agent, and save its answer."""
    st.session_state.transcript.append(
        {
            "role": "user",
            "text": question,
        }
    )

    with st.chat_message("user"):
        st.markdown(question)

    spreadsheet_before = st.session_state.session.in_use()

    with st.chat_message("assistant"):
        box = st.container()

        try:
            said = draw_turn(
                st.session_state.session.ask(
                    question
                ),
                box,
            )

        except Exception as error:  # noqa: BLE001
            message = (
                f"That went wrong: {error}"
            )

            box.error(message)

            said = {
                "role": "assistant",
                "text": message,
                "calls": [],
                "artifacts": [],
            }

    st.session_state.transcript.append(
        said
    )

    # main() looks for a waiting change before it runs the question, so a turn
    # that stops has to redraw to ask; otherwise the question sits unshown
    # until something else reruns the page.
    if said.get("waiting"):
        st.rerun()

    # A turn can select another spreadsheet. The header/sidebar were rendered
    # before the agent changed it, so rerun once to make the UI truthful.
    if st.session_state.session.in_use() != spreadsheet_before:
        # Dropped rather than set to the new name: the picker draws whatever it
        # holds, so leaving the old choice there would show one spreadsheet
        # while the agent worked on another.
        st.session_state.pop(WORKBOOK_CHOICE, None)

        suggestions.clear()
        st.rerun()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def main() -> None:
    """Render the Streamlit application."""
    st.set_page_config(
        page_title=browsing.TITLE,
        page_icon="📊",
        layout="centered",
        initial_sidebar_state="collapsed",
    )

    st.markdown(
        PAGE_CSS,
        unsafe_allow_html=True,
    )

    ensure_state()

    sidebar()
    draw_header()
    draw_transcript()

    pending = asking_permission()

    if pending:
        draw_permission(pending)
        return

    picked = draw_empty_state()

    typed = st.chat_input(
        "Ask about or edit the spreadsheet"
    )

    question = typed or picked

    if not question:
        return

    handle_question(
        question.strip()
    )


if __name__ == "__main__":
    main()