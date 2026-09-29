# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
"""Tests for the editor features computed from the trace model."""

import pathlib

import trace_features as tf

from .conftest import PROJECT

SDOC = str(PROJECT / "reqs.sdoc")
APP = str(PROJECT / "src" / "app.py")
LIB = str(PROJECT / "src" / "lib.c")
TEST_APP = str(PROJECT / "src" / "tests" / "test_app.py")
SDOC_URI = pathlib.Path(SDOC).as_uri()
APP_URI = pathlib.Path(APP).as_uri()


def _text(path):
    return pathlib.Path(path).read_text()


def _lines(locations):
    return [(l.uri, l.range.start.line, l.range.end.line) for l in locations]


def test_marker_uids():
    assert tf.marker_uids("# @relation(REQ-1, REQ-2, scope=function)") == [
        ("REQ-1", 12, 17),
        ("REQ-2", 19, 24),
    ]


def test_hover_on_marker(model):
    result = tf.hover(model, APP, _text(APP), 7, 16)  # "@relation(REQ-1" in do_x
    assert "**System shall do X** · `REQ-1`" in result.contents.value
    assert "*Children:* REQ-2, REQ-3" in result.contents.value
    assert tf.hover(model, APP, _text(APP), 9, 5) is None


def test_definition_code_to_requirement(model):
    result = tf.definition(model, APP, _text(APP), 7, 16)
    assert _lines(result) == [(SDOC_URI, 4, 4)]


def test_definition_requirement_to_code(model):
    result = tf.definition(model, SDOC, _text(SDOC), 4, 6)  # "UID: REQ-1"
    assert _lines(result) == [
        (APP_URI, 0, 14),
        (APP_URI, 5, 10),
        (pathlib.Path(LIB).as_uri(), 2, 8),
        (pathlib.Path(TEST_APP).as_uri(), 3, 8),
    ]


def test_definition_parent_and_file_values(model):
    text = _text(SDOC)
    assert _lines(tf.definition(model, SDOC, text, 14, 10)) == [(SDOC_URI, 4, 4)]
    assert _lines(tf.definition(model, SDOC, text, 16, 10)) == [(APP_URI, 12, 14)]


def test_references(model):
    result = tf.references(model, SDOC, _text(SDOC), 4, 6)
    assert len(result) == 6  # three code ranges, one test and two children
    assert _lines(result)[-2:] == [(SDOC_URI, 9, 9), (SDOC_URI, 23, 23)]


def test_document_links(model):
    links = tf.document_links(model, APP, _text(APP))
    assert [(l.range.start.line, l.range.start.character) for l in links] == [
        (1, 10),
        (7, 14),
    ]
    assert links[0].target == f"{SDOC_URI}#L5"


def test_code_lenses_in_sdoc(model):
    lenses = tf.code_lenses(model, SDOC, _text(SDOC))
    assert [l.range.start.line for l in lenses] == [4, 4, 9, 9, 23, 23]
    assert lenses[0].command.title == "↑0 parents · ↓2 children"
    assert lenses[0].command.arguments == ["REQ-1"]
    assert lenses[1].command.title == "⟨⟩3 code · ✓1 tests"
    assert lenses[1].command.command == "strictdoc.goToCode"


def test_code_lenses_in_code(model):
    lenses = tf.code_lenses(model, APP, _text(APP))
    assert [(l.range.start.line, l.command.title) for l in lenses] == [
        (0, "System shall do X · REQ-1"),
        (5, "System shall do X · REQ-1"),
        (12, "traced by Helper for X · REQ-2 (reqs.sdoc)"),
    ]
    assert lenses[2].command.arguments == [SDOC_URI, 9]


def test_completions(model):
    items = tf.completions(model, APP, "# @relation(RE", 0, 14)
    assert [i.label for i in items] == ["REQ-1", "REQ-2", "REQ-3"]
    assert tf.completions(model, APP, "# @relation(REQ-1) x", 0, 20) == []
    assert len(tf.completions(model, SDOC, "  VALUE: ", 0, 9)) == 3


def test_diagnostics(model):
    from trace_model import BuildError

    result = tf.diagnostics(
        model, [BuildError(SDOC, 3, "bad"), BuildError(None, 0, "x")]
    )
    assert list(result) == [SDOC]
    assert result[SDOC][0].range.start.line == 3


def test_locate_forward_linked_function(model):
    result = tf.locate(model, APP, 13)  # inside helper()
    assert [r["uid"] for r in result["requirements"]] == ["REQ-2", "REQ-1"]
    assert result["code"][0]["forward"] is True


def test_locate_in_sdoc(model):
    result = tf.locate(model, SDOC, 11)  # STATEMENT of REQ-2
    assert [r["uid"] for r in result["requirements"]] == ["REQ-2"]
    assert len(result["code"]) == 4


def test_graph_depth(model):
    one = tf.graph(model, "REQ-1", None, None, 1, 1, False)
    assert one["focus"] == ["REQ-1"]
    assert [n["uid"] for n in one["nodes"]] == ["REQ-1", "REQ-2", "REQ-3"]
    assert one["edges"] == [["REQ-1", "REQ-2"], ["REQ-1", "REQ-3"]]
    assert one["code"] == []

    none = tf.graph(model, "REQ-1", None, None, 0, 0, True)
    assert [n["uid"] for n in none["nodes"]] == ["REQ-1"]
    assert len(none["code"]) == 4

    child = tf.graph(model, "REQ-2", None, None, -1, -1, False)
    assert [n["uid"] for n in child["nodes"]] == ["REQ-1", "REQ-2"]
    assert child["edges"] == [["REQ-1", "REQ-2"]]


def test_graph_focus_from_location_and_badges(model):
    result = tf.graph(model, None, APP, 13, -1, -1, False)
    assert result["focus"] == ["REQ-2", "REQ-1"]
    badges = {n["uid"]: [b["kind"] for b in n["badges"]] for n in result["nodes"]}
    assert badges == {
        "REQ-1": ["code", "test"],
        "REQ-2": ["code", "test"],
        "REQ-3": ["code", "test"],
    }
    counts = {n["uid"]: (n["parentCount"], n["childCount"]) for n in result["nodes"]}
    assert counts["REQ-1"] == (0, 2)


def test_graph_depth_and_layer_title(model):
    result = tf.graph(model, "REQ-1", None, None, -1, -1, False)
    depths = {n["uid"]: n["depth"] for n in result["nodes"]}
    assert depths == {"REQ-1": 0, "REQ-2": 1, "REQ-3": 1}
    node = result["nodes"][0]
    assert node["layerTitle"] == "Sample requirements"
    assert node["statement"] == model.requirements["REQ-1"].statement[:300]


def test_requirements_tree(model):
    docs = tf.requirements_tree(model)["docs"]
    assert docs[0]["title"] == "Sample requirements"
    assert docs[0]["path"] == SDOC
    assert [(r["uid"], r["untraced"]) for r in docs[0]["requirements"]] == [
        ("REQ-1", False),
        ("REQ-2", False),
        ("REQ-3", True),
    ]


def test_roots(model):
    assert [n["uid"] for n in tf.roots(model)["nodes"]] == ["REQ-1"]
    assert tf.graph(model, "REQ-2", None, None, 1, 1, False)["rootCount"] == 1


def test_code_and_test_links(model):
    links = {
        (l.path.split("/")[-1], l.begin): l for l in model.requirements["REQ-1"].code
    }
    assert links[("app.py", 5)].kind == "code"
    by_role = links[("test_app.py", 3)]
    assert (by_role.kind, by_role.role) == ("test", "Verification")
    by_path = [
        l for l in model.requirements["REQ-2"].code if l.path.endswith("test_app.py")
    ][0]
    assert (by_path.kind, by_path.role) == ("test", None)
    lenses = tf.code_lenses(model, TEST_APP, _text(TEST_APP))
    assert [l.command.title for l in lenses] == [
        "✓ verifies System shall do X · REQ-1",
        "✓ verifies Helper for X · REQ-2",
    ]


def test_link_pills_count_the_subtree(model):
    nodes = {
        n["uid"]: n for n in tf.graph(model, "REQ-1", None, None, 1, 1, False)["nodes"]
    }
    pills = {b["kind"]: b["text"] for b in nodes["REQ-1"]["badges"]}
    # REQ-1 has 3 code links and 1 test of its own; child REQ-2 adds 3 and 1.
    assert pills == {"code": "⟨⟩ 6", "test": "✓ 2"}
    assert (nodes["REQ-1"]["totalCode"], nodes["REQ-1"]["totalTests"]) == (6, 2)
    assert "(3 own)" in nodes["REQ-1"]["badges"][0]["title"]
    assert {b["kind"]: b["text"] for b in nodes["REQ-2"]["badges"]} == {
        "code": "⟨⟩ 3",
        "test": "✓ 1",
    }


def test_zero_counts_are_warning_pills(model):
    nodes = {
        n["uid"]: n for n in tf.graph(model, "REQ-1", None, None, 1, 1, False)["nodes"]
    }
    zero = {b["kind"]: b for b in nodes["REQ-3"]["badges"]}
    assert {k: (b["text"], b["zero"]) for k, b in zero.items()} == {
        "code": ("⟨⟩ 0", True),
        "test": ("✓ 0", True),
    }
    assert (
        zero["test"]["title"] == "No tests for this requirement or its sub-requirements"
    )
    assert not any(b["zero"] for b in nodes["REQ-1"]["badges"])


def test_coverage_view(model):
    result = tf.coverage(model, "REQ-2")
    assert result["project"] == {"requirements": 3, "withSource": 2, "withTests": 2}
    assert [(r["uid"], r["coverage"]) for r in result["roots"]] == [
        ("REQ-1", {"requirements": 3, "withSource": 2, "withTests": 2})
    ]
    assert result["focus"]["uid"] == "REQ-2"
    assert result["focus"]["coverage"] == {
        "requirements": 1,
        "withSource": 1,
        "withTests": 1,
    }
    assert tf.coverage(model)["focus"] is None
    nodes = {
        n["uid"]: n for n in tf.graph(model, "REQ-1", None, None, 1, 1, False)["nodes"]
    }
    missing = {
        b["kind"]: (b["missing"], b["missingTitle"]) for b in nodes["REQ-3"]["badges"]
    }
    assert missing == {
        "code": (1, "1 requirement below has no source (StrictDoc tree-map coverage)"),
        "test": (1, "1 requirement below has no tests (StrictDoc tree-map coverage)"),
    }


def test_node_sections_and_order(model):
    node = tf.roots(model)["nodes"][0]
    assert (node["sections"], node["order"]) == ([], 0)
    assert node["docKey"].endswith("reqs.sdoc")
