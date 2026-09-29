# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
"""Tests for building the traceability model with StrictDoc."""

import shutil

from trace_model import build_model

from .conftest import PROJECT

APP = str(PROJECT / "src" / "app.py")
LIB = str(PROJECT / "src" / "lib.c")


def _link(model, uid, path, description):
    return next(
        l
        for l in model.requirements[uid].code
        if l.path == path and l.description == description
    )


def test_requirements_and_relations(model):
    assert list(model.requirements) == ["REQ-1", "REQ-2", "REQ-3"]
    req1 = model.requirements["REQ-1"]
    assert req1.title == "System shall do X"
    assert req1.sdoc_path == str(PROJECT / "reqs.sdoc")
    assert req1.uid_line == 4
    assert req1.children == ["REQ-2", "REQ-3"]
    assert model.requirements["REQ-2"].parents == ["REQ-1"]
    assert [d.uids for d in model.docs] == [["REQ-1", "REQ-2", "REQ-3"]]


def test_marker_links(model):
    file_link = _link(model, "REQ-1", APP, "entire file")
    assert (file_link.begin, file_link.end, file_link.marker_line) == (0, 13, 1)
    function = _link(model, "REQ-1", APP, "function do_x()")
    assert (function.begin, function.end, function.marker_line) == (5, 9, 7)
    assert not function.forward
    code_range = [
        l
        for l in model.requirements["REQ-2"].code
        if not l.forward and l.kind == "code"
    ]
    assert [(l.path, l.begin, l.end, l.marker_line) for l in code_range] == [
        (LIB, 14, 18, 14)
    ]


def test_forward_links(model):
    helper = _link(model, "REQ-2", APP, "function helper()")
    assert (helper.begin, helper.end, helper.forward) == (12, 13, True)
    assert helper.marker_line is None
    line_range = next(
        l for l in model.requirements["REQ-2"].code if l.forward and l.path == LIB
    )
    assert (line_range.begin, line_range.end) == (10, 12)
    assert model.requirements["REQ-3"].code == []


def test_files_index(model):
    assert sorted(model.files) == [APP, LIB, APP.replace("app.py", "tests/test_app.py")]
    assert [l.uid for l in model.files[APP].links] == ["REQ-1", "REQ-1", "REQ-2"]


def test_broken_sdoc_reports_error(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(PROJECT, project)
    sdoc = project / "reqs.sdoc"
    sdoc.write_text(
        sdoc.read_text().replace("STATEMENT: The system", "STATMENT: The system")
    )

    result, errors = build_model(str(project), str(tmp_path / "cache"))

    assert result is None
    assert len(errors) == 1
    assert errors[0].path == str(sdoc)
    assert errors[0].line == 3  # the [REQUIREMENT] line of REQ-1
    assert "STATMENT" in errors[0].message


def test_unknown_uid_in_code_reports_error(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(PROJECT, project)
    app = project / "src" / "app.py"
    app.write_text(
        app.read_text().replace("REQ-1, scope=function", "REQ-404, scope=function")
    )

    result, errors = build_model(str(project), str(tmp_path / "cache"))

    assert result is None
    assert (errors[0].path, errors[0].line) == (str(app), 7)
    assert "REQ-404" in errors[0].message


def test_build_keeps_stdout_clean(tmp_path, capsys):
    build_model(str(PROJECT), str(tmp_path / "cache"))
    assert capsys.readouterr().out == ""


def test_virtualenv_in_project_is_ignored(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(PROJECT, project)
    # uv creates .venv inside the project; its Markdown files are not documents.
    site = project / ".venv" / "lib" / "site-packages" / "pkg.dist-info"
    site.mkdir(parents=True)
    (site / "AUTHORS.md").write_text("not a StrictDoc document\n")

    result, errors = build_model(str(project), str(tmp_path / "cache"))

    assert errors == []
    assert "REQ-1" in result.requirements


def test_tree_map_coverage(model):
    reqs = model.requirements
    # REQ-1 and REQ-2 link code and a test under tests/; REQ-3 is an uncovered leaf.
    assert [
        (u, reqs[u].missing_source, reqs[u].missing_tests) for u in sorted(reqs)
    ] == [
        ("REQ-1", 0, 0),
        ("REQ-2", 0, 0),
        ("REQ-3", 1, 1),
    ]


def test_tree_map_coverage_rule():
    from trace_model import CodeLink, Requirement, _set_coverage

    def req(uid, children=(), links=()):
        code = [CodeLink(uid, p, 0, 0, None, "", False, rel_path=p) for p in links]
        return Requirement(uid, uid, "", "", "", 0, [], list(children), code)

    reqs = {
        # P has no links of its own: covered with source because all children are.
        "P": req("P", ["A", "B"]),
        "A": req("A", links=["src/a.py", "tests/test_a.py"]),
        "B": req("B", links=["src/b.py"]),
        # Q: one child uncovered -> Q is uncovered, missing = the uncovered leaves below.
        "Q": req("Q", ["A", "C", "D"]),
        "C": req("C", ["E"]),
        "D": req("D"),
        "E": req("E"),
    }
    _set_coverage(reqs)
    got = {u: (r.missing_source, r.missing_tests) for u, r in reqs.items()}
    assert got == {
        "P": (0, 1),  # B has no tests
        "A": (0, 0),
        "B": (0, 1),
        "Q": (2, 2),  # E and D (A covers itself); tests: E and D
        "C": (1, 1),
        "D": (1, 1),
        "E": (1, 1),
    }


def test_sections_and_order(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(PROJECT, project)
    sdoc = project / "reqs.sdoc"
    text = sdoc.read_text()
    # Put REQ-2 in "Outer" > "Inner" and REQ-3 in "Outer".
    text = text.replace(
        "[REQUIREMENT]\nUID: REQ-2",
        "[[SECTION]]\nTITLE: Outer\n\n[[SECTION]]\nTITLE: Inner\n\n[REQUIREMENT]\nUID: REQ-2",
        1,
    )
    text = text.replace(
        "[REQUIREMENT]\nUID: REQ-3", "[[/SECTION]]\n\n[REQUIREMENT]\nUID: REQ-3", 1
    )
    sdoc.write_text(text.rstrip() + "\n\n[[/SECTION]]\n")
    result, errors = build_model(str(project), str(tmp_path / "cache"))
    assert errors == []
    reqs = result.requirements
    assert [
        (u, reqs[u].section, reqs[u].order) for u in ("REQ-1", "REQ-2", "REQ-3")
    ] == [
        ("REQ-1", [], 0),
        ("REQ-2", ["Outer", "Inner"], 1),
        ("REQ-3", ["Outer"], 2),
    ]
