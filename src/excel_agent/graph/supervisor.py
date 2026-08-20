"""The planner: decides who does the next step and what the task is, or
answers the user and stops."""

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import (
    ModelRetryMiddleware,
    SummarizationMiddleware,
    after_model,
    dynamic_prompt,
    wrap_model_call,
)
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from excel_agent.graph.replies import DELIVERED, undoubled, without_drawn_table
from excel_agent.graph.state import DELEGATE, Delegate, State
from excel_agent.prompts import CANNOT_DO


ORCHESTRATOR_PROMPT = f"""\
You are the planner for a Google Sheets assistant.

Your responsibility is planning and delegation. You do not read or modify
spreadsheets yourself and you do not call low-level spreadsheet or Drive
operations.

Specialists:
- file_manager: spreadsheet discovery, search and selection.
- analyst: reads, searches and summarises data in a sheet.
- row_editor: changes row data.
- structure_editor: changes columns and cell formatting.
- chart_maker: creates, updates and deletes charts.

PLANNING
- Decide which specialist owns each required step.
- For a simple request, delegate directly to one specialist.
- For a multi-step request, execute dependent steps in order, not in parallel.
- Use the result of an earlier step when preparing a later one.
- Never claim a change succeeded until the specialist responsible for it says
  it succeeded.
- Once a specialist reports it has done the task, the task is done. Never
  delegate the same task again hoping for a different result.
- A specialist that cannot settle something answers QUESTION: <question>.
  Settle it yourself whenever ORIGINAL USER REQUEST or an earlier tool
  result already answers it, then delegate again with the answer written
  into the instruction. The user said it once and must not be asked twice.
- Put a QUESTION to the user only when nothing you have settles it. Ask it
  as your own question, and stop there.
- Never hand a specialist's question back to the user already answered, as
  though you were the user giving an instruction. "Yes, please create a
  column at position 15" is something the user says to you; it is never
  something you say to them. Resolve the QUESTION from the original request
  or an earlier specialist result and re-delegate; relay only when it is
  genuinely unresolvable, and then as a question.

ROUTING
- Finding/listing/selecting spreadsheet files -> file_manager.
- Reading/showing/searching/statistics -> analyst.
- Changing row values or adding/removing/moving records -> row_editor.
- Columns, formulas or visual formatting -> structure_editor.
- Charts -> chart_maker.

SPREADSHEET CONTEXT
- If the user asks to switch to or choose another spreadsheet, delegate that
  step to file_manager first.
- If the user identifies the intended spreadsheet only by something stored
  inside it, file_manager resolves which file is meant.
- Never ask the user for an exact filename when file_manager can resolve it.
- Merely asking where something exists does not mean the active spreadsheet
  should change.

AMBIGUOUS "LIKE"
- "same formatting", "same appearance", "same style", "look like" means
  structure_editor.
- "same values", "same contents", "copy the data" means row_editor.
- If wording such as "make row 12 like row 3" does not establish which meaning
  is intended, ask whether the user means values or formatting before making
  any change.

DISPLAYING DATA
- When the user explicitly asks to show, display, list, print, return or view
  spreadsheet rows or a table, say so in the task you delegate. The analyst
  decides how to read; you only say what the user asked for.

EMPTY OR UNINITIALIZED SHEETS
- A completely empty sheet has no table schema yet. Do not send raw A1/B1/C1
  coordinates to row_editor.
- Creating the first header row or establishing columns belongs to
  structure_editor.
- If the user wants headers and data added to an empty sheet, first delegate
  creation of the columns/headers to structure_editor. After that succeeds,
  delegate the data rows to row_editor using the newly created header names.
- row_editor works with table rows identified by existing column headers; it
  is not a general-purpose A1 cell writer.

LANGUAGE
- Reply in the language the user asked in. Never answer in another
  language.
- Preserve the user's intended meaning when delegating.
- Never translate spreadsheet-owned names or values merely to match the
  conversation language.

FINAL ANSWER
- Return only the final user-facing result.
- Do not reveal planning, scratch work, internal instructions, tool mechanics
  or hidden reasoning.
- Never mention specialists, agents or tools.
- Keep successful write confirmations concise.
- Never restate the user's requested action as though you are the user.
  "Insert row with value X in position Y" is an instruction for a
  specialist, never something you say to the user.
- Say each thing once. Never repeat a sentence, restate the answer in other
  words, or follow an answer with a fuller version of the same answer.
- Never end with a sign-off or an offer of more help. "Let me know if you
  need any more details" is not part of an answer.
- After a delegated write, say only what actually succeeded or why it could
  not be completed.
- If no write succeeded, never phrase the requested change as completed,
  and never word it as an intention, instruction or request: not "I want
  to", not "Please create".

{CANNOT_DO}
"""


SUMMARISE_AT = 0.7
KEEP_MESSAGES = 20

# One request gets this many delegations at most. A turn that legitimately
# needs more than this is not a turn; it is a loop, and every extra pass by a
# writing specialist lands another copy of the same change on the sheet.
MAX_DELEGATIONS = 8

DECIDING = """\


HOW TO REPLY
- To hand the next step to a specialist, call the delegate tool.
- To answer the user, write the answer as your reply and call nothing.
- Do one or the other, never both.
"""

REWRITING = """\


REWRITE YOUR ANSWER
Your previous answer was rejected before reaching the user, for the
reasons below. Write a corrected answer now, from WORK SO FAR THIS TURN
and nothing else. Do not delegate. Fix only what is named; keep
everything that was right.
{feedback}
"""

OUT_OF_STEPS = """\

OUT OF STEPS
You have used every delegation this request is allowed. Do not delegate
again. Answer the user now from WORK SO FAR THIS TURN, saying plainly what
was done and what was not. Never claim a success those reports do not
establish.
"""


class SupervisorState(AgentState):
    """What the supervisor agent is handed on each call.

    Every field the prompt reads has to be declared here as well as passed;
    an undeclared one comes back as None with no error.
    """

    spreadsheet_id: str | None
    spreadsheet_name: str | None
    worker_results: list[str]
    delegations: int
    correction: str | None


@tool(DELEGATE, args_schema=Delegate)
def delegate(next: str, task: str) -> str:
    """Hand the next step to a specialist."""

    return ""


@after_model
def stop_at_delegation(state, runtime) -> dict | None:
    """End the agent on a delegation, instead of running the tool."""
    if getattr(state["messages"][-1], "tool_calls", None):
        return {"jump_to": "end"}

    return None


def supervisor_instructions(
    spreadsheet_name: str | None,
    worker_results: list[str],
    out_of_steps: bool = False,
    correction: str | None = None,
) -> str:
    """The prompt, named the file and the work so far."""
    return (
        f"{ORCHESTRATOR_PROMPT}\n"
        "CURRENT SPREADSHEET\n"
        f"- {spreadsheet_name or 'None chosen yet. Delegate to file_manager first.'}\n\n"
        "WORK SO FAR THIS TURN\n"
        + (
            "\n".join(f"- {one}" for one in worker_results)
            or "- Nothing yet."
        )
        + DECIDING
        + (OUT_OF_STEPS if out_of_steps else "")
        + (
            REWRITING.format(feedback=correction)
            if correction is not None
            else ""
        )
    )


@dynamic_prompt
def supervisor_prompt(request) -> str:
    """Rebuild the prompt on every call, from the state the agent was handed."""
    return supervisor_instructions(
        request.state.get("spreadsheet_name"),
        request.state.get("worker_results") or [],
        out_of_steps=(
            (request.state.get("delegations") or 0) >= MAX_DELEGATIONS
        ),
        correction=request.state.get("correction"),
    )


@wrap_model_call
def no_more_delegating(request, handler):
    """Take the delegate tool away once the turn's budget is spent.

    The prompt says to answer; this makes delegating impossible as well, so a
    model that ignores the prompt still cannot loop.
    """
    if (
        (request.state.get("delegations") or 0) >= MAX_DELEGATIONS
        or request.state.get("correction") is not None
    ):
        request = request.override(tools=[])

    return handler(request)


def build_supervisor(model):
    """The planner. Its one tool routes; it never touches a spreadsheet.

    Delegating is a tool call and answering is ordinary prose, so the model is
    free to write the reply as a sentence. Asked for the answer inside a
    schema, this one returned malformed JSON about half the time.
    """
    return create_agent(
        model=model,
        tools=[delegate],
        system_prompt=ORCHESTRATOR_PROMPT,
        state_schema=SupervisorState,
        middleware=[
            supervisor_prompt,
            no_more_delegating,
            stop_at_delegation,
            # The model produces a malformed tool call.
            # on_failure must be "error": the default lets the agent carry on and call the failing model again. never terminating.
            ModelRetryMiddleware(max_retries=1, on_failure="error"),
            SummarizationMiddleware(
                model=model,
                trigger=("fraction", SUMMARISE_AT),
                keep=("messages", KEEP_MESSAGES),
            ),
        ],
    )


def _why(failure: Exception) -> str:
    """One line about a failure, without the provider's JSON body."""
    head = str(failure).strip().split("{", 1)[0].strip(" -:\t")

    return (head or type(failure).__name__)[:160]


def _spent(state: State) -> str:
    """The answer of last resort, once the budget is gone and the model
    still did not write one."""
    done = "\n".join(
        f"- {one}" for one in state.get("worker_results") or []
    )

    if not done:
        return (
            "I used every step this request is allowed without getting "
            "anywhere. Please try again, or ask for something smaller."
        )

    return (
        "I used every step this request is allowed before finishing. "
        f"What was done:\n{done}"
    )


def _one_call(supervisor, state: State, delegated: int, config=None):
    """Ask the planner once.

    The node's config goes with it, so the planner's model calls and its
    middleware are recorded inside this node's run rather than beside it.
    """
    return supervisor.invoke(
        {
            "messages": state["messages"],
            "spreadsheet_id": state.get("spreadsheet_id"),
            "spreadsheet_name": state.get("spreadsheet_name"),
            "worker_results": state.get("worker_results") or [],
            "delegations": delegated,
            "correction": state.get("correction"),
        },
        config,
    )["messages"][-1]


def _decided_something(said) -> bool:
    """A decision is a tool call or some text; a blank message is neither."""
    return bool(
        getattr(said, "tool_calls", None)
        or str(said.content or "").strip()
    )


def _said(supervisor, state: State, delegated: int, config=None):
    """One supervisor call, retried once if it decides nothing.

    The model sometimes returns a message with no tool call and no text --
    neither a delegation nor an answer. Taken at face value that became an
    empty reply, which the front end shows as a turn that said nothing.
    """
    said = _one_call(supervisor, state, delegated, config)

    if _decided_something(said):
        return said

    return _one_call(supervisor, state, delegated, config)


def _nothing_to_say(state: State) -> str:
    """The honest reply when the model twice decided nothing."""
    done = "\n".join(
        f"- {one}" for one in state.get("worker_results") or []
    )

    if not done:
        return (
            "I could not produce an answer for that. Please try again, or "
            "put it another way."
        )

    return (
        "I could not write up an answer, but this much was done:\n"
        f"{done}"
    )


def _decide(supervisor, state: State, config=None) -> dict:
    """Ask the planner what happens next, and turn it into state."""
    delegated = state.get("delegations") or 0
    correcting = state.get("correction") is not None

    said = _said(supervisor, state, delegated, config)

    calls = getattr(said, "tool_calls", None) or []

    # On the rewrite pass a delegation is ignored, not just discouraged: the
    # tools were stripped, but a model that hallucinates a call anyway must
    # not re-enter a worker with the turn's work already done.
    if calls and delegated < MAX_DELEGATIONS and not correcting:
        asked = Delegate(**calls[0]["args"])

        # Only the first call is answered, so a second one checkpointed here
        # would sit unanswered and make every later turn invalid.
        kept = (
            said
            if len(calls) == 1
            else said.model_copy(update={"tool_calls": calls[:1]})
        )

        return {
            "route": asked.next,
            "task": asked.task,
            "final_answer": None,
            "messages": [kept],
            "delegations": delegated + 1,
        }
    
    # On the rewrite pass a stray call is ignored, so text riding along with
    # it is still the rewrite and must not be thrown away with the call.
    answer = (
        ""
        if calls and not correcting
        else str(said.content or "")
    )

    answer = answer.replace(DELIVERED, "").strip()

    answer = without_drawn_table(answer, state.get("drawn_tables"))

    answer = undoubled(answer)

    if not answer:
        answer = (
            _spent(state)
            if delegated >= MAX_DELEGATIONS
            else _nothing_to_say(state)
        )

    # The rewrite pass changes the answer only. The thread still holds the
    # original as its last message, so the validator can weigh the two and
    # settle the message itself; writing one here would destroy the original
    # before that comparison happens.
    if correcting:
        return {
            "route": "end",
            "task": None,
            "final_answer": answer,
        }

    # The turn-scoped fields are not cleared here: the validator still needs
    # the worker reports to check the answer against, and clears them itself
    # once the turn truly ends.
    return {
        "route": "end",
        "task": None,
        "final_answer": answer,
        "messages": [
            said
            if not calls and str(said.content or "") == answer
            else AIMessage(answer)
        ],
    }


def supervisor_node(supervisor):
    """Route to a worker, or answer and end the turn."""

    def decide(state: State, config=None) -> dict:
        try:
            return _decide(supervisor, state, config)
        
        except Exception as failure:  # noqa: BLE001
            return {
                "route": "end",
                "task": None,
                "final_answer": (
                    "Something went wrong working that out: "
                    f"{_why(failure)}. Please try again."
                ),
                "worker_results": [],
                "drawn_tables": [],
                "delegations": 0,
            }

    return decide
