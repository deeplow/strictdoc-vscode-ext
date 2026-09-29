// Navigation commands between code and requirements, backed by the server's custom requests.

import * as vscode from 'vscode';
import { LanguageClient } from 'vscode-languageclient/node';

interface RequirementLoc {
    uid: string;
    title: string;
    uri: string;
    line: number;
}

interface CodeLoc {
    uid: string;
    uri: string;
    begin: number;
    end: number;
    description: string;
    kind?: 'code' | 'test';
    role?: string | null;
}

interface Located {
    requirements: RequirementLoc[];
    code: CodeLoc[];
}

const SCOPES = ['function', 'class', 'file', 'line', 'range_start', 'range_end'];

function commentPrefix(languageId: string): string {
    if (['python', 'ruby', 'shellscript', 'yaml', 'toml', 'robotframework', 'robot'].includes(languageId)) {
        return '#';
    }
    if (['sql', 'lua', 'haskell'].includes(languageId)) {
        return '--';
    }
    return '//';
}

function openLocation(uri: string, line: number, endLine?: number): Thenable<unknown> {
    return vscode.commands.executeCommand('strictdoc.openLocation', uri, line, endLine);
}

async function locateAtCursor(client: LanguageClient): Promise<Located | undefined> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        return undefined;
    }
    return client.sendRequest<Located>('strictdoc/locate', {
        uri: editor.document.uri.toString(),
        line: editor.selection.active.line,
    });
}

async function pickCode(code: CodeLoc[]): Promise<void> {
    if (code.length === 0) {
        vscode.window.showInformationMessage('StrictDoc: no code is traced to this requirement.');
        return;
    }
    // Code first, then tests.
    const sorted = [...code].sort((a, b) => Number(a.kind === 'test') - Number(b.kind === 'test'));
    const items = sorted.map((c) => ({
        label: `${c.kind === 'test' ? '$(beaker)' : '$(code)'} ${vscode.workspace.asRelativePath(vscode.Uri.parse(c.uri))}:${c.begin + 1}-${c.end + 1}`,
        description: [c.role, c.description].filter(Boolean).join(' · '),
        detail: c.uid,
        code: c,
    }));
    const picked = items.length === 1 ? items[0] : await vscode.window.showQuickPick(items);
    if (picked) {
        await openLocation(picked.code.uri, picked.code.begin, picked.code.end);
    }
}

export function registerNavigationCommands(getClient: () => LanguageClient | undefined): vscode.Disposable[] {
    const withClient =
        (fn: (client: LanguageClient, ...args: any[]) => Promise<void>) =>
        async (...args: any[]) => {
            const client = getClient();
            if (!client?.isRunning()) {
                vscode.window.showWarningMessage('StrictDoc: the language server is not running.');
                return;
            }
            await fn(client, ...args);
        };

    return [
        vscode.commands.registerCommand(
            'strictdoc.goToRequirement',
            withClient(async (client) => {
                const reqs = (await locateAtCursor(client))?.requirements ?? [];
                if (reqs.length === 0) {
                    vscode.window.showInformationMessage('StrictDoc: no requirement traces this line.');
                    return;
                }
                const items = reqs.map((r) => ({ label: r.title || r.uid, description: r.uid, req: r }));
                const picked =
                    items.length === 1
                        ? items[0]
                        : await vscode.window.showQuickPick(items, { matchOnDescription: true });
                if (picked) {
                    await openLocation(picked.req.uri, picked.req.line);
                }
            }),
        ),
        vscode.commands.registerCommand(
            'strictdoc.goToCode',
            withClient(async (client, uid?: string) => {
                if (typeof uid === 'string') {
                    const graph = await client.sendRequest<{ code: CodeLoc[] }>('strictdoc/graph', {
                        uid,
                        up: 0,
                        down: 0,
                        includeCode: true,
                    });
                    await pickCode(graph.code);
                } else {
                    await pickCode((await locateAtCursor(client))?.code ?? []);
                }
            }),
        ),
        vscode.commands.registerCommand(
            'strictdoc.insertRelationMarker',
            withClient(async (client) => {
                const editor = vscode.window.activeTextEditor;
                if (!editor) {
                    return;
                }
                const tree = await client.sendRequest<{
                    docs: { title: string; requirements: { uid: string; title: string }[] }[];
                }>('strictdoc/requirements', {});
                const uidItems = tree.docs.flatMap((d) =>
                    d.requirements.map((r) => ({ label: r.title || r.uid, description: r.uid, detail: d.title })),
                );
                const uid = await vscode.window.showQuickPick(uidItems, {
                    placeHolder: 'Requirement to link',
                    matchOnDescription: true,
                });
                const scope = uid && (await vscode.window.showQuickPick(SCOPES, { placeHolder: 'Marker scope' }));
                if (!uid || !scope) {
                    return;
                }
                const line = editor.document.lineAt(editor.selection.active.line);
                const indent = line.text.substring(0, line.firstNonWhitespaceCharacterIndex);
                const prefix = commentPrefix(editor.document.languageId);
                await editor.edit((edit) =>
                    edit.insert(line.range.start, `${indent}${prefix} @relation(${uid.description}, scope=${scope})\n`),
                );
            }),
        ),
    ];
}
