import os
import sys
from pathlib import Path
from typing import Dict

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--use-docker-env",
        action="store_true",
        default=False,
        help="Load environment variables from docker.env before running tests.",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--use-docker-env"):
        _load_env_file(config.rootpath / "docker.env")


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        raise pytest.UsageError(
            f"--use-docker-env was provided but {env_path} does not exist"
        )

    for key, value in _parse_env(env_path).items():
        os.environ.setdefault(key, value)


def _parse_env(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            data[key] = value
    return data
