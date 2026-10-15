class AgentError(Exception):
    """Base exception for expected Agent failures."""


class AgentTimedOut(AgentError):
    """The Agent did not finish within the internal request budget."""


class BackendUnavailable(AgentError):
    """The configured Agent backend could not serve the request."""


class InvalidBackendResponse(AgentError):
    """The backend returned a response that cannot satisfy the evaluation contract."""
