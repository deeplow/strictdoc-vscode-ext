// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import {
    commands,
    ConfigurationScope,
    Disposable,
    LogOutputChannel,
    window,
    workspace,
    WorkspaceConfiguration,
    WorkspaceFolder,
} from 'vscode';

export function createOutputChannel(name: string): LogOutputChannel {
    return window.createOutputChannel(name, { log: true });
}

export function getConfiguration(config: string, scope?: ConfigurationScope): WorkspaceConfiguration {
    return workspace.getConfiguration(config, scope);
}

export function registerCommand(command: string, callback: (...args: any[]) => any, thisArg?: any): Disposable {
    return commands.registerCommand(command, callback, thisArg);
}

export const { onDidChangeConfiguration } = workspace;

export function getWorkspaceFolders(): readonly WorkspaceFolder[] {
    return workspace.workspaceFolders ?? [];
}
