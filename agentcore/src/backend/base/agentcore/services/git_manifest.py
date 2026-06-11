"""Push agents.yaml to a Git repository — git sync via PAT tokens has been removed."""


def read_manifest_from_git() -> dict:
    """Git sync is disabled — token-based GitHub/ADO push has been removed."""
    return {}


def push_manifest_to_git(data: dict, commit_message: str) -> None:
    """Git sync is disabled — token-based GitHub/ADO push has been removed."""
    return
