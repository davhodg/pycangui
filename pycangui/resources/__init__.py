# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Data files shipped with pycangui (EDS samples, icons ...)."""

from importlib import resources
from pathlib import Path


def path(name: str) -> Path:
    return Path(str(resources.files(__name__) / name))
