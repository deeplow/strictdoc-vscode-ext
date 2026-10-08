# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
"""Editor features computed from a TraceModel. Pure functions, 0-based lines."""

from __future__ import annotations

import os
import pathlib
import re

from lsprotocol import types as lsp
from trace_model import BuildError, CodeLink, Requirement, TraceModel

RELATION = re.compile(r"@relation\(([^)]*)\)?")
SDOC_FIELD = re.compile(r"^\s*(UID|VALUE|PATH):\s*(\S+)")
RELATION_TYPE = re.compile(r"^\s*- TYPE:\s*(\w+)")


def _uri(path: str) -> str:
    return pathlib.Path(path).as_uri()


def _is_sdoc(path: str) -> bool:
    return path.endswith(".sdoc")


def _get_line(text: str, line: int) -> str:
    lines = text.splitlines()
    return lines[line] if 0 <= line < len(lines) else ""


def _range(begin: int, end: int, start_char: int = 0, end_char: int = 0):
    return lsp.Range(
        start=lsp.Position(line=begin, character=start_char),
        end=lsp.Position(line=end, character=end_char),
    )


def _req_location(req: Requirement) -> lsp.Location:
    return lsp.Location(
        uri=_uri(req.sdoc_path), range=_range(req.uid_line, req.uid_line)
    )


def _code_location(link: CodeLink) -> lsp.Location:
    return lsp.Location(uri=_uri(link.path), range=_range(link.begin, link.end + 1))


def marker_uids(line_text: str) -> list[tuple[str, int, int]]:
    """Returns (uid, start, end) for each UID inside `@relation(...)`."""
    result = []
    for match in RELATION.finditer(line_text):
        offset = match.start(1)
        for arg in match.group(1).split(","):
            uid = arg.strip()
            if uid and "=" not in uid:
                start = offset + arg.index(uid)
                result.append((uid, start, start + len(uid)))
            offset += len(arg) + 1
    return result


def _token(text: str, path: str, line: int, character: int):
    """Returns (kind, value) under the cursor.

    kind is "uid" (a marker UID in code, or the `UID:` of a requirement),
    "ref" (a Parent/Child `VALUE:` in .sdoc) or "file" (a File relation path).
    """
    line_text = _get_line(text, line)
    if not _is_sdoc(path):
        for uid, start, end in marker_uids(line_text):
            if start <= character <= end:
                return "uid", uid
        return None, None
    match = SDOC_FIELD.match(line_text)
    if not match:
        return None, None
    field, value = match.groups()
    if field == "UID":
        return "uid", value
    if field == "PATH":
        return "file", value
    lines = text.splitlines()
    for i in range(line, -1, -1):
        type_match = RELATION_TYPE.match(lines[i])
        if type_match:
            return ("file" if type_match.group(1) == "File" else "ref"), value
    return "ref", value


def _requirement_at(model: TraceModel, path: str, line: int) -> Requirement | None:
    """The requirement whose block contains `line` in an .sdoc file."""
    # `line + 1` so the `[REQUIREMENT]` header just above `UID:` counts too.
    candidates = [
        r
        for r in model.requirements.values()
        if r.sdoc_path == path and r.uid_line <= line + 1
    ]
    return max(candidates, key=lambda r: r.uid_line, default=None)


def _file_path(model: TraceModel, value: str) -> str:
    for path in model.files:
        if path.endswith(os.sep + os.path.normpath(value)):
            return path
    return os.path.join(model.project_dir, value)


def _covering_links(model: TraceModel, path: str, line: int) -> list[CodeLink]:
    """Links covering a code line, innermost first."""
    source = model.files.get(path)
    links = [l for l in source.links if l.begin <= line <= l.end] if source else []
    return sorted(links, key=lambda l: l.end - l.begin)


def _subtree(model: TraceModel, uid: str) -> set[str]:
    """The requirement and all its descendants, each once."""
    seen, stack = set(), [uid]
    while stack:
        u = stack.pop()
        if u in seen or u not in model.requirements:
            continue
        seen.add(u)
        stack += model.requirements[u].children
    return seen


def _has_code(model: TraceModel, uid: str) -> bool:
    """True when the requirement or any of its descendants has code links."""
    return any(model.requirements[u].code for u in _subtree(model, uid))


def hover(model, path, text, line, character) -> lsp.Hover | None:
    kind, value = _token(text, path, line, character)
    req = model.requirements.get(value) if kind in ("uid", "ref") else None
    if req is None:
        return None
    parts = [
        f"**{req.title}** · `{req.uid}`",
        req.statement,
        f"*Document:* {req.doc_title}",
    ]
    if req.parents:
        parts.append("*Parents:* " + ", ".join(req.parents))
    if req.children:
        parts.append("*Children:* " + ", ".join(req.children))
    code, tests = _split(req.code)
    parts.append(f"*Code links:* {len(code)} · *Tests:* {len(tests)}")
    return lsp.Hover(
        contents=lsp.MarkupContent(
            kind=lsp.MarkupKind.Markdown, value="\n\n".join(parts)
        )
    )


def definition(model, path, text, line, character) -> list[lsp.Location] | None:
    kind, value = _token(text, path, line, character)
    if kind == "file":
        file_path = _file_path(model, value)
        req = _requirement_at(model, path, line)
        links = [l for l in req.code if l.path == file_path] if req else []
        return [_code_location(l) for l in links] or [
            lsp.Location(uri=_uri(file_path), range=_range(0, 0))
        ]
    req = model.requirements.get(value)
    if req is None:
        return None
    if kind == "uid" and _is_sdoc(path):
        return [_code_location(l) for l in req.code] or None
    return [_req_location(req)]


def references(model, path, text, line, character) -> list[lsp.Location] | None:
    kind, value = _token(text, path, line, character)
    req = model.requirements.get(value) if kind in ("uid", "ref") else None
    if req is None:
        return None
    children = [model.requirements[c] for c in req.children if c in model.requirements]
    return [_code_location(l) for l in req.code] + [_req_location(c) for c in children]


def _command(title: str, command: str, *arguments) -> lsp.Command:
    return lsp.Command(title=title, command=command, arguments=list(arguments))


def code_lenses(model, path, text) -> list[lsp.CodeLens]:
    lenses = []
    if _is_sdoc(path):
        for req in model.requirements.values():
            if req.sdoc_path != path:
                continue
            trace = f"↑{len(req.parents)} parents · ↓{len(req.children)} children"
            commands = [
                _command(trace, "strictdoc.focusRequirement", req.uid),
                _command(_link_counts(req), "strictdoc.goToCode", req.uid),
            ]
            lenses += [
                lsp.CodeLens(range=_range(req.uid_line, req.uid_line), command=c)
                for c in commands
            ]
        return lenses
    source = model.files.get(path)
    for link in source.links if source else []:
        req = model.requirements[link.uid]
        if link.kind == "test":
            title = f"✓ verifies {req.title} · {req.uid}"
        elif link.forward:
            title = (
                f"traced by {req.title} · {req.uid} ({os.path.basename(req.sdoc_path)})"
            )
        else:
            title = f"{req.title} · {req.uid}"
        command = lsp.Command(
            title=title,
            command="strictdoc.openLocation",
            arguments=[_uri(req.sdoc_path), req.uid_line],
        )
        lenses.append(
            lsp.CodeLens(range=_range(link.begin, link.begin), command=command)
        )
    return lenses


def document_links(model, path, text) -> list[lsp.DocumentLink]:
    links = []
    if _is_sdoc(path):
        return links
    for i, line_text in enumerate(text.splitlines()):
        for uid, start, end in marker_uids(line_text):
            req = model.requirements.get(uid)
            if req:
                links.append(
                    lsp.DocumentLink(
                        range=_range(i, i, start, end),
                        target=f"{_uri(req.sdoc_path)}#L{req.uid_line + 1}",
                        tooltip=req.title,
                    )
                )
    return links


def completions(model, path, text, line, character) -> list[lsp.CompletionItem]:
    prefix = _get_line(text, line)[:character]
    if _is_sdoc(path):
        wanted = re.match(r"^\s*VALUE:", prefix) is not None
    else:
        start = prefix.rfind("@relation(")
        wanted = start >= 0 and ")" not in prefix[start:]
    if not wanted:
        return []
    return [
        lsp.CompletionItem(
            label=r.uid, detail=r.title, kind=lsp.CompletionItemKind.Reference
        )
        for r in model.requirements.values()
    ]


def diagnostics(model, errors: list[BuildError]) -> dict[str, list[lsp.Diagnostic]]:
    """Build errors grouped by file. Errors without a file are left out."""
    result: dict[str, list[lsp.Diagnostic]] = {}
    for error in errors:
        if error.path:
            result.setdefault(error.path, []).append(
                lsp.Diagnostic(
                    range=_range(error.line, error.line + 1),
                    message=error.message,
                    severity=lsp.DiagnosticSeverity.Error,
                    source="strictdoc",
                )
            )
    return result


def _req_dict(req: Requirement) -> dict:
    return {
        "uid": req.uid,
        "title": req.title,
        "uri": _uri(req.sdoc_path),
        "line": req.uid_line,
    }


def _code_dict(link: CodeLink) -> dict:
    return {
        "uid": link.uid,
        "uri": _uri(link.path),
        "begin": link.begin,
        "end": link.end,
        "description": link.description,
        "forward": link.forward,
        "kind": link.kind,
        "role": link.role,
    }


def _split(links: list[CodeLink]) -> tuple[list[CodeLink], list[CodeLink]]:
    """(code links, test links)."""
    return [l for l in links if l.kind != "test"], [
        l for l in links if l.kind == "test"
    ]


def _link_counts(req: Requirement) -> str:
    code, tests = _split(req.code)
    return f"⟨⟩{len(code)} code · ✓{len(tests)} tests"


def locate(model, path, line) -> dict:
    """Requirements covering a code line, or the requirement at an .sdoc line."""
    if _is_sdoc(path):
        req = _requirement_at(model, path, line)
        reqs = [req] if req else []
        links = req.code if req else []
    else:
        links = _covering_links(model, path, line)
        uids = list(dict.fromkeys(l.uid for l in links))
        reqs = [model.requirements[u] for u in uids]
    return {
        "requirements": [_req_dict(r) for r in reqs],
        "code": [_code_dict(l) for l in links],
    }


def _walk(model, start: list[str], attr: str, depth: int) -> list[str]:
    """BFS along parents or children; depth -1 means unlimited."""
    seen, frontier, result = set(start), list(start), []
    level = 0
    while frontier and (depth < 0 or level < depth):
        level += 1
        next_frontier = []
        for uid in frontier:
            for other in getattr(model.requirements[uid], attr):
                if other not in seen and other in model.requirements:
                    seen.add(other)
                    next_frontier.append(other)
        result.append(next_frontier)
        frontier = next_frontier
    return result


def graph(model, uid, path, line, up, down, include_code) -> dict:
    """Lineage subgraph around the focus: ancestors, focus, descendants."""
    if uid:
        focus = [uid] if uid in model.requirements else []
    else:
        focus = [r["uid"] for r in locate(model, path, line)["requirements"]]
    ancestors = _walk(model, focus, "parents", up)
    descendants = _walk(model, focus, "children", down)
    ordered = [u for level in reversed(ancestors) for u in level] + focus
    ordered += [u for level in descendants for u in level]
    ordered = list(dict.fromkeys(ordered))
    included = set(ordered)

    nodes, edges, code = [], [], []
    for node_uid in ordered:
        req = model.requirements[node_uid]
        nodes.append(_node_dict(model, req))
        edges += [[node_uid, c] for c in req.children if c in included]
        if include_code:
            code += [_code_dict(l) for l in req.code]
    return {
        "focus": focus,
        "nodes": nodes,
        "edges": edges,
        "code": code,
        "rootCount": len(roots(model)["nodes"]),
    }


def roots(model) -> dict:
    """Top-level requirements (no parents), in document order."""
    top = [r for r in model.requirements.values() if not r.parents]
    return {
        "nodes": [_node_dict(model, r) for r in top],
        "project": _coverage(model, model.requirements),
    }


def _node_dict(model, req: Requirement) -> dict:
    """A requirement for the Trace Graph. Link pills count the whole subtree
    (the requirement and its descendants); `own*` count its own links only."""
    own_code, own_tests = _split(req.code)
    subtree = _subtree(model, req.uid)
    links = [l for u in subtree for l in model.requirements[u].code]
    code, tests = _split(links)
    badges = []
    for kind, total, own, icon, noun, missing, what in (
        ("code", code, own_code, "⟨⟩", "code links", req.missing_source, "source"),
        ("test", tests, own_tests, "✓", "tests", req.missing_tests, "tests"),
    ):
        if total:
            title = f"{len(total)} {noun} incl. sub-requirements ({len(own)} own)"
        else:
            title = f"No {noun} for this requirement or its sub-requirements"
        badges.append(
            {
                "kind": kind,
                "text": f"{icon} {len(total)}",
                "title": title,
                "zero": not total,
                # StrictDoc tree-map coverage: requirements below still without it.
                "missing": missing,
                "missingTitle": f"{missing} requirement{'s' if missing != 1 else ''}"
                f" below {'have' if missing != 1 else 'has'} no {what}"
                " (StrictDoc tree-map coverage)",
            }
        )
    return {
        **_req_dict(req),
        "doc": req.doc_title,
        "depth": req.depth,
        "layerTitle": req.doc_title,
        "statement": req.statement[:300],
        "badges": badges,
        "parentCount": len(req.parents),
        "childCount": len(req.children),
        # Enclosing [[SECTION]] titles (outermost first) within document `docKey`, and
        # the position in file order: the Trace Graph groups rows by section.
        "sections": req.section,
        "docKey": req.sdoc_path,
        "order": req.order,
        "totalCode": len(code),
        "totalTests": len(tests),
        "coverage": _coverage(model, subtree),
    }


def _coverage(model, uids) -> dict:
    """Requirements coverage with source / tests over `uids` (StrictDoc tree map)."""
    reqs = [model.requirements[u] for u in uids]
    return {
        "requirements": len(reqs),
        "withSource": sum(r.missing_source == 0 for r in reqs),
        "withTests": sum(r.missing_tests == 0 for r in reqs),
    }


def requirements_tree(model) -> dict:
    return {
        "docs": [
            {
                "title": doc.title,
                "path": doc.path,
                "requirements": [
                    {
                        "uid": uid,
                        "title": model.requirements[uid].title,
                        "depth": model.requirements[uid].depth,
                        "untraced": not _has_code(model, uid),
                    }
                    for uid in doc.uids
                ],
            }
            for doc in model.docs
        ]
    }
