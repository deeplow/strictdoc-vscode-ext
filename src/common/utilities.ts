// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import * as fs from 'fs-extra';
import * as path from 'path';
import { LogLevel, Uri, window, workspace, WorkspaceFolder } from 'vscode';
import { Trace } from 'vscode-jsonrpc/node';
import { getConfiguration, getWorkspaceFolders } from './vscodeapi';

function logLevelToTrace(logLevel: LogLevel): Trace {
    switch (logLevel) {
        case LogLevel.Error:
        case LogLevel.Warning:
        case LogLevel.Info:
            return Trace.Messages;

        case LogLevel.Debug:
        case LogLevel.Trace:
            return Trace.Verbose;

        case LogLevel.Off:
        default:
            return Trace.Off;
    }
}

export function getLSClientTraceLevel(channelLogLevel: LogLevel, globalLogLevel: LogLevel): Trace {
    if (channelLogLevel === LogLevel.Off) {
        return logLevelToTrace(globalLogLevel);
    }
    if (globalLogLevel === LogLevel.Off) {
        return logLevelToTrace(channelLogLevel);
    }
    const level = logLevelToTrace(channelLogLevel <= globalLogLevel ? channelLogLevel : globalLogLevel);
    return level;
}

function isStrictdocProject(folder: WorkspaceFolder): boolean {
    const projectPath = getConfiguration('strictdoc', folder.uri).inspect<string>('projectPath');
    return (
        projectPath?.workspaceFolderValue !== undefined ||
        ['strictdoc_config.py', 'strictdoc.toml'].some((f) => fs.existsSync(path.join(folder.uri.fsPath, f)))
    );
}

// The StrictDoc project folder: the active editor's folder if it is one, else the first
// folder that is one, else the first folder. Multi-root workspaces get one project.
export async function getProjectRoot(): Promise<WorkspaceFolder> {
    const workspaces = getWorkspaceFolders();
    if (workspaces.length === 0) {
        return { uri: Uri.file(process.cwd()), name: path.basename(process.cwd()), index: 0 };
    }
    const activeUri = window.activeTextEditor?.document.uri;
    const active = activeUri && workspace.getWorkspaceFolder(activeUri);
    if (active && isStrictdocProject(active)) {
        return active;
    }
    return workspaces.find(isStrictdocProject) ?? workspaces[0];
}
