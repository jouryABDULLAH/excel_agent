"""A browser front end, run with `streamlit run src/excel_agent/ui.py`.

Talks to the same Session the command line does, so what is drawn here is the
same events printed there. Nothing in this file knows what a message or a
checkpointer is.

Streamlit runs this whole file again on every interaction, so anything that
must survive a click lives in st.session_state: the agent, and the transcript
drawn above the box.
"""

import time
from pathlib import Path

import streamlit as st

from excel_agent import config
from excel_agent.browsing import IN_USE
from excel_agent.config import MODEL
from excel_agent.runner import Answer, Session, ToolCall, rendered
from excel_agent.subagents.factory import VARIANTS, agent_name, build

UPLOAD_SUFFIX = ".xlsx"

# Reading Drive costs a call over the network, and Streamlit runs this file
# again on every click. Held for a minute, so clicking about the page does not
# ask Google the same question a dozen times over. Cleared whenever the file
# being worked on changes, since both answers are about that file.
CACHE_SECONDS = 60

# Long enough to be seen, short enough that nobody waits for it. The class
# comes from the key on the container the suggestions are drawn in.
FADE_SECONDS = 0.35
FADE_OUT = """
<style>
@keyframes suggestions-away {
  to { opacity: 0; transform: translateY(-6px); }
}
.st-key-suggestions {
  animation: suggestions-away %.2fs ease-out forwards;
}
</style>
""" % FADE_SECONDS


def save_upload(upload, folder: Path) -> str:
    """Put an uploaded workbook in the folder, and say what happened.

    The name is cut back to its last part, so an upload calling itself
    ../../something cannot write outside the folder. A name already taken is
    refused rather than written over: the file it would replace is the user's,
    and a demo that quietly overwrites their sheet has nothing to put back.
    """
    name = Path(upload.name).name

    if not name.lower().endswith(UPLOAD_SUFFIX):
        return f"{name} is not a {UPLOAD_SUFFIX} file, so it was not saved."

    destination = folder / name
    if destination.exists():
        return f"There is already a workbook called {name}. Rename it and try again."

    folder.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(upload.getbuffer())
    return f"Saved {name}."


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def workbooks(backend: str) -> list[str]:
    """What can be worked on. Nothing here asks the model."""
    return IN_USE["workbooks"]()


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def suggestions(reading: str | None) -> list[str]:
    """A few things worth asking about the file in hand.

    Built from its column names rather than written down in advance, so what
    is offered fits the file that is open. Nothing here asks the model: it
    reads the header the same way the tools do.

    The argument is the file being worked on. Nothing uses it: it is there to
    be part of what the cache is keyed on, so that switching files asks about
    the new one rather than answering about the last. A name starting with an
    underscore would be left out of that key, which is the opposite.
    """
    return IN_USE["suggestions"]()


def heading() -> str:
    """The one line saying where a change would land, and how to go and see it.

    The sheet itself is the only view of the sheet worth having, so the page
    does not draw one: it points at the real thing instead, which is always
    right and shows a change landing as it lands.
    """
    said = f"Working on **{IN_USE['where']()}**"

    link = IN_USE["link"]()
    if link:
        said += f" · [open it]({link})"

    return said


def start(variant: str) -> None:
    """Build the agent and forget whatever was said to the last one."""
    st.session_state.variant = variant
    st.session_state.session = Session(build(variant), name=agent_name(variant))
    st.session_state.transcript = []


# A turn can end with nothing said: the answer is built from whatever message
# carried content, and there is no rule that one did. Drawn as an empty bubble
# it reads as the page having lost the answer, which is the one thing it must
# not be mistaken for. It does not say nothing happened, because the calls
# above it may well have changed the sheet.
NO_ANSWER = (
    "_This turn ended without anything being said. Any tool calls listed "
    "above did run, so look at the sheet before asking again._"
)


def draw_turn(events, box) -> dict:
    """Draw one turn as it happens, and return it for the transcript.

    Split out from the asking, so that when these events arrive over HTTP
    instead of from a Session this part does not change.
    """
    calls: list[str] = []
    answer = ""

    with box.status("Working", expanded=True) as status:
        for event in events:
            if isinstance(event, ToolCall):
                calls.append(rendered(event))
                status.write(rendered(event))
            elif isinstance(event, Answer):
                answer = event.text
        status.update(label=f"{len(calls)} tool call(s)", state="complete", expanded=False)

    said = answer.strip() or NO_ANSWER
    box.markdown(said)
    return {"role": "assistant", "text": said, "calls": calls}


def draw_transcript() -> None:
    """Redraw what has been said, because Streamlit runs this file again."""
    for said in st.session_state.transcript:
        with st.chat_message(said["role"]):
            if said.get("calls"):
                with st.expander(f"{len(said['calls'])} tool call(s)"):
                    for call in said["calls"]:
                        st.code(call, language="python")
            st.markdown(said["text"])


def sidebar() -> None:
    """The files there are, which one is in use, and how to add another."""
    with st.sidebar:
        st.subheader(IN_USE["noun"])

        names = workbooks(config.BACKEND)
        in_use = IN_USE["in_use"]()

        if not names:
            st.info(IN_USE["empty"])
        else:
            # A list, not a row of buttons: eleven spreadsheets as a radio is a
            # wall of text. Nothing is chosen when the agent starts against
            # Drive without EXCEL_AGENT_SPREADSHEET set, so it starts empty and
            # the line under the title says as much.
            chosen = st.selectbox(
                "Working on",
                names,
                index=names.index(in_use) if in_use in names else None,
                placeholder="Choose one, or ask",
                label_visibility="collapsed",
            )
            if chosen and chosen != in_use:
                # The same switch /use makes at the command line.
                try:
                    IN_USE["choose"](chosen)
                except ValueError as explanation:
                    st.error(str(explanation))
                else:
                    # What was read about the last file says nothing about
                    # this one, and both are keyed on a name that has changed.
                    suggestions.clear()
                    st.rerun()

        if IN_USE["uploads"]:
            upload = st.file_uploader("Add a workbook", type=["xlsx"])
            if upload is not None:
                st.write(save_upload(upload, config.DATA_DIR))
                workbooks.clear()
        else:
            st.caption("Spreadsheets come from your Drive. Add one there.")

        st.divider()
        st.subheader("Agent")
        variant = st.radio(
            "Agents",
            VARIANTS,
            index=VARIANTS.index(st.session_state.variant),
            label_visibility="collapsed",
            help="single is one agent holding every tool, multi delegates to subagents",
        )
        if variant != st.session_state.variant:
            start(variant)
            st.rerun()

        if st.button("New conversation"):
            st.session_state.session.reset()
            st.session_state.transcript = []
            st.rerun()

        st.caption(f"{MODEL}")


def main() -> None:
    """Draw the page and answer whatever is typed into it."""
    # Opens as a chat and nothing else. Everything in the sidebar is either a
    # setting or a way out of a corner, and none of it is worth the width on
    # the way in: the first thing anyone should see is somewhere to type.
    st.set_page_config(
        page_title=IN_USE["title"],
        page_icon=":bar_chart:",
        initial_sidebar_state="collapsed",
    )
    st.title(IN_USE["title"])

    if "session" not in st.session_state:
        try:
            start("single")
        except RuntimeError as explanation:
            # A missing API key explains itself. Better said here than as a
            # stack trace in front of an audience.
            st.error(str(explanation))
            st.stop()

    sidebar()
    st.caption(heading())
    draw_transcript()

    picked = None
    holder = st.empty()
    if not st.session_state.transcript:
        with holder.container(key="suggestions"):
            st.caption("Try one of these")
            asks = suggestions(IN_USE["in_use"]())
            # Two to a row, filled across before down, so they read in the
            # order they are written.
            for first in range(0, len(asks), 2):
                for column, ask in zip(st.columns(2), asks[first : first + 2]):
                    if column.button(ask, use_container_width=True):
                        picked = ask

    question = st.chat_input("Ask for a change, or a look at the sheet") or picked
    if not question:
        return

    if picked:
        # Faded out and then taken off the page. Left up, they would be
        # buttons from a run that has ended: clicking one again does nothing,
        # which reads as the page ignoring you.
        st.markdown(FADE_OUT, unsafe_allow_html=True)
        time.sleep(FADE_SECONDS)
        holder.empty()

    st.session_state.transcript.append({"role": "user", "text": question})
    with st.chat_message("user"):
        st.markdown(question)

    was = IN_USE["in_use"]()

    with st.chat_message("assistant"):
        box = st.container()
        try:
            said = draw_turn(st.session_state.session.ask(question), box)
        except Exception as error:  # noqa: BLE001 - one bad turn must not end the session
            box.error(f"That went wrong: {error}")
            # Kept, rather than returned on. Dropping it leaves the question on
            # the page with nothing under it once anything redraws, which reads
            # as the answer having gone missing rather than as a turn failing.
            said = {
                "role": "assistant",
                "text": f"That went wrong: {error}",
                "calls": [],
            }

    st.session_state.transcript.append(said)

    # The agent can move to another file part way through a turn, and the line
    # saying where the work is going was drawn before it did. Left alone, the
    # page would name the old file until something else was clicked.
    if IN_USE["in_use"]() != was:
        st.rerun()


# Streamlit runs this file as the main script, so this is where the page is
# drawn. Guarded so importing the module, as the tests do, does not build an
# agent and start talking to Groq.
if __name__ == "__main__":
    main()
