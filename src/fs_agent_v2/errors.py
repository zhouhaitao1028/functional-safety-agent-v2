class FunctionalSafetyError(Exception):
    """Base error for domain and orchestration failures."""


class ContractError(FunctionalSafetyError):
    """Structured artifact does not satisfy its contract."""


class GateError(FunctionalSafetyError):
    """A human approval or stage dependency is missing."""


class KnowledgeError(FunctionalSafetyError):
    """Knowledge entry is missing or malformed."""


class ModelError(FunctionalSafetyError):
    """A model provider call or tool-calling response is invalid."""

