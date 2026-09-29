# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
"""Builds a plain-data traceability model using StrictDoc's Python API."""

from __future__ import annotations

import contextlib
import glob
import io
import os
import re
from dataclasses import dataclass, field

from strictdoc.backend.sdoc.errors.document_tree_error import DocumentTreeError
from strictdoc.backend.sdoc.models.document import SDocDocument
from strictdoc.backend.sdoc.models.node import SDocNode
from strictdoc.core.project_config import (
    ProjectConfig,
    ProjectConfigLoader,
    ProjectFeature,
)
from strictdoc.core.traceability_index_builder import TraceabilityIndexBuilder
from strictdoc.helpers.exception import StrictDocException
from strictdoc.helpers.parallelizer import NullParallelizer

# Importing the export formats pulls in html2pdf4doc, which replaces
# sys.stdout on import. Do it once now, before any stdout redirection.
ProjectConfig.default_formats()


@dataclass
class CodeLink:
    uid: str
    path: str
    begin: int  # 0-based, inclusive
    end: int  # 0-based, inclusive
    marker_line: int | None  # None for forward links (no marker in code)
    description: str
    forward: bool
    role: str | None = (
        None  # StrictDoc relation role, e.g. "Implementation", "Verification"
    )
    kind: str = "code"  # "code" or "test"
    rel_path: str = ""  # relative to the source root, as StrictDoc keys it


@dataclass
class Requirement:
    uid: str
    title: str
    statement: str
    doc_title: str
    sdoc_path: str
    uid_line: int
    parents: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    code: list[CodeLink] = field(default_factory=list)
    depth: int = 0  # longest chain of parents: 0 for top-level requirements
    # StrictDoc tree-map coverage: requirements below that still need source / tests
    # (0 = covered). See _set_coverage.
    missing_source: int = 0
    missing_tests: int = 0
    section: list[str] = field(
        default_factory=list
    )  # enclosing [[SECTION]] titles, outermost first
    order: int = 0  # position in document order across the project


@dataclass
class SourceFile:
    path: str
    links: list[CodeLink] = field(default_factory=list)


@dataclass
class Doc:
    title: str
    path: str
    uids: list[str] = field(default_factory=list)


@dataclass
class BuildError:
    path: str | None
    line: int  # 0-based
    message: str


@dataclass
class TraceModel:
    project_dir: str
    requirements: dict[str, Requirement] = field(default_factory=dict)
    docs: list[Doc] = field(default_factory=list)
    files: dict[str, SourceFile] = field(default_factory=dict)


def build_model(
    project_dir: str, cache_dir: str
) -> tuple[TraceModel | None, list[BuildError]]:
    """Runs a full StrictDoc build and converts the result into a TraceModel."""
    project_dir = os.path.abspath(project_dir)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            index, config = _build_index(project_dir, cache_dir)
            return _convert(index, config.source_root_path, project_dir), []
    except SystemExit:
        return None, [
            _to_error(project_dir, out.getvalue().strip() or "strictdoc exited")
        ]
    except (StrictDocException, DocumentTreeError) as e:
        return None, [_to_error(project_dir, _message(e), e)]
    except Exception as e:  # pylint: disable=broad-except
        return None, [_to_error(project_dir, f"{type(e).__name__}: {e}", e)]


VENV_PATTERNS = [".venv/**", "venv/**"]


def _build_index(project_dir: str, cache_dir: str):
    config = ProjectConfigLoader.load(project_dir)
    if config.source_root_path is None:
        config.source_root_path = project_dir
    # Never index virtualenvs inside the project (e.g. created by `uv run`).
    config.exclude_doc_paths += VENV_PATTERNS
    config.exclude_source_paths += VENV_PATTERNS
    config.dir_for_sdoc_cache = os.path.join(cache_dir, "sdoc_cache")
    config.export_output_html_root = os.path.join(cache_dir, "html")
    if ProjectFeature.REQUIREMENT_TO_SOURCE_TRACEABILITY not in config.project_features:
        config.project_features.append(
            ProjectFeature.REQUIREMENT_TO_SOURCE_TRACEABILITY
        )
    index = TraceabilityIndexBuilder.create(
        project_config=config, parallelizer=NullParallelizer()
    )
    return index, config


def _convert(index, source_root: str, project_dir: str) -> TraceModel:
    model = TraceModel(project_dir=project_dir)
    for document in index.document_tree.document_list:
        sdoc_path = document.meta.input_doc_full_path
        doc = Doc(title=document.reserved_title or "", path=sdoc_path)
        model.docs.append(doc)
        lines = _read_lines(sdoc_path)
        iterator = index.get_document_iterator(document)
        for node, _ in iterator.all_content(print_fragments=False):
            if not isinstance(node, SDocNode) or not node.reserved_uid:
                continue
            req = Requirement(
                uid=node.reserved_uid,
                title=node.reserved_title or "",
                statement=node.reserved_statement or "",
                doc_title=doc.title,
                sdoc_path=sdoc_path,
                uid_line=_uid_line(lines, node.ng_line_start),
                parents=[
                    p.reserved_uid
                    for p, _ in index.get_parent_relations_with_roles(node)
                ],
                children=[
                    c.reserved_uid
                    for c, _ in index.get_child_relations_with_roles(node)
                ],
                section=_sections(node),
                order=len(model.requirements),
            )
            for rel_path, markers in index.get_requirement_file_links(node):
                path = os.path.join(source_root, rel_path)
                for m in markers:
                    if m.is_begin():
                        req.code.append(_code_link(req.uid, path, rel_path, m))
            for link in req.code:
                model.files.setdefault(
                    link.path, SourceFile(path=link.path)
                ).links.append(link)
            model.requirements[req.uid] = req
            doc.uids.append(req.uid)
    _set_depths(model.requirements)
    _set_coverage(model.requirements)
    return model


def _set_coverage(requirements: dict[str, Requirement]) -> None:
    """Requirements coverage with source / tests, as StrictDoc's tree map computes it.

    A requirement is covered with source if it has a file link whose path does not
    contain "tests/", or it has children and all of them are covered (tests: the
    path contains "tests/"). Missing = the uncovered leaf requirements below it.
    """
    for kind, attr in (("source", "missing_source"), ("tests", "missing_tests")):
        gaps: dict[str, frozenset] = {}

        def gaps_of(uid: str) -> frozenset:
            if uid not in gaps:
                req = requirements[uid]
                children = [c for c in req.children if c in requirements]
                linked = any(
                    ("tests/" in l.rel_path) == (kind == "tests") for l in req.code
                )
                if linked:
                    gaps[uid] = frozenset()
                elif not children:
                    gaps[uid] = frozenset([uid])
                else:
                    gaps[uid] = frozenset().union(*map(gaps_of, children))
            return gaps[uid]

        for uid, req in requirements.items():
            setattr(req, attr, len(gaps_of(uid)))


def _set_depths(requirements: dict[str, Requirement]) -> None:
    """Depth = longest parent chain; StrictDoc rejects cycles, so this terminates."""
    depths: dict[str, int] = {}

    def depth(uid: str) -> int:
        if uid not in depths:
            parents = [p for p in requirements[uid].parents if p in requirements]
            depths[uid] = 1 + max(map(depth, parents), default=-1)
        return depths[uid]

    for uid, req in requirements.items():
        req.depth = depth(uid)


def _sections(node) -> list[str]:
    """Titles of the [[SECTION]]s enclosing a node, outermost first (StrictDoc's tree)."""
    titles = []
    parent = getattr(node, "parent", None)
    # Stop at the document (its node_type asserts on top-level documents).
    while parent is not None and not isinstance(parent, SDocDocument):
        if parent.node_type == "SECTION":
            titles.append(parent.reserved_title or "")
        parent = getattr(parent, "parent", None)
    return titles[::-1]


def _code_link(uid: str, path: str, rel_path: str, marker) -> CodeLink:
    forward = marker.is_forward()
    return CodeLink(
        uid=uid,
        path=path,
        begin=marker.ng_range_line_begin - 1,
        end=marker.ng_range_line_end - 1,
        marker_line=None if forward else marker.ng_source_line_begin - 1,
        description=marker.get_description() or "",
        forward=forward,
        role=marker.role,
        kind=_link_kind(marker.role, rel_path),
        rel_path=rel_path,
    )


TEST_ROLES = {"verification", "test", "tests", "testing", "validation"}
_TEST_PATH = re.compile(
    r"(^|/)tests?/|(^|/)test_[^/]*$|_test\.[^/]*$|\.(test|spec)\.[^/]*$"
)


def _link_kind(role: str | None, path: str) -> str:
    """A link is a test by its role; without a role, by a test-like path (relative to the source root)."""
    if role:
        return "test" if role.lower() in TEST_ROLES else "code"
    return "test" if _TEST_PATH.search(path.replace(os.sep, "/")) else "code"


def _read_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


def _uid_line(lines: list[str], line_start: int | None) -> int:
    """Returns the 0-based line of the node's `UID:` field."""
    start = max((line_start or 1) - 1, 0)
    for i in range(start, len(lines)):
        if lines[i].startswith("UID:"):
            return i
    return start


def _message(e: Exception) -> str:
    if hasattr(e, "to_print_message"):
        return e.to_print_message()
    return str(e)


_LOCATION = re.compile(r"([^\s:\"']+\.\w+)(?::(\d+))?")
_MISSING_UID = re.compile(r"does ?n[o']t exist: ([^\s,]+?)\.?$", re.MULTILINE)


def _to_error(project_dir: str, message: str, e: Exception | None = None) -> BuildError:
    """Locates an error from its attributes or from `path:line` in its text."""
    start = message.find("error:")
    message = message[start:] if start > 0 else message
    path, line = getattr(e, "file_path", None), getattr(e, "line", None)
    if path is None:
        matches = _LOCATION.finditer(message)
        # Prefer `path:line` over a bare path.
        for match in sorted(matches, key=lambda m: m.group(2) is None):
            if os.path.isfile(os.path.join(project_dir, match.group(1))):
                path = os.path.join(project_dir, match.group(1))
                line = match.group(2) and int(match.group(2))
                break
    if line is None:
        # Errors about an unknown UID carry no line: find where it is used.
        missing = _MISSING_UID.search(message)
        if missing:
            path, line = _find_text(project_dir, path, missing.group(1))
    return BuildError(path=path, line=max((line or 1) - 1, 0), message=message)


def _find_text(project_dir: str, path: str | None, text: str):
    """Returns (path, 1-based line) of the first occurrence of text."""
    paths = [path] if path else glob.glob(f"{project_dir}/**/*.sdoc", recursive=True)
    for candidate in paths:
        for i, line in enumerate(_read_lines(candidate)):
            if text in line:
                return candidate, i + 1
    return path, None
