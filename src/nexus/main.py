from __future__ import annotations

from .storage import load_projects


def main() -> None:
    """Simple boot script to list projects."""
    projects = load_projects()
    print("Nexus boot")
    print(f"Projects: {len(projects)}")
    for project in projects:
        print(f"- {project.name} | {project.path} | {project.ide}")


if __name__ == "__main__":
    main()
