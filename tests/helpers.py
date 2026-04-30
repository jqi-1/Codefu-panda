import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def workspace(prefix="case"):
    parent = Path.cwd() / ".test_workspaces"
    parent.mkdir(exist_ok=True)
    root = parent / f"{prefix}_{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)
