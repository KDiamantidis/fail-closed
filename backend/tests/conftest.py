import pytest

from app.config import Settings


def _docker_ready() -> str | None:
    """Return None if Docker and the sandbox image are available, else the reason to skip."""
    try:
        import docker

        client = docker.from_env()
        client.ping()
        client.images.get(Settings().sandbox_image)
        return None
    except Exception as e:  # noqa: BLE001
        return f"Docker or sandbox image not available ({type(e).__name__})"


def pytest_collection_modifyitems(config, items):
    docker_items = [i for i in items if "docker" in i.keywords]
    if not docker_items:
        return
    reason = _docker_ready()
    if reason:
        for item in docker_items:
            item.add_marker(pytest.mark.skip(reason=reason))
