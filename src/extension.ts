// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import * as vscode from 'vscode';
import { LanguageClient } from 'vscode-languageclient/node';
import { registerLogger, traceError, traceLog, traceVerbose } from './common/log/logging';
import { restartServer } from './common/server';
import { checkIfConfigurationChanged } from './common/settings';
import { loadServerDefaults } from './common/setup';
import { LS_SERVER_RESTART_DELAY } from './common/constants';
import { getLSClientTraceLevel } from './common/utilities';
import { createOutputChannel, onDidChangeConfiguration, registerCommand } from './common/vscodeapi';
import { registerNavigationCommands } from './navigation';
import { RequirementsTreeProvider } from './requirementsTree';
import { CoverageViewProvider } from './coverageView';
import { TraceGraphViewProvider } from './traceGraphView';

let lsClient: LanguageClient | undefined;
let isRestarting = false;
let restartTimer: NodeJS.Timeout | undefined;
export async function activate(context: vscode.ExtensionContext): Promise<void> {
    // This is required to get server name and module. This should be
    // the first thing that we do in this extension.
    const serverInfo = loadServerDefaults();
    const serverName = serverInfo.name;
    const serverId = serverInfo.module;

    // Setup logging
    const outputChannel = createOutputChannel(serverName);
    context.subscriptions.push(outputChannel, registerLogger(outputChannel));

    const changeLogLevel = async (c: vscode.LogLevel, g: vscode.LogLevel) => {
        const level = getLSClientTraceLevel(c, g);
        await lsClient?.setTrace(level);
    };

    context.subscriptions.push(
        outputChannel.onDidChangeLogLevel(async (e) => {
            await changeLogLevel(e, vscode.env.logLevel);
        }),
        vscode.env.onDidChangeLogLevel(async (e) => {
            await changeLogLevel(outputChannel.logLevel, e);
        }),
    );

    // Log Server information
    traceLog(`Name: ${serverInfo.name}`);
    traceLog(`Module: ${serverInfo.module}`);
    traceVerbose(`Full Server Info: ${JSON.stringify(serverInfo)}`);

    const cacheDir = (context.storageUri ?? context.globalStorageUri).fsPath;
    const getClient = () => lsClient;
    const requirementsTree = new RequirementsTreeProvider(getClient);
    const traceGraph = new TraceGraphViewProvider(context.extensionUri, getClient);
    const coverage = new CoverageViewProvider(context.extensionUri, getClient);
    context.subscriptions.push(traceGraph.onDidChangeFocus((uid) => coverage.setFocus(uid)));
    let indexUpdated: vscode.Disposable | undefined;
    let followTimer: NodeJS.Timeout | undefined;

    // Called after every (re)start: refresh the views now and whenever the server rebuilds its index.
    const onClientStarted = () => {
        indexUpdated?.dispose();
        indexUpdated = lsClient?.onNotification('strictdoc/indexUpdated', () => {
            requirementsTree.refresh();
            traceGraph.refresh();
            coverage.refresh();
        });
        requirementsTree.refresh();
        traceGraph.refresh();
        coverage.refresh();
    };

    const runServer = async () => {
        if (isRestarting) {
            if (restartTimer) {
                clearTimeout(restartTimer);
            }
            restartTimer = setTimeout(runServer, LS_SERVER_RESTART_DELAY);
            return;
        }
        isRestarting = true;
        try {
            lsClient = await restartServer(serverId, serverName, outputChannel, cacheDir, lsClient);
            onClientStarted();
        } finally {
            isRestarting = false;
        }
    };

    context.subscriptions.push(
        onDidChangeConfiguration(async (e: vscode.ConfigurationChangeEvent) => {
            if (checkIfConfigurationChanged(e, serverId)) {
                await runServer();
            }
        }),
        registerCommand(`${serverId}.restart`, async () => {
            await runServer();
        }),
        vscode.window.registerTreeDataProvider('strictdoc.requirements', requirementsTree),
        vscode.window.registerWebviewViewProvider('strictdoc.traceGraph', traceGraph),
        vscode.window.registerWebviewViewProvider('strictdoc.coverage', coverage),
        registerCommand('strictdoc.rebuild', () => lsClient?.sendRequest('strictdoc/rebuild', {})),
        registerCommand('strictdoc.toggleUntracedFilter', () => requirementsTree.toggleUntracedFilter()),
        registerCommand('strictdoc.focusRequirement', async (uid: string) => {
            await vscode.commands.executeCommand('strictdoc.traceGraph.focus');
            traceGraph.focus(uid);
        }),
        registerCommand('strictdoc.openLocation', async (uri: string, line: number, endLine?: number) => {
            const selection = new vscode.Range(line, 0, line, 0);
            const editor = await vscode.window.showTextDocument(vscode.Uri.parse(uri), { selection });
            const range = new vscode.Range(line, 0, endLine ?? line, 0);
            editor.revealRange(range, vscode.TextEditorRevealType.InCenterIfOutsideViewport);
        }),
        ...registerNavigationCommands(getClient),
        vscode.window.onDidChangeTextEditorSelection((e) => {
            if (e.textEditor.document.uri.scheme !== 'file') {
                return;
            }
            clearTimeout(followTimer);
            followTimer = setTimeout(() => {
                traceGraph.followLocation(e.textEditor.document.uri.toString(), e.selections[0].active.line);
            }, 300);
        }),
    );

    setImmediate(runServer);
}

export async function deactivate(): Promise<void> {
    if (lsClient) {
        try {
            await lsClient.stop();
        } catch (ex) {
            traceError(`Server: Stop failed: ${ex}`);
        }
    }
}
