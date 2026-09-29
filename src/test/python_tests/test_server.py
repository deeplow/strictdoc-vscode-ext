# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
"""
LSP round trip against the sample StrictDoc project.

StrictDoc prints to stdout, so any response arriving here also proves that
the protocol stream is not corrupted.
"""

from threading import Event

from hamcrest import assert_that, contains_string, ends_with, has_item, is_

from .lsp_test_client import defaults, session, utils

PROJECT = defaults.TEST_PROJECT
APP_PATH = PROJECT / "src" / "app.py"
APP_URI = utils.as_uri(str(APP_PATH))
TIMEOUT = 60  # a cold StrictDoc build can take a while


def _marker_position():
    """Position of REQ-1 in the function-scope marker of app.py."""
    lines = APP_PATH.read_text(encoding="utf-8").splitlines()
    line = next(i for i, text in enumerate(lines) if "scope=function" in text)
    return {"line": line, "character": lines[line].index("REQ-1") + 1}


def _single_location(result):
    """Definition may be a Location, a list of them or LocationLinks."""
    location = result[0] if isinstance(result, list) else result
    return location.get("uri") or location.get("targetUri")


def test_round_trip():
    """Build the index, then hover and go to definition on a marker."""
    params = {"textDocument": {"uri": APP_URI}, "position": _marker_position()}

    with session.LspSession() as ls_session:
        index_updated = Event()
        updates = []

        def _on_index_updated(update):
            updates.append(update)
            index_updated.set()

        ls_session.set_notification_callback(session.INDEX_UPDATED, _on_index_updated)
        ls_session.initialize(defaults.VSCODE_DEFAULT_INITIALIZE)
        assert_that(index_updated.wait(TIMEOUT), is_(True))
        assert_that(updates[0]["errorCount"], is_(0))

        hover = ls_session.text_document_hover(params)
        assert_that(str(hover["contents"]), contains_string("System shall do X"))

        definition = ls_session.text_document_definition(params)
        assert_that(_single_location(definition), ends_with("reqs.sdoc"))

        helper_line = (
            APP_PATH.read_text(encoding="utf-8").splitlines().index("    return 41")
        )
        located = ls_session.request(
            "strictdoc/locate", {"uri": APP_URI, "line": helper_line}
        )
        uids = [req["uid"] for req in located["requirements"]]
        assert_that(uids, has_item("REQ-2"))

        rebuilt = ls_session.request("strictdoc/rebuild", {})
        assert_that(rebuilt, is_({"ok": True, "errorCount": 0}))
