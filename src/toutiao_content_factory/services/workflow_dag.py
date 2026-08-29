"""Workflow DAG — declarative definition of agent execution order.

Each step declares its dependencies and whether it's optional.
The executor resolves the DAG and runs steps in topological order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class WorkflowStep:
    """A single step in the workflow DAG."""
    name: str
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    optional: bool = False
    condition: str = ""  # Name of a method to call to check if this step should run

    def __repr__(self) -> str:
        deps = f", depends_on={self.depends_on}" if self.depends_on else ""
        opt = ", optional=True" if self.optional else ""
        cond = f', condition="{self.condition}"' if self.condition else ""
        return f"WorkflowStep({self.name}{deps}{opt}{cond})"


# Default workflow matching the current AgentWorkflowService.run() behavior
DEFAULT_WORKFLOW_STEPS: list[WorkflowStep] = [
    WorkflowStep(
        name="evolution",
        description="Update best practices rules from feedback",
        depends_on=[],
        optional=True,
    ),
    WorkflowStep(
        name="trend",
        description="Analyze industry trends and hot topics",
        depends_on=[],
    ),
    WorkflowStep(
        name="data",
        description="Extract and analyze data points",
        depends_on=["trend"],
    ),
    WorkflowStep(
        name="quality_gate",
        description="Abort if topic quality is too low",
        depends_on=["data"],
        condition="check_quality",
    ),
    WorkflowStep(
        name="write",
        description="Generate article content",
        depends_on=["trend", "data"],
    ),
    WorkflowStep(
        name="polish",
        description="Polish and improve article quality",
        depends_on=["write"],
    ),
    WorkflowStep(
        name="quality_review_council",
        description="Independent post-polish article quality audit",
        depends_on=["polish"],
        optional=True,
        condition="quality_review_enabled",
    ),
    WorkflowStep(
        name="title",
        description="Generate and optimize titles",
        depends_on=["polish"],
    ),
    WorkflowStep(
        name="review",
        description="Review article quality and compliance",
        depends_on=["title"],
    ),
    WorkflowStep(
        name="second_polish",
        description="Optional second polish pass based on review feedback",
        depends_on=["review"],
        optional=True,
        condition="needs_second_polish",
    ),
    WorkflowStep(
        name="publish_review",
        description="Final pre-publish review and readiness check",
        depends_on=["review"],
    ),
    WorkflowStep(
        name="bad_words_gate",
        description="Scan for sensitive/prohibited words",
        depends_on=["publish_review"],
        condition="check_bad_words",
    ),
]


def validate_workflow(steps: list[WorkflowStep]) -> list[str]:
    """Validate a workflow DAG for issues like cycles or missing dependencies.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []
    step_names = {step.name for step in steps}

    # Check for duplicate names
    seen: set[str] = set()
    for step in steps:
        if step.name in seen:
            errors.append(f"Duplicate step name: {step.name}")
        seen.add(step.name)

    # Check for missing dependencies
    for step in steps:
        for dep in step.depends_on:
            if dep not in step_names:
                errors.append(f"Step '{step.name}' depends on unknown step '{dep}'")

    # Check for cycles using DFS
    adj: dict[str, list[str]] = {step.name: list(step.depends_on) for step in steps}
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {name: WHITE for name in step_names}

    def dfs(node: str) -> bool:
        color[node] = GRAY
        for neighbor in adj.get(node, []):
            if color.get(neighbor) == GRAY:
                return True  # cycle found
            if color.get(neighbor) == WHITE and dfs(neighbor):
                return True
        color[node] = BLACK
        return False

    for name in step_names:
        if color[name] == WHITE:
            if dfs(name):
                errors.append("Workflow contains a cycle")
                break

    return errors


def topological_sort(steps: list[WorkflowStep]) -> list[WorkflowStep]:
    """Sort workflow steps in topological order based on dependencies.

    Uses Kahn's algorithm for deterministic ordering.
    """
    step_map = {step.name: step for step in steps}
    in_degree: dict[str, int] = {step.name: len(step.depends_on) for step in steps}

    # Build adjacency list (reverse: dependency -> dependents)
    dependents: dict[str, list[str]] = {step.name: [] for step in steps}
    for step in steps:
        for dep in step.depends_on:
            if dep in dependents:
                dependents[dep].append(step.name)

    # Start with steps that have no dependencies
    queue = sorted([name for name, degree in in_degree.items() if degree == 0])
    result: list[WorkflowStep] = []

    while queue:
        current = queue.pop(0)
        result.append(step_map[current])
        for dependent in sorted(dependents.get(current, [])):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)
                queue.sort()  # Keep deterministic order

    return result
