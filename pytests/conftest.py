import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture(scope="session")
def root():
    return ROOT


@pytest.fixture(scope="session")
def defective_db(root):
    path = os.path.join(root, "warehouse", "defective.duckdb")
    if not os.path.exists(path):
        pytest.fail("build the defective scenario first: make defective")
    return path
