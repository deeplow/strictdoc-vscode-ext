// Requirements view: documents -> requirements, from the `strictdoc/requirements` request.

import * as vscode from 'vscode';
import { LanguageClient } from 'vscode-languageclient/node';

interface RequirementInfo {
    uid: string;
    title: string;
    depth: number;
    untraced: boolean;
}

interface DocInfo {
    title: string;
    path: string;
    requirements: RequirementInfo[];
}

type Node = DocInfo | RequirementInfo;

export class RequirementsTreeProvider implements vscode.TreeDataProvider<Node> {
    private readonly changed = new vscode.EventEmitter<void>();
    readonly onDidChangeTreeData = this.changed.event;
    private untracedOnly = false;

    constructor(private readonly getClient: () => LanguageClient | undefined) {}

    refresh(): void {
        this.changed.fire();
    }

    toggleUntracedFilter(): void {
        this.untracedOnly = !this.untracedOnly;
        this.refresh();
    }

    getTreeItem(node: Node): vscode.TreeItem {
        if ('requirements' in node) {
            const item = new vscode.TreeItem(node.title, vscode.TreeItemCollapsibleState.Expanded);
            item.iconPath = new vscode.ThemeIcon('book');
            item.tooltip = node.path;
            return item;
        }
        const item = new vscode.TreeItem(node.title || node.uid);
        item.description = node.uid;
        item.iconPath = node.untraced
            ? new vscode.ThemeIcon('warning', new vscode.ThemeColor('list.warningForeground'))
            : new vscode.ThemeIcon('circle-filled', new vscode.ThemeColor(`strictdoc.depth${Math.min(node.depth, 4)}`));
        item.tooltip = node.untraced ? 'Not traced to code' : undefined;
        item.command = { command: 'strictdoc.focusRequirement', title: 'Focus', arguments: [node.uid] };
        return item;
    }

    async getChildren(node?: Node): Promise<Node[]> {
        if (node) {
            return 'requirements' in node ? node.requirements : [];
        }
        const client = this.getClient();
        if (!client?.isRunning()) {
            return [];
        }
        const result = await client.sendRequest<{ docs: DocInfo[] }>('strictdoc/requirements', {});
        if (!this.untracedOnly) {
            return result.docs;
        }
        return result.docs
            .map((d) => ({ ...d, requirements: d.requirements.filter((r) => r.untraced) }))
            .filter((d) => d.requirements.length > 0);
    }
}
