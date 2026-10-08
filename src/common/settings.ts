// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import { commands, ConfigurationChangeEvent, ConfigurationScope, window, WorkspaceConfiguration, WorkspaceFolder } from 'vscode';
import { findInterpreter } from './interpreter';
import { traceLog } from './log/logging';
import { findConfigDirs } from './strictdocConfigs';
import { getConfiguration, getWorkspaceFolders } from './vscodeapi';

export interface ISettings {
    workspace: string;
    projectPath: string;
    needsConfig: boolean; // no configuration chosen: the server does not build
    interpreter: string[];
    importStrategy: string;
    showNotifications: string;
}

function resolveVariables(value: string[], workspace?: WorkspaceFolder): string[] {
    const substitutions = new Map<string, string>();
    const home = process.env.HOME || process.env.USERPROFILE;
    if (home) {
        substitutions.set('${userHome}', home);
    }
    if (workspace) {
        substitutions.set('${workspaceFolder}', workspace.uri.fsPath);
    }
    substitutions.set('${cwd}', process.cwd());
    getWorkspaceFolders().forEach((w) => {
        substitutions.set('${workspaceFolder:' + w.name + '}', w.uri.fsPath);
    });

    return value.map((s) => {
        for (const [key, value] of substitutions) {
            s = s.replace(key, value);
        }
        return s;
    });
}

export function getInterpreterFromSetting(namespace: string, scope?: ConfigurationScope) {
    const config = getConfiguration(namespace, scope);
    return config.get<string[]>('interpreter');
}

/** The project folder: the projectPath setting if set, else the only configuration found. */
async function getProject(
    config: WorkspaceConfiguration,
    workspace: WorkspaceFolder,
): Promise<{ projectPath: string; needsConfig: boolean }> {
    const setting = config.inspect<string>('projectPath');
    const explicit = setting?.workspaceFolderValue ?? setting?.workspaceValue ?? setting?.globalValue;
    if (explicit !== undefined) {
        return { projectPath: resolveVariables([explicit], workspace)[0], needsConfig: false };
    }
    const dirs = await findConfigDirs(workspace);
    if (dirs.length === 1) {
        traceLog(`Using StrictDoc project ${dirs[0]} (auto-detected)`);
        return { projectPath: dirs[0], needsConfig: false };
    }
    if (dirs.length > 1) {
        void window
            .showInformationMessage(
                'Several StrictDoc configurations were found. Which project should StrictDoc Trace use?',
                'Select…',
            )
            .then((choice) => choice && commands.executeCommand('strictdoc.selectConfig'));
    }
    return { projectPath: workspace.uri.fsPath, needsConfig: true };
}

export async function getWorkspaceSettings(
    namespace: string,
    workspace: WorkspaceFolder,
    includeInterpreter?: boolean,
): Promise<ISettings> {
    const config = getConfiguration(namespace, workspace.uri);

    let interpreter: string[] = [];
    if (includeInterpreter) {
        interpreter = getInterpreterFromSetting(namespace, workspace) ?? [];
        if (interpreter.length === 0) {
            interpreter = findInterpreter(workspace);
        }
    }

    const workspaceSetting = {
        workspace: workspace.uri.toString(),
        ...(await getProject(config, workspace)),
        interpreter: resolveVariables(interpreter, workspace),
        importStrategy: config.get<string>(`importStrategy`) ?? 'useBundled',
        showNotifications: config.get<string>(`showNotifications`) ?? 'off',
    };
    return workspaceSetting;
}

export function checkIfConfigurationChanged(e: ConfigurationChangeEvent, namespace: string): boolean {
    const settings = [
        `${namespace}.projectPath`,
        `${namespace}.codeLens.enabled`,
        `${namespace}.interpreter`,
        `${namespace}.importStrategy`,
        `${namespace}.showNotifications`,
    ];
    const changed = settings.map((s) => e.affectsConfiguration(s));
    return changed.includes(true);
}
