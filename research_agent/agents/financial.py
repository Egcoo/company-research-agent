"""Execute selected financial questions and check exact requested metric coverage."""

from research_agent.validation import missing_metrics as missing_metrics


def methods() -> str:
    """Read authoritative financial research methods from the selected Skill."""
    from research_agent.skill_loader import load_skill

    return load_skill(task_type="financial").methodology
