"""What a tool reads out of the agent it is running inside."""


def chosen(runtime) -> str | None:
    """The spreadsheet the specialist running this tool was handed.

    A tool takes a name. When the model leaves the argument out, the name to
    use is the one the supervisor put into the specialist's state, and this
    is where a tool reaches it.

    Untyped on purpose: only something with state on it is wanted. Invoked
    outside an agent, as the tests do, there is no runtime and no name, and
    the caller falls through to whatever it would have done before.
    """
    if runtime is None:
        return None

    return (runtime.state or {}).get("spreadsheet_name")
