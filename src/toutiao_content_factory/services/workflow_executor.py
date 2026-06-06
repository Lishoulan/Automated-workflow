"""Workflow Executor — runs workflow steps in DAG order.

Integrates with the existing AgentWorkflowService by providing
a declarative way to define and execute the agent pipeline.
"""
from __future__ import annotations

import logging
from typing import Any

from toutiao_content_factory.services.workflow_dag import (
    WorkflowStep,
    DEFAULT_WORKFLOW_STEPS,
    topological_sort,
    validate_workflow,
)


logger = logging.getLogger(__name__)


class WorkflowExecutor:
    """Executes workflow steps in topological order.

    Each step is mapped to a method on the workflow service.
    Optional steps that fail are skipped with a warning.
    Conditional steps are only executed if their condition method returns True.
    """

    def __init__(
        self,
        workflow_service: Any,
        steps: list[WorkflowStep] | None = None,
    ) -> None:
        self.service = workflow_service
        self.steps = steps or DEFAULT_WORKFLOW_STEPS
        self._completed: set[str] = set()
        self._skipped: set[str] = set()
        self._failed: set[str] = set()
        self._results: dict[str, Any] = {}

    @property
    def completed_steps(self) -> set[str]:
        return set(self._completed)

    @property
    def skipped_steps(self) -> set[str]:
        return set(self._skipped)

    @property
    def failed_steps(self) -> set[str]:
        return set(self._failed)

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """Execute all workflow steps in topological order.

        Args:
            context: Initial context dict with extraction, style, cache_context, etc.

        Returns:
            Updated context dict with results from all steps.
        """
        errors = validate_workflow(self.steps)
        if errors:
            raise ValueError(f"Invalid workflow: {'; '.join(errors)}")

        sorted_steps = topological_sort(self.steps)
        logger.info(
            "Workflow execution order: %s",
            " -> ".join(step.name for step in sorted_steps),
        )

        for step in sorted_steps:
            # Check if all dependencies completed successfully
            deps_ok = all(dep in self._completed for dep in step.depends_on)
            if not deps_ok:
                missing = [dep for dep in step.depends_on if dep not in self._completed]
                if step.optional:
                    logger.info("Skipping optional step '%s': missing deps %s", step.name, missing)
                    self._skipped.add(step.name)
                    continue
                else:
                    logger.warning("Skipping step '%s': missing deps %s", step.name, missing)
                    self._skipped.add(step.name)
                    continue

            # Check condition if specified
            if step.condition and not self._check_condition(step.condition, context):
                logger.info("Skipping step '%s': condition '%s' not met", step.name, step.condition)
                self._skipped.add(step.name)
                continue

            # Execute the step
            try:
                result = self._execute_step(step, context)
                self._completed.add(step.name)
                self._results[step.name] = result
                context[step.name] = result
            except Exception as exc:
                self._failed.add(step.name)
                if step.optional:
                    logger.warning("Optional step '%s' failed, continuing: %s", step.name, exc)
                    self._skipped.add(step.name)
                else:
                    raise

        return context

    def _execute_step(self, step: WorkflowStep, context: dict[str, Any]) -> Any:
        """Execute a single workflow step by calling the corresponding method."""
        method_name = f"_step_{step.name}"
        method = getattr(self, method_name, None)
        if method is None:
            method = getattr(self.service, step.name, None)
        if method is None:
            raise ValueError(f"No method found for step '{step.name}'")
        return method(context)

    def _check_condition(self, condition_name: str, context: dict[str, Any]) -> bool:
        """Check a named condition on the workflow service."""
        method = getattr(self.service, condition_name, None)
        if method is None:
            method = getattr(self, condition_name, None)
        if method is None:
            logger.warning("Condition method '%s' not found, defaulting to True", condition_name)
            return True
        return bool(method(context))
