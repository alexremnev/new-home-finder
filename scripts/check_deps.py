import importlib.metadata as md
import pathlib
import re
import sys

LOCK = pathlib.Path(__file__).resolve().parent.parent / "uv.lock"
RUNTIME = ("telethon", "psycopg", "psycopg-binary", "pydantic", "python-dateutil")

def norm(name: str) -> str:
    return name.lower().replace("_", "-")

def locked_versions(text: str) -> dict[str, str]:
    return {
        norm(name): version
        for name, version in re.findall(
            r'\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"', text
        )
    }

def installed_versions() -> dict[str, str]:
    found: dict[str, str] = {}
    for dist in md.distributions():
        name = dist.metadata["Name"] if dist.metadata else None
        if name:
            found[norm(name)] = dist.version
    return found

def main() -> int:
    text = LOCK.read_text()
    locked = locked_versions(text)
    blocks = text.count("[[package]]")
    if len(locked) != blocks:
        print(f"! parsed {len(locked)} of {blocks} lock entries — lock format changed")
        return 2

    installed = installed_versions()
    print(f"lock {len(locked)} packages   venv {len(installed)} installed   "
          f"python {sys.version.split()[0]}")
    print()
    for name in RUNTIME:
        print(f"  {name:<16} installed {installed.get(name, '-'):<12} "
              f"locked {locked.get(name, '-')}")

    problems = []
    for name, version in sorted(installed.items()):
        want = locked.get(name)
        if want is None:
            problems.append(f"installed but absent from the lock: {name} {version}")
        elif want != version:
            problems.append(f"version differs: {name} installed {version}, locked {want}")

    print()
    if problems:
        print("PROBLEMS")
        for line in problems:
            print(f"  ! {line}")
        return 1
    print(f"Every one of the {len(installed)} installed packages matches the lock.")
    absent = sorted(set(locked) - set(installed))
    if absent:
        print(f"Not installed, as expected with --no-dev: {', '.join(absent)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
