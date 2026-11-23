"""Shared test setup.

Servers built from a configuration file can write that file (config.update). Tests must only ever give them
temporary copies (`tmp_config`), never the examples in the repo; `examples_unchanged` fails the run if one changed.
"""
import glob
import hashlib
import os
import shutil
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = sorted(glob.glob(os.path.join(ROOT, "config", "examples", "*.yaml"))
                  + glob.glob(os.path.join(ROOT, "docs", "examples", "*.yaml")))


def tmp_config(path: str) -> str:
    """A copy of a configuration file in a new temporary directory."""
    target = os.path.join(tempfile.mkdtemp(prefix="tinkerpnp-test-"), os.path.basename(path))
    shutil.copy(path, target)
    return target


def _digests() -> dict[str, str]:
    return {p: hashlib.sha256(open(p, "rb").read()).hexdigest() for p in EXAMPLES}


@pytest.fixture(scope="session", autouse=True)
def examples_unchanged():
    before = _digests()
    yield
    changed = [os.path.relpath(p, ROOT) for p, d in _digests().items() if before.get(p) != d]
    assert not changed, f"tests changed example configurations in the repo: {changed}"
