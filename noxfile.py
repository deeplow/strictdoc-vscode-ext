# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
"""Build tasks, run with `uvx nox -s <session>`."""

import nox  # pylint: disable=import-error

nox.options.default_venv_backend = "none"


@nox.session()
def setup(session: nox.Session) -> None:
    """Syncs the dev environment and vendors bundled libs into bundled/libs."""
    session.run("uv", "sync", external=True)
    session.run(
        "bash",
        "-c",
        "uv export --only-group bundled --no-hashes --no-emit-project"
        " | uv pip install --target bundled/libs --no-deps -r -",
        external=True,
    )


@nox.session()
def tests(session: nox.Session) -> None:
    """Runs all the Python tests for the extension."""
    session.run("uv", "run", "pytest", "src/test/python_tests", external=True)


@nox.session()
def build_package(session: nox.Session) -> None:
    """Builds VSIX package for publishing."""
    setup(session)
    session.run("npm", "install", external=True)
    session.run("npm", "run", "vsce-package", external=True)
