# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).parents[3]
sys.path.insert(0, str(ROOT / "bundled" / "tool"))
sys.path.insert(0, str(ROOT / "bundled" / "libs"))

PROJECT = pathlib.Path(__file__).parent / "test_data" / "strictdoc_project"


@pytest.fixture(scope="session")
def model(tmp_path_factory):
    from trace_model import build_model

    result, errors = build_model(str(PROJECT), str(tmp_path_factory.mktemp("cache")))
    assert errors == []
    return result
