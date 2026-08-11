"""A browser front end, run with `streamlit run src/excel_agent/ui.py`.

Talks to the same Session the command line does, so what is drawn here is the
same events printed there. Nothing in this file knows what a message or a
checkpointer is.

Streamlit runs this whole file again on every interaction, so anything that
must survive a click lives in st.session_state: the agent, and the transcript
drawn above the box.
"""

from pathlib import Path

import streamlit as st

from excel_agent import config
from excel_agent.config import MODEL, resolve_workbook, workbook_names
from excel_agent.runner import Answer, Session, ToolCall, rendered
from excel_agent.subagents.factory import VARIANTS, build

UPLOAD_SUFFIX = ".xlsx"


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


def start(variant: str) -> None:
    """Build the agent and forget whatever was said to the last one."""
    st.session_state.variant = variant
    st.session_state.session = Session(build(variant))
    st.session_state.transcript = []


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

    box.markdown(answer)
    return {"role": "assistant", "text": answer, "calls": calls}


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
    """The workbooks there are, which one is in use, and how to add another."""
    with st.sidebar:
        st.subheader("Workbooks")

        names = workbook_names()
        if not names:
            st.info(f"No workbooks in {config.DATA_DIR.name} yet. Upload one below.")
        else:
            in_use = config.WORKBOOK_PATH.name
            chosen = st.radio(
                "Working on",
                names,
                index=names.index(in_use) if in_use in names else 0,
                label_visibility="collapsed",
            )
            if chosen != in_use:
                # The same switch /use makes at the command line.
                config.WORKBOOK_PATH = resolve_workbook(chosen)
                st.rerun()

        upload = st.file_uploader("Add a workbook", type=["xlsx"])
        if upload is not None:
            st.write(save_upload(upload, config.DATA_DIR))

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
    st.set_page_config(page_title="Excel agent", page_icon=":bar_chart:")
    st.title("Excel agent")

    if "session" not in st.session_state:
        try:
            start("single")
        except RuntimeError as explanation:
            # A missing API key explains itself. Better said here than as a
            # stack trace in front of an audience.
            st.error(str(explanation))
            st.stop()

    sidebar()
    st.caption(f"Working on **{config.WORKBOOK_PATH.name}**")
    draw_transcript()

    question = st.chat_input("Ask for a change, or a look at the sheet")
    if not question:
        return

    st.session_state.transcript.append({"role": "user", "text": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        box = st.container()
        try:
            said = draw_turn(st.session_state.session.ask(question), box)
        except Exception as error:  # noqa: BLE001 - one bad turn must not end the session
            box.error(f"That went wrong: {error}")
            return

    st.session_state.transcript.append(said)


# Streamlit runs this file as the main script, so this is where the page is
# drawn. Guarded so importing the module, as the tests do, does not build an
# agent and start talking to Groq.
if __name__ == "__main__":
    main()
