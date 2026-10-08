// Trace Graph webview: one hop around the focused requirement (direct parents,
// a card with the statement and code links, direct children), or "home": the
// list of top-level requirements.
// Plain JS, no framework. `layout` is pure so it can be tested with node.
//
// `data` (from the strictdoc/graph request, up = down = 1):
//   focus: string[]  UIDs in focus (usually one; several when following code)
//   nodes: [{uid, title, uri, line, doc, depth, layerTitle, statement,
//            badges: [{kind: 'code'|'test', text, title, zero, missing, missingTitle}],
//            parentCount, childCount, totalCode, totalTests, sections, docKey, order}]
//          `sections`: enclosing [[SECTION]] titles (outermost first) in document `docKey`;
//          rows are grouped by them. `order` is the position in file order.
//          code/test badges and total* count the requirement and all its descendants;
//          `missing` is StrictDoc's tree-map coverage (requirements below still uncovered).
//          `depth` is the longest chain of parents (0 = top level); it picks the colour.
//   edges: [[parentUid, childUid]]
//   code:  [{uid, uri, begin, end, description, forward, kind: 'code'|'test', role}]  empty when hidden
//   rootCount: number of top-level requirements
(function () {
    const DEPTH_COLORS = 5; // strictdoc.depth0..4 in package.json; deeper reuses the last

    /** Returns {choices, focus, parents, children, code}; focus is null if not among the nodes. */
    function layout(data) {
        const byUid = new Map(data.nodes.map((n) => [n.uid, n]));
        const uid = data.focus[0];
        const order = (a, b) => a.order - b.order; // file order, as in the .sdoc documents
        const hop = (from, to) =>
            [...new Set(data.edges.filter((e) => e[from] === uid).map((e) => e[to]))]
                .map((u) => byUid.get(u))
                .filter(Boolean)
                .sort(order);
        return {
            choices: data.focus.length > 1 ? data.focus : [],
            focus: byUid.get(uid) || null,
            parents: hop(1, 0),
            children: hop(0, 1),
            code: (data.code || []).filter((c) => c.uid === uid),
        };
    }

    if (typeof module !== 'undefined') {
        module.exports = { layout };
    }
    if (typeof acquireVsCodeApi !== 'function') {
        return;
    }

    // ---- Webview UI ----
    const vscode = acquireVsCodeApi();
    const post = (msg) => vscode.postMessage(msg);
    let data; // graph for the focused requirement, or undefined when home
    let roots = []; // top-level requirements, shown when home
    let state = { includeCode: false, showWarnings: true, history: [] };
    let list = [];
    let status = '';
    let noIndex = {}; // {needsConfig} or {error, projectDir} when the server has no index

    function h(tag, attrs, ...kids) {
        const el = document.createElement(tag);
        for (const [k, v] of Object.entries(attrs || {})) {
            if (k.startsWith('on')) {
                el.addEventListener(k.slice(2), v);
            } else {
                el.setAttribute(k, v);
            }
        }
        el.append(...kids.filter((k) => k !== undefined && k !== null));
        return el;
    }

    // Contributed theme colours reach webviews as --vscode-<id with dots as dashes>.
    const depthColor = (depth) => `var(--vscode-strictdoc-depth${Math.min(depth || 0, DEPTH_COLORS - 1)})`;

    function stripe(depth) {
        const el = h('span', { class: 'stripe' });
        el.style.setProperty('background', depthColor(depth)); // CSSOM: the CSP forbids style=""
        return el;
    }

    function chips(n) {
        const out = [];
        if (n.parentCount > 1) {
            out.push(h('span', { class: 'chip', title: `${n.parentCount} parents` }, `⇡${n.parentCount}`));
        }
        if (n.childCount > 0) {
            out.push(h('span', { class: 'chip', title: `${n.childCount} children` }, `↓${n.childCount}`));
        }
        return out;
    }

    // "⟨⟩ 218" -> icon on the left, number on the right.
    function pill(b) {
        const [icon, num] = b.text.includes(' ') ? b.text.split(' ') : [b.text, ''];
        return h('span', { class: `pill ${b.kind}${b.zero ? ' zero' : ''}`, title: b.title || '' },
            h('span', {}, icon), num ? h('span', { class: 'num' }, num) : null);
    }
    const pills = (n) => (n.badges || []).map(pill);
    const badge = (n, kind) => (n.badges || []).find((b) => b.kind === kind);

    /**
     * Coverage problems (StrictDoc tree map), shown only when under-covered: a yellow ⚠
     * pill whose tooltip says how many requirements below lack tests / source.
     */
    function problemsPill(n) {
        const problems = [badge(n, 'test'), badge(n, 'code')].filter((b) => b?.missing);
        return problems.length && state.showWarnings !== false
            ? h('span', { class: 'pill problems', title: problems.map((b) => b.missingTitle).join('\n') }, '⚠')
            : h('span');
    }

    const openReq = (n) => post({ type: 'open', uri: n.uri, line: n.line });
    const focusOn = (uid) => () => post({ type: 'focus', uid });

    /**
     * A requirement row. Every row emits the same cells so a group's columns line up:
     * stripe, [marker], title, UID (on hover), [⟨⟩ code, ✓ tests when showing code links],
     * coverage problems (⚠), chips (↗ on hover).
     */
    function reqRow(n, marker, indent = 0) {
        const title = n.title || '';
        // Marker + title share one cell, indented under the row's section header.
        const lead = h('span', { class: 'lead' },
            marker === undefined ? null : h('span', { class: 'marker' }, marker),
            h('span', { class: 'title', title }, title));
        lead.style.setProperty('padding-left', `${indent}px`);
        const links = state.includeCode ? ['code', 'test'].map((k) => (badge(n, k) ? pill(badge(n, k)) : h('span'))) : [];
        return h('div', { class: 'row', title: `${n.uid} ${title}\n${n.layerTitle || n.doc || ''}`,
            onclick: focusOn(n.uid), ondblclick: () => openReq(n) },
            stripe(n.depth),
            lead,
            // Takes no width; the UID appears over the end of the title on hover.
            h('span', { class: 'uid' }, h('span', { class: 'uid-full' }, n.uid)),
            ...links,
            problemsPill(n),
            h('span', { class: 'chips' }, ...chips(n)),
            h('button', { class: 'open', title: 'Open in .sdoc', onclick: (e) => { e.stopPropagation(); openReq(n); } }, '↗'));
    }

    const STEP = 12; // px per section level

    /** Rows arranged as the documents' section tree: {title, rows, kids: Map}, in file order. */
    function sectionTree(nodes) {
        const root = { title: '', rows: [], kids: new Map() };
        for (const n of [...nodes].sort((a, b) => a.order - b.order)) {
            let node = root;
            (n.sections || []).forEach((title, i) => {
                const key = [n.docKey, ...n.sections.slice(0, i + 1)].join('\u0000');
                if (!node.kids.has(key)) {
                    node.kids.set(key, { title, rows: [], kids: new Map() });
                }
                node = node.kids.get(key);
            });
            node.rows.push(n);
        }
        return root;
    }

    /**
     * Requirement rows sharing one grid, so their columns line up, grouped under section
     * and subsection headers: a section's own rows first, then its subsections. A section
     * holding just one subsection (and no rows) merges with it: "Section » Subsection".
     */
    function rows(nodes, marker) {
        const out = [];
        const walk = (node, level) => {
            // Rows sit under their innermost header; the marker already takes one step.
            node.rows.forEach((n) => out.push(reqRow(n, marker, Math.max(0, level - 1) * STEP)));
            for (let kid of node.kids.values()) {
                let title = kid.title;
                while (!kid.rows.length && kid.kids.size === 1) {
                    kid = [...kid.kids.values()][0];
                    title += ` » ${kid.title}`;
                }
                const header = h('div', { class: 'group' }, title);
                header.style.setProperty('padding-left', `${14 + level * STEP}px`);
                out.push(header);
                walk(kid, level + 1);
            }
        };
        walk(sectionTree(nodes), 0);
        return h('div', { class: `rows${state.includeCode ? ' links' : ''}` }, ...out);
    }

    const OBVIOUS_ROLES = ['implementation', 'verification'];

    function codeRow(c) {
        const file = decodeURIComponent(c.uri.split('/').pop());
        const where = c.begin === c.end ? `${c.begin + 1}` : `${c.begin + 1}–${c.end + 1}`;
        const role = c.role && !OBVIOUS_ROLES.includes(c.role.toLowerCase()) ? c.role : null;
        return h('div', { class: `row ${c.kind || 'code'}`, title: `${c.uri}\n${c.description || ''}${c.role ? `\nRole: ${c.role}` : ''}`,
            onclick: () => post({ type: 'open', uri: c.uri, line: c.begin, endLine: c.end }) },
            h('span', { class: 'icon' }, c.kind === 'test' ? '✓' : '⟨⟩'),
            h('span', { class: 'loc' }, `${file}:${where}`),
            h('span', { class: 'title' }, c.description || ''),
            role ? h('span', { class: 'uid' }, role) : null);
    }

    /** The card's links, as "CODE (n)" and "TESTS (m)" blocks. */
    function linkBlocks(links) {
        return [['code', 'CODE'], ['test', 'TESTS']].flatMap(([kind, label]) => {
            const rows = links.filter((c) => (c.kind || 'code') === kind);
            return rows.length ? [h('div', { class: 'card-links' }, `${label} (${rows.length})`), ...rows.map(codeRow)] : [];
        });
    }

    function card(data) {
        const l = layout(data);
        const n = l.focus;
        if (!n) {
            return h('div', { class: 'status' }, 'Nothing in focus');
        }
        const choices = l.choices.length ? h('div', { class: 'card-choices' },
            ...l.choices.map((u) => h('button', { class: u === n.uid ? 'on' : '', onclick: focusOn(u) }, u))) : null;
        const section = (label, nodes, marker) => [
            h('div', { class: 'section' }, `${label} (${nodes.length})`),
            rows(nodes, marker),
        ];
        const counts = [`⇡${n.parentCount} parents`, `↓${n.childCount} children`];
        const box = h('div', { class: 'card' },
            h('div', { class: 'card-head' },
                h('span', { class: 'card-layer' }, n.layerTitle || n.doc || ''),
                h('span', { class: 'uid' }, n.uid),
                h('span', { class: 'spacer' }),
                h('button', { class: 'card-open', title: 'Open in .sdoc', onclick: () => openReq(n) }, '↗')),
            h('div', { class: 'card-title' }, n.title || ''),
            n.statement ? h('div', { class: 'card-statement', title: n.statement }, n.statement) : null,
            h('div', { class: 'card-badges' }, ...pills(n), problemsPill(n), h('span', { class: 'card-counts' }, counts.join(' · '))),
            ...linkBlocks(l.code));
        box.style.setProperty('border-left-color', depthColor(n.depth));
        // A top-level requirement has no parents: offer the other top-level ones instead.
        const above = l.parents.length ? section('PARENTS', l.parents, '↑') : [
            h('div', { class: 'row home-link', title: 'Show all top-level requirements', onclick: goHome },
                h('span', { class: 'marker' }, '⌂'),
                h('span', { class: 'title' }, `All top-level requirements (${data.rootCount})`))];
        return h('div', { class: 'card-view' },
            ...above, choices, box,
            ...section('CHILDREN', l.children, '↓'));
    }

    function home() {
        return h('div', { class: 'card-view' },
            h('div', { class: 'section' }, `TOP-LEVEL REQUIREMENTS (${roots.length})`),
            rows(roots));
    }

    // Header is built once so re-renders don't steal focus from the search box.
    const goHome = () => post({ type: 'home' });
    const back = h('button', { title: 'Back', onclick: () => post({ type: 'back' }) }, '←');
    const homeButton = h('button', { title: 'All top-level requirements', onclick: goHome }, '⌂');
    const datalist = h('datalist', { id: 'reqs' });
    const input = h('input', { type: 'text', list: 'reqs', placeholder: 'Search a requirement to focus…',
        onfocus: () => post({ type: 'search' }),
        onkeydown: (e) => e.key === 'Enter' && search(input.value) });
    const content = h('div');
    document.getElementById('app').append(h('div', { class: 'header' }, back, homeButton, input, datalist), content);

    function search(query) {
        const q = query.trim().toLowerCase();
        const hit =
            list.find((r) => r.uid.toLowerCase() === q) ||
            list.find((r) => r.uid.toLowerCase().startsWith(q)) ||
            list.find((r) => `${r.uid} ${r.title}`.toLowerCase().includes(q));
        if (q && hit) {
            input.value = '';
            post({ type: 'focus', uid: hit.uid });
        }
    }

    /** Why there is no index (no configuration selected, or a failed build), or null. */
    function notice(info) {
        if (!info.needsConfig && !info.error) {
            return null;
        }
        const text = info.needsConfig
            ? 'Select the StrictDoc configuration to load requirements.'
            : `StrictDoc could not build the index for ${info.projectDir}: ${info.error}`;
        return h('div', { class: 'notice' }, h('div', {}, text),
            h('button', { class: 'primary', onclick: () => post({ type: 'selectConfig' }) }, 'Select configuration…'));
    }

    function render() {
        back.disabled = !state.history.length;
        homeButton.disabled = !data;
        datalist.replaceChildren(...list.map((r) => h('option', { value: r.uid }, r.title)));
        const kids = [h('div', { class: 'toolbar' }, h('span', { class: 'spacer' }),
            h('button', { class: state.includeCode ? 'on' : '', title: 'Show code and test links',
                onclick: () => post({ type: 'toggleCode' }) }, '⟨⟩'),
            h('button', { class: state.showWarnings !== false ? 'on' : '', title: 'Show coverage warnings',
                onclick: () => post({ type: 'toggleWarnings' }) }, '⚠'))];
        if (status) {
            kids.push(h('div', { class: 'status' }, status));
        }
        kids.push(data ? card(data) : notice(noIndex) || home());
        content.replaceChildren(...kids);
    }

    window.addEventListener('message', (e) => {
        const msg = e.data;
        if (msg.state) {
            state = msg.state;
        }
        if (msg.type === 'graph') {
            data = msg.data;
            status = '';
        } else if (msg.type === 'home') {
            data = undefined;
            roots = msg.nodes;
            noIndex = msg;
            status = '';
        } else if (msg.type === 'list') {
            list = msg.items;
        } else if (msg.type === 'status') {
            status = msg.text;
        }
        render();
    });
    render();
    post({ type: 'ready' });
})();
