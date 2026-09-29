// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

import { execFile } from 'child_process';
import * as fsapi from 'fs-extra';
import { Disposable, env, LogOutputChannel, Uri, window, workspace } from 'vscode';
import { State } from 'vscode-languageclient';
import {
    LanguageClient,
    LanguageClientOptions,
    RevealOutputChannelOn,
    ServerOptions,
} from 'vscode-languageclient/node';
import { DEBUG_SERVER_SCRIPT_PATH, SERVER_SCRIPT_PATH } from './constants';
import { traceError, traceInfo, traceVerbose } from './log/logging';
import { getWorkspaceSettings, ISettings } from './settings';
import { getLSClientTraceLevel, getProjectRoot } from './utilities';
import { getConfiguration } from './vscodeapi';

export type IInitOptions = { settings: ISettings[]; cacheDir: string };

const WATCHED_FILES = '**/{*.sdoc,*.py,*.c,*.h,*.cc,*.cpp,*.hh,*.hpp,*.rs,*.robot,strictdoc.toml}';

const MIN_STRICTDOC = '0.30.1';

// Prints the installed strictdoc version without importing it (importing takes seconds).
const VERSION_SCRIPT =
    'import sys, importlib.metadata as m\n' +
    'if sys.version_info < (3, 10): sys.exit("Python 3.10 or newer is required")\n' +
    'try: print(m.version("strictdoc"))\n' +
    'except m.PackageNotFoundError: pass';

function isOlder(version: string, minimum: string): boolean {
    const a = version.split('.').map((x) => parseInt(x, 10) || 0);
    const b = minimum.split('.').map((x) => parseInt(x, 10) || 0);
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
        if ((a[i] ?? 0) !== (b[i] ?? 0)) {
            return (a[i] ?? 0) < (b[i] ?? 0);
        }
    }
    return false;
}

// Returns an error message if the interpreter cannot run the server.
function checkInterpreter(interpreter: string[]): Promise<string | undefined> {
    const python = interpreter.join(' ');
    const hint = `Install it with: uv pip install --python ${python} "strictdoc>=${MIN_STRICTDOC}", then run "StrictDoc: Restart Server".`;
    return new Promise((resolve) => {
        execFile(interpreter[0], [...interpreter.slice(1), '-c', VERSION_SCRIPT], (err, stdout, stderr) => {
            const version = stdout.trim();
            if (err) {
                resolve(`Cannot run ${python}: ${stderr.trim() || err.message}`);
            } else if (!version) {
                resolve(`strictdoc is not installed in ${python}. ${hint}`);
            } else if (isOlder(version, MIN_STRICTDOC)) {
                resolve(`strictdoc ${version} in ${python} is too old (need >= ${MIN_STRICTDOC}). ${hint}`);
            } else {
                resolve(undefined);
            }
        });
    });
}

async function createServer(
    settings: ISettings,
    serverId: string,
    serverName: string,
    outputChannel: LogOutputChannel,
    initializationOptions: IInitOptions,
): Promise<LanguageClient> {
    const command = settings.interpreter[0];
    const cwd = Uri.parse(settings.workspace).fsPath;

    // Debug the server only when both USE_DEBUGPY and DEBUGPY_PATH are set.
    const newEnv = { ...process.env };
    const isDebugScript = await fsapi.pathExists(DEBUG_SERVER_SCRIPT_PATH);
    if (!newEnv.USE_DEBUGPY || !newEnv.DEBUGPY_PATH) {
        newEnv.USE_DEBUGPY = 'False';
    }

    // Set import strategy
    newEnv.LS_IMPORT_STRATEGY = settings.importStrategy;

    // Set notification type
    newEnv.LS_SHOW_NOTIFICATION = settings.showNotifications;

    const args =
        newEnv.USE_DEBUGPY === 'False' || !isDebugScript
            ? settings.interpreter.slice(1).concat([SERVER_SCRIPT_PATH])
            : settings.interpreter.slice(1).concat([DEBUG_SERVER_SCRIPT_PATH]);
    traceInfo(`Server run command: ${[command, ...args].join(' ')}`);

    const serverOptions: ServerOptions = {
        command,
        args,
        options: { cwd, env: newEnv },
    };

    const watcher = workspace.createFileSystemWatcher(WATCHED_FILES);
    _disposables.push(watcher);

    // Options to control the language client
    const clientOptions: LanguageClientOptions = {
        // .sdoc files plus any code file that may carry @relation markers
        documentSelector: [{ scheme: 'file' }],
        synchronize: { fileEvents: watcher },
        middleware: {
            provideCodeLenses: (document, token, next) =>
                getConfiguration(serverId, document.uri).get<boolean>('codeLens.enabled', true)
                    ? next(document, token)
                    : [],
        },
        outputChannel: outputChannel,
        traceOutputChannel: outputChannel,
        revealOutputChannelOn: RevealOutputChannelOn.Never,
        initializationOptions,
    };

    return new LanguageClient(serverId, serverName, serverOptions, clientOptions);
}

let _disposables: Disposable[] = [];
export async function restartServer(
    serverId: string,
    serverName: string,
    outputChannel: LogOutputChannel,
    cacheDir: string,
    lsClient?: LanguageClient,
): Promise<LanguageClient | undefined> {
    if (lsClient) {
        traceInfo(`Server: Stop requested`);
        try {
            await lsClient.stop();
        } catch (ex) {
            traceError(`Server: Stop failed: ${ex}`);
        }
    }
    _disposables.forEach((d) => d.dispose());
    _disposables = [];
    const projectRoot = await getProjectRoot();
    traceInfo(`StrictDoc project folder: ${projectRoot.uri.fsPath}`);
    const workspaceSetting = await getWorkspaceSettings(serverId, projectRoot, true);

    const message = await checkInterpreter(workspaceSetting.interpreter);
    if (message) {
        traceError(message);
        window.showErrorMessage(message);
        return undefined;
    }

    await fsapi.ensureDir(cacheDir);
    const newLSClient = await createServer(workspaceSetting, serverId, serverName, outputChannel, {
        settings: [workspaceSetting],
        cacheDir,
    });
    traceInfo(`Server: Start requested.`);
    _disposables.push(
        newLSClient.onDidChangeState((e) => {
            switch (e.newState) {
                case State.Stopped:
                    traceVerbose(`Server State: Stopped`);
                    break;
                case State.Starting:
                    traceVerbose(`Server State: Starting`);
                    break;
                case State.Running:
                    traceVerbose(`Server State: Running`);
                    break;
            }
        }),
    );
    try {
        await newLSClient.start();
    } catch (ex) {
        traceError(`Server: Start failed: ${ex}`);
        return undefined;
    }

    const level = getLSClientTraceLevel(outputChannel.logLevel, env.logLevel);
    await newLSClient.setTrace(level);
    return newLSClient;
}
