# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
"""StrictDoc Trace language server."""

from __future__ import annotations

import importlib.metadata
import json
import os
import pathlib
import sys
import tempfile
import threading


# **********************************************************
# Update sys.path before importing any bundled libraries.
# **********************************************************
def update_sys_path(path_to_add: str, strategy: str) -> None:
    """Add given path to `sys.path`."""
    if path_to_add not in sys.path and os.path.isdir(path_to_add):
        if strategy == "useBundled":
            sys.path.insert(0, path_to_add)
        elif strategy == "fromEnvironment":
            sys.path.append(path_to_add)


# Ensure that we can import LSP libraries, and other bundled libraries.
update_sys_path(
    os.fspath(pathlib.Path(__file__).parent.parent / "libs"),
    os.getenv("LS_IMPORT_STRATEGY", "useBundled"),
)

# StrictDoc prints to stdout and html2pdf4doc reopens stdout by fd. Keep the
# real stdout for the LSP protocol and send every other write to stderr.
REAL_STDOUT = sys.stdout.buffer
sys.stdout = sys.stderr

# **********************************************************
# Imports needed for the language server goes below this.
# **********************************************************
# pylint: disable=wrong-import-position,import-error
from lsprotocol import types as lsp
from packaging.version import Version
from pygls import uris
from pygls.lsp.server import LanguageServer

MIN_STRICTDOC = "0.30.1"
INSTALL_HINT = f'uv add "strictdoc>={MIN_STRICTDOC}" (or pip install -U strictdoc)'


def _check_strictdoc() -> str | None:
    """Return an error message if strictdoc is missing or too old."""
    try:
        version = importlib.metadata.version("strictdoc")
    except importlib.metadata.PackageNotFoundError:
        return f"strictdoc is not installed in the selected interpreter: {INSTALL_HINT}"
    if Version(version) < Version(MIN_STRICTDOC):
        return (
            f"strictdoc {version} is too old (need >= {MIN_STRICTDOC}): {INSTALL_HINT}"
        )
    return None


STRICTDOC_ERROR = _check_strictdoc()
if STRICTDOC_ERROR is None:
    # trace_model imports html2pdf4doc, which reopens sys.stdout by fd: it
    # must happen after the redirection above.
    import trace_features
    import trace_model

WORKSPACE_SETTINGS = {}
CACHE_DIR = os.path.join(tempfile.gettempdir(), "strictdoc-trace")

MODEL = None  # last good trace_model.TraceModel
# Why there is no MODEL: {"needsConfig": True} until a configuration is chosen, or
# {"error": message} when StrictDoc failed; None once an index exists or while building.
NO_INDEX: dict | None = None
PUBLISHED_URIS: set[str] = set()
BUILD_LOCK = threading.Lock()
BUILD_TIMER: threading.Timer | None = None
DEBOUNCE_SECONDS = 0.5

LSP_SERVER = LanguageServer(name="StrictDoc Trace", version="0.1.0", max_workers=5)


# **********************************************************
# Index building.
# **********************************************************
def schedule_build(delay: float = DEBOUNCE_SECONDS) -> None:
    """Rebuild the index in a thread after `delay` seconds, restarting the timer."""
    global BUILD_TIMER  # pylint: disable=global-statement
    if BUILD_TIMER is not None:
        BUILD_TIMER.cancel()
    BUILD_TIMER = threading.Timer(delay, build)
    BUILD_TIMER.daemon = True
    BUILD_TIMER.start()


def build() -> tuple[bool, int]:
    """Rebuild the index, publish diagnostics and notify the client.

    Returns (ok, error_count). On failure the last good model is kept.
    """
    global MODEL, NO_INDEX  # pylint: disable=global-statement
    if STRICTDOC_ERROR:
        log_error(STRICTDOC_ERROR)
        return False, 1

    with BUILD_LOCK:
        project_dir = _get_project_dir()
        if _settings().get("needsConfig"):
            # No configuration chosen yet: ask for one instead of building with defaults.
            log_to_output("No StrictDoc configuration selected: not building the index.")
            NO_INDEX = {"needsConfig": True, "projectDir": project_dir}
            LSP_SERVER.protocol.notify("strictdoc/indexUpdated", {**NO_INDEX, "requirementCount": 0, "errorCount": 0})
            return False, 0
        log_to_output(f"Building StrictDoc index for {project_dir}")
        # build_model captures strictdoc output and turns failures into errors.
        model, errors = trace_model.build_model(project_dir, CACHE_DIR)
        for error in errors:
            log_to_output(f"{error.path}:{error.line + 1}: {error.message}")
        if model is not None:
            MODEL = model
        NO_INDEX = (
            {"error": errors[0].message, "projectDir": project_dir}
            if MODEL is None and errors
            else None
        )
        _publish_diagnostics(trace_features.diagnostics(MODEL, errors))
        LSP_SERVER.protocol.notify(
            "strictdoc/indexUpdated",
            {
                **(NO_INDEX or {}),
                "requirementCount": len(MODEL.requirements) if MODEL else 0,
                "errorCount": len(errors),
            },
        )
        return model is not None, len(errors)


def _publish_diagnostics(by_path: dict) -> None:
    """Publish diagnostics per file and clear files that no longer have any."""
    uris_now = {uris.from_fs_path(path): diags for path, diags in by_path.items()}
    for uri in PUBLISHED_URIS - uris_now.keys():
        uris_now[uri] = []
    for uri, diags in uris_now.items():
        LSP_SERVER.text_document_publish_diagnostics(
            lsp.PublishDiagnosticsParams(uri=uri, diagnostics=diags)
        )
    PUBLISHED_URIS.clear()
    PUBLISHED_URIS.update(uri for uri, diags in uris_now.items() if diags)


# **********************************************************
# Document sync: rebuild on save or on watched file changes.
# **********************************************************
@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
def did_save(_params: lsp.DidSaveTextDocumentParams) -> None:
    """LSP handler for textDocument/didSave request."""
    schedule_build()


@LSP_SERVER.feature(lsp.WORKSPACE_DID_CHANGE_WATCHED_FILES)
def did_change_watched_files(_params: lsp.DidChangeWatchedFilesParams) -> None:
    """LSP handler for workspace/didChangeWatchedFiles notification."""
    schedule_build()


# **********************************************************
# Language features, delegated to trace_features.
# **********************************************************
def _document(uri: str) -> tuple[str, str]:
    """Return (path, text) for a document uri."""
    return uris.to_fs_path(uri), LSP_SERVER.workspace.get_text_document(uri).source


def _at_position(func, params):
    """Call a trace_features function taking (model, path, text, line, character)."""
    if MODEL is None:
        return None
    path, text = _document(params.text_document.uri)
    pos = params.position
    return func(MODEL, path, text, pos.line, pos.character)


def _in_document(func, params):
    """Call a trace_features function taking (model, path, text)."""
    if MODEL is None:
        return None
    return func(MODEL, *_document(params.text_document.uri))


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_HOVER)
def hover(params: lsp.HoverParams) -> lsp.Hover | None:
    """LSP handler for textDocument/hover request."""
    return _at_position(trace_features.hover, params)


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_DEFINITION)
def definition(params: lsp.DefinitionParams):
    """LSP handler for textDocument/definition request."""
    return _at_position(trace_features.definition, params)


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_REFERENCES)
def references(params: lsp.ReferenceParams):
    """LSP handler for textDocument/references request."""
    return _at_position(trace_features.references, params)


@LSP_SERVER.feature(
    lsp.TEXT_DOCUMENT_COMPLETION,
    lsp.CompletionOptions(trigger_characters=["(", ",", " "]),
)
def completion(params: lsp.CompletionParams):
    """LSP handler for textDocument/completion request."""
    return _at_position(trace_features.completions, params)


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_CODE_LENS)
def code_lens(params: lsp.CodeLensParams):
    """LSP handler for textDocument/codeLens request."""
    return _in_document(trace_features.code_lenses, params)


@LSP_SERVER.feature(lsp.TEXT_DOCUMENT_DOCUMENT_LINK)
def document_link(params: lsp.DocumentLinkParams):
    """LSP handler for textDocument/documentLink request."""
    return _in_document(trace_features.document_links, params)


# **********************************************************
# Custom requests. Params arrive as namedtuples (or None).
# **********************************************************
def _param(params, name: str, default=None):
    return getattr(params, name, default) if params is not None else default


def _uri_to_path(uri: str | None) -> str | None:
    return uris.to_fs_path(uri) if uri else None


@LSP_SERVER.feature("strictdoc/requirements")
def requirements(_params=None) -> dict:
    """Return the requirements tree: {docs: [...]}."""
    if MODEL is None:
        return {"docs": []}
    return trace_features.requirements_tree(MODEL)


@LSP_SERVER.feature("strictdoc/roots")
def roots(_params=None) -> dict:
    """Return the top-level requirements: {nodes: [...]}."""
    if MODEL is None:
        return {"nodes": [], **(NO_INDEX or {})}
    return trace_features.roots(MODEL)


@LSP_SERVER.feature("strictdoc/graph")
def graph(params=None) -> dict:
    """Return the lineage subgraph around a uid or a document position."""
    if MODEL is None:
        return {"focus": [], "nodes": [], "edges": [], "code": []}
    return trace_features.graph(
        MODEL,
        _param(params, "uid"),
        _uri_to_path(_param(params, "uri")),
        _param(params, "line"),
        _param(params, "up", 1),
        _param(params, "down", 1),
        _param(params, "includeCode", False),
    )


@LSP_SERVER.feature("strictdoc/locate")
def locate(params=None) -> dict:
    """Return the requirements covering a code line, or the code of an .sdoc line."""
    path = _uri_to_path(_param(params, "uri"))
    if MODEL is None or path is None:
        return {"requirements": [], "code": []}
    return trace_features.locate(MODEL, path, _param(params, "line", 0))


@LSP_SERVER.thread()
@LSP_SERVER.feature("strictdoc/rebuild")
def rebuild(_params=None) -> dict:
    """Rebuild the index now and report the result."""
    ok, error_count = build()
    return {"ok": ok, "errorCount": error_count}


# **********************************************************
# Required Language Server Initialization and Exit handlers.
# **********************************************************
@LSP_SERVER.feature(lsp.INITIALIZE)
def initialize(params: lsp.InitializeParams) -> None:
    """LSP handler for initialize request."""
    global CACHE_DIR  # pylint: disable=global-statement
    log_to_output(f"CWD Server: {os.getcwd()}")

    paths = "\r\n   ".join(sys.path)
    log_to_output(f"sys.path used to run Server:\r\n   {paths}")

    options = params.initialization_options or {}
    CACHE_DIR = options.get("cacheDir") or CACHE_DIR

    settings = options.get("settings", [])
    _update_workspace_settings(settings)
    log_to_output(
        f"Settings used to run Server:\r\n{json.dumps(settings, indent=4, ensure_ascii=False)}\r\n"
    )


@LSP_SERVER.feature(lsp.INITIALIZED)
def initialized(_params: lsp.InitializedParams) -> None:
    """LSP handler for initialized notification: build the index."""
    schedule_build(0)


def _update_workspace_settings(settings):
    if not settings:
        key = os.getcwd()
        WORKSPACE_SETTINGS[key] = {"workspaceFS": key, "projectPath": key}
        return

    for setting in settings:
        key = uris.to_fs_path(setting["workspace"])
        WORKSPACE_SETTINGS[key] = {**setting, "workspaceFS": key}


def _settings() -> dict:
    """The settings of the first workspace (multi-root not supported)."""
    return next(iter(WORKSPACE_SETTINGS.values()))


def _get_project_dir() -> str:
    settings = _settings()
    return settings.get("projectPath") or settings["workspaceFS"]


# *****************************************************
# Logging and notification.
# *****************************************************
def log_to_output(
    message: str, msg_type: lsp.MessageType = lsp.MessageType.Log
) -> None:
    LSP_SERVER.window_log_message(lsp.LogMessageParams(type=msg_type, message=message))


def log_error(message: str) -> None:
    LSP_SERVER.window_log_message(
        lsp.LogMessageParams(type=lsp.MessageType.Error, message=message)
    )
    if os.getenv("LS_SHOW_NOTIFICATION", "off") in ["onError", "onWarning", "always"]:
        LSP_SERVER.window_show_message(
            lsp.ShowMessageParams(type=lsp.MessageType.Error, message=message)
        )


# *****************************************************
# Start the server.
# *****************************************************
if __name__ == "__main__":
    LSP_SERVER.start_io(stdout=REAL_STDOUT)
