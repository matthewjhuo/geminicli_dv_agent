"""Framework exception hierarchy."""

from __future__ import annotations


class DVAgentError(Exception):
    """Base class for all framework errors."""


class ConfigError(DVAgentError):
    pass


class ApprovalRequired(DVAgentError):
    """Raised when a gated action was not explicitly approved by a human."""

    def __init__(self, message: str, request=None):
        super().__init__(message)
        self.request = request


class ForbiddenOperation(DVAgentError):
    """Raised for operations that are never allowed (e.g. Perforce submit)."""


class ConfidentialityError(DVAgentError):
    """Raised when data would leave the organization without authorization."""


class BudgetExceeded(DVAgentError):
    pass


class ContractViolation(DVAgentError):
    def __init__(self, message: str, errors=None):
        super().__init__(message)
        self.errors = list(errors or [])


class TaskTimeout(DVAgentError):
    pass


class BackendError(DVAgentError):
    pass


class AdapterNotValidated(DVAgentError):
    """Real-tool mode requested for an adapter not yet validated in this environment."""


class UnsupportedOperation(DVAgentError):
    """The requested real integration is not implemented; never report mock success."""


class ProvenanceError(DVAgentError):
    pass


class PromotionError(DVAgentError):
    pass


class GraphError(DVAgentError):
    pass
