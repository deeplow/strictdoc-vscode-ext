// Finding the StrictDoc configurations of a workspace folder.
// Git lists the files (honouring .gitignore and leaving submodule contents out); outside git,
// VS Code's own file search (and its files.exclude / search.exclude) is used.

import { execFile } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import { ConfigurationTarget, RelativePattern, window, workspace, WorkspaceFolder } from 'vscode';

export const CONFIG_NAMES = ['strictdoc_config.py', 'strictdoc.toml'];

/** Folders (relative, '' for the root) holding a StrictDoc config, shallowest first. */
export function configDirs(relativeFiles: string[]): string[] {
    const dirs = new Set(
        relativeFiles
            .map((f) => f.replace(/\\/g, '/'))
            .filter((f) => CONFIG_NAMES.includes(path.posix.basename(f)))
            .map((f) => path.posix.dirname(f).replace(/^\.$/, '')),
    );
    const depth = (d: string) => (d ? d.split('/').length : 0);
    return [...dirs].sort((a, b) => depth(a) - depth(b) || a.localeCompare(b));
}

function gitFiles(cwd: string): Promise<string[]> {
    return new Promise((resolve, reject) =>
        execFile(
            'git',
            ['ls-files', '--cached', '--others', '--exclude-standard'],
            { cwd, maxBuffer: 64 * 1024 * 1024 },
            (err, stdout) => (err ? reject(err) : resolve(stdout.split('\n').filter(Boolean))),
        ),
    );
}

/** Absolute folders with a StrictDoc config in a workspace folder, shallowest first. */
export async function findConfigDirs(folder: WorkspaceFolder): Promise<string[]> {
    const root = folder.uri.fsPath;
    let files: string[];
    try {
        files = await gitFiles(root);
    } catch {
        const found = await workspace.findFiles(new RelativePattern(folder, `**/{${CONFIG_NAMES.join(',')}}`));
        files = found.map((uri) => path.relative(root, uri.fsPath));
    }
    return configDirs(files).map((dir) => path.join(root, dir));
}

/** "StrictDoc: Select Project Configuration…": saves the choice as strictdoc.projectPath. */
export async function selectConfig(folder: WorkspaceFolder): Promise<void> {
    const dirs = await findConfigDirs(folder);
    const config = workspace.getConfiguration('strictdoc', folder.uri);
    const current = config.inspect<string>('projectPath')?.workspaceFolderValue;
    const items = dirs.map((dir) => {
        const rel = path.relative(folder.uri.fsPath, dir).replace(/\\/g, '/');
        const value = rel ? `\${workspaceFolder}/${rel}` : '${workspaceFolder}';
        const file = CONFIG_NAMES.find((name) => fs.existsSync(path.join(dir, name)));
        return { label: `${value === current ? '✓ ' : ''}${rel || '.'}`, detail: file, value };
    });
    if (!items.length) {
        items.push({ label: 'Use workspace root (StrictDoc defaults)', detail: undefined, value: '${workspaceFolder}' });
    }
    const placeHolder = dirs.length
        ? `StrictDoc configurations in ${folder.name}`
        : `No StrictDoc configuration found in ${folder.name}`;
    const choice = await window.showQuickPick(items, { placeHolder });
    if (choice) {
        // The configuration-change handler restarts the server.
        await config.update('projectPath', choice.value, ConfigurationTarget.WorkspaceFolder);
    }
}
