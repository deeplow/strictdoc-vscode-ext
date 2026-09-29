// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import * as vscode from 'vscode';
import { LanguageClient } from 'vscode-languageclient/node';

type Focus = { uid: string } | { uri: string; line: number };

interface GraphData {
    focus: string[];
    nodes: unknown[];
    edges: [string, string][];
    code: unknown[];
    rootCount: number;
}

/** Messages from the webview (fields depend on `type`). */
interface Message {
    type: 'ready' | 'search' | 'focus' | 'home' | 'open' | 'toggleCode' | 'toggleWarnings' | 'back';
    uid: string;
    uri: string;
    line: number;
    endLine?: number;
}

interface State {
    focus?: Focus; // undefined = home: the list of top-level requirements
    includeCode: boolean; // show code/test links: count pills on rows, link lists in the card
    showWarnings: boolean; // show the yellow ⚠ coverage warnings
    history: (string | null)[]; // null = home
}

/**
 * The Trace Graph webview: the focused requirement with its direct parents and children,
 * or (home) the list of top-level requirements.
 */
export class TraceGraphViewProvider implements vscode.WebviewViewProvider {
    private view?: vscode.WebviewView;
    private data?: GraphData;
    private state: State = { includeCode: false, showWarnings: true, history: [] };
    private readonly focusChanged = new vscode.EventEmitter<string | undefined>();
    /** The focused requirement changed (undefined: the top-level list is shown). */
    readonly onDidChangeFocus = this.focusChanged.event;

    constructor(
        private readonly extensionUri: vscode.Uri,
        private readonly getClient: () => LanguageClient | undefined,
    ) {}

    resolveWebviewView(view: vscode.WebviewView): void {
        this.view = view;
        view.webview.options = {
            enableScripts: true,
            localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, 'media')],
        };
        view.webview.html = this.getHtml(view.webview);
        view.webview.onDidReceiveMessage((msg: Message) => this.onMessage(msg));
        view.onDidDispose(() => (this.view = undefined));
    }

    /** Focus the graph on a requirement, remembering the previous view for "back". */
    focus(uid: string): void {
        const current = this.current();
        if (current !== uid) {
            this.state.history.push(current);
        }
        this.state.focus = { uid };
        this.view?.show(true);
        void this.refresh();
    }

    /** Show the list of top-level requirements. */
    home(): void {
        const current = this.current();
        if (current !== null) {
            this.state.history.push(current);
        }
        this.state.focus = undefined;
        this.data = undefined;
        void this.refresh();
    }

    /** The focused requirement, or null when home. */
    private current(): string | null {
        return (this.state.focus && this.data?.focus[0]) || null;
    }

    /** Follow the editor cursor; keeps the previous graph when nothing is traced there. */
    followLocation(uri: string, line: number): void {
        void this.refresh({ uri, line });
    }

    async refresh(focus: Focus | undefined = this.state.focus): Promise<void> {
        const client = this.getClient();
        if (!client) {
            this.post({ type: 'status', text: 'Starting StrictDoc server…' });
            return;
        }
        const { includeCode } = this.state;
        try {
            if (!focus) {
                const { nodes } = await client.sendRequest<{ nodes: unknown[] }>('strictdoc/roots', {});
                this.post({ type: 'home', nodes, state: this.state });
                this.focusChanged.fire(undefined);
                return;
            }
            const data = await client.sendRequest<GraphData>('strictdoc/graph', {
                ...focus,
                up: 1,
                down: 1,
                includeCode,
            });
            if (!data.focus.length) {
                if (focus === this.state.focus && !this.data) {
                    this.post({ type: 'status', text: 'Nothing to show.' });
                }
                return;
            }
            this.state.focus = focus;
            this.data = data;
            this.postGraph();
            this.focusChanged.fire(data.focus[0]);
        } catch (err) {
            this.post({ type: 'status', text: `Trace graph failed: ${err}` });
        }
    }

    private postGraph(): void {
        if (this.data) {
            this.post({ type: 'graph', data: this.data, state: this.state });
        }
    }

    private async postList(): Promise<void> {
        const client = this.getClient();
        if (!client) {
            return;
        }
        try {
            const res = await client.sendRequest<{ docs: { requirements: { uid: string; title: string }[] }[] }>(
                'strictdoc/requirements',
                {},
            );
            const items = res.docs.flatMap((d) => d.requirements.map((r) => ({ uid: r.uid, title: r.title })));
            this.post({ type: 'list', items });
        } catch {
            // Suggestions are optional; the graph still works without them.
        }
    }

    private onMessage(msg: Message): void {
        switch (msg.type) {
            case 'ready':
                this.post({ type: 'state', state: this.state });
                void this.postList();
                if (this.data) {
                    this.postGraph();
                } else {
                    void this.refresh();
                }
                break;
            case 'search':
                void this.postList();
                break;
            case 'focus':
                this.focus(msg.uid);
                break;
            case 'home':
                this.home();
                break;
            case 'open':
                void vscode.commands.executeCommand('strictdoc.openLocation', msg.uri, msg.line, msg.endLine);
                break;
            case 'toggleCode':
                this.state.includeCode = !this.state.includeCode;
                void this.refresh();
                break;
            case 'toggleWarnings':
                // Display only: the webview already has the data.
                this.state.showWarnings = !this.state.showWarnings;
                this.post({ type: 'state', state: this.state });
                break;
            case 'back': {
                if (!this.state.history.length) {
                    break;
                }
                const uid = this.state.history.pop();
                this.state.focus = uid ? { uid } : undefined;
                this.data = uid ? this.data : undefined;
                void this.refresh();
                break;
            }
        }
    }

    private post(msg: unknown): void {
        void this.view?.webview.postMessage(msg);
    }

    private getHtml(webview: vscode.Webview): string {
        const media = (file: string) => webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, 'media', file));
        const nonce = Array.from({ length: 32 }, () => Math.floor(Math.random() * 36).toString(36)).join('');
        return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="${media('traceGraph.css')}" rel="stylesheet">
</head>
<body>
<div id="app"></div>
<script nonce="${nonce}" src="${media('traceGraph.js')}"></script>
</body>
</html>`;
    }
}
