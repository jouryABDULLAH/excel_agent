"""A chat model that reads from a script instead of thinking.

Lives on its own because more than one test file drives an agent with it. The
stock fake models refuse to bind tools, which is what this exists to do.
"""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedModel(BaseChatModel):
    """A chat model that reads from a script instead of thinking.

    Each call takes the next message off the front of the script, so a test
    can lay out a whole conversation in advance, tool calls and all.
    """

    script: list[BaseMessage]

    # Middleware that sizes itself against the context window asks the model
    # how big that is. A real ChatGroq reports it; this has to say something.
    profile: dict = {"max_input_tokens": 131072, "max_output_tokens": 65536}

    def bind_tools(self, tools, **kwargs):
        """Accept the tools and ignore them.

        The agent binds its tools to the model before it can be used, and the
        stock fake models refuse, which is why this one exists. What the
        script asks for is decided by the test, not by the tools on offer.
        """
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])

    @property
    def _llm_type(self) -> str:
        return "scripted"


def calling(name: str, call_id: str, **arguments) -> AIMessage:
    """A message asking for one tool call."""
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": arguments, "id": call_id}],
    )


class ScriptedJudge:
    """A judge that hands back prepared verdicts instead of thinking.

    The validator asks its model for structured output, so this stands in
    for that shape alone: with_structured_output gives back itself, and
    invoke returns the next prepared JudgeResult. An Exception in the list
    is raised instead, which is how a judge that cannot be reached is put
    into a test.
    """

    def __init__(self, results):
        self.results = iter(results)

    def with_structured_output(self, *arguments, **named):
        return self

    def invoke(self, messages):
        result = next(self.results)

        if isinstance(result, Exception):
            raise result

        return result
