// Coverage view: StrictDoc's requirements coverage with source / tests (tree map),
// for the project, each top-level requirement and the Trace Graph's focus.

import * as vscode from 'vscode';
import { LanguageClient } from 'vscode-languageclient/node';

export class CoverageViewProvider implements vscode.WebviewViewProvider {
    private view?: vscode.WebviewView;
    private focusUid?: string;

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
        view.webview.onDidReceiveMessage((msg: { type: string; uid: string }) => {
            if (msg.type === 'ready') {
                void this.refresh();
            } else if (msg.type === 'focus') {
                void vscode.commands.executeCommand('strictdoc.focusRequirement', msg.uid);
            }
        });
        view.onDidDispose(() => (this.view = undefined));
    }

    /** Follow the Trace Graph's focused requirement (undefined when it shows the top level). */
    setFocus(uid: string | undefined): void {
        if (uid !== this.focusUid) {
            this.focusUid = uid;
            void this.refresh();
        }
    }

    async refresh(): Promise<void> {
        const client = this.getClient();
        if (!this.view || !client) {
            return;
        }
        try {
            const data = await client.sendRequest('strictdoc/coverage', { uid: this.focusUid ?? null });
            void this.view.webview.postMessage({ type: 'coverage', data });
        } catch {
            // The next index update refreshes the view.
        }
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
<script nonce="${nonce}" src="${media('coverage.js')}"></script>
</body>
</html>`;
    }
}
