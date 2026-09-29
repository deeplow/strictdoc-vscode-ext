// Finds a Python interpreter without the Python extension, whose interpreter
// discovery is slow (and broken in some VS Code OSS builds).

import * as fs from 'fs';
import * as path from 'path';
import { WorkspaceFolder } from 'vscode';
import { traceLog } from './log/logging';

const isWindows = process.platform === 'win32';

// The project's .venv if it exists, else python3 from PATH.
export function findInterpreter(workspace: WorkspaceFolder): string[] {
    const venv = path.join(workspace.uri.fsPath, '.venv', ...(isWindows ? ['Scripts', 'python.exe'] : ['bin', 'python']));
    const python = fs.existsSync(venv) ? venv : isWindows ? 'python' : 'python3';
    traceLog(`Using Python interpreter: ${python}`);
    return [python];
}
