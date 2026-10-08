// Coverage webview: StrictDoc's requirements coverage with source / tests (tree map).
// `data`: {project, roots: [{uid, title, depth, coverage}], focus}, where each
// coverage is {requirements, withSource, withTests}. Plain JS, no framework.
(function () {
    const vscode = acquireVsCodeApi();
    const DEPTH_COLORS = 5; // strictdoc.depth0..4 in package.json

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

    /** "Source coverage ▓▓▓░ 24/27 · 3 missing" (percentage when complete). */
    function bar(label, covered, total) {
        const pct = total ? Math.round((100 * covered) / total) : 100;
        const missing = total - covered;
        const track = h('span', { class: 'bar' }, h('span', { class: 'fill' }));
        track.firstChild.style.setProperty('width', `${pct}%`); // CSSOM: the CSP forbids style=""
        return h('div', { class: `coverage${missing ? ' partial' : ''}`,
            title: `${covered} of ${total} requirements covered (StrictDoc tree-map coverage)` },
            h('span', { class: 'label' }, label), track,
            h('span', { class: 'num' }, missing ? `${covered}/${total} · ${missing} missing` : `${covered}/${total} · ${pct}%`));
    }

    const bars = (c) => [bar('Source coverage', c.withSource, c.requirements), bar('Test coverage', c.withTests, c.requirements)];

    /** A requirement heading (colour stripe + title, click focuses it in the Trace Graph) and its bars. */
    function block(r) {
        const stripe = h('span', { class: 'stripe' });
        stripe.style.setProperty('background', `var(--vscode-strictdoc-depth${Math.min(r.depth, DEPTH_COLORS - 1)})`);
        return h('div', { class: 'coverage-block' },
            h('div', { class: 'row', title: `${r.uid} ${r.title}`, onclick: () => vscode.postMessage({ type: 'focus', uid: r.uid }) },
                stripe, h('span', { class: 'title' }, r.title || r.uid)),
            h('div', { class: 'summary' }, ...bars(r.coverage)));
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
            h('button', { class: 'primary', onclick: () => vscode.postMessage({ type: 'selectConfig' }) }, 'Select configuration…'));
    }

    const app = document.getElementById('app');

    function render(data) {
        if (!data.project) {
            app.replaceChildren(notice(data) || h('div', { class: 'status' }, 'Waiting for the StrictDoc index…'));
            return;
        }
        app.replaceChildren(
            h('div', { class: 'section' }, 'PROJECT'),
            h('div', { class: 'summary' }, ...bars(data.project)),
            data.focus ? h('div', { class: 'section' }, 'FOCUSED IN TRACE GRAPH') : null,
            data.focus ? block(data.focus) : null,
            h('div', { class: 'section' }, `TOP-LEVEL REQUIREMENTS (${data.roots.length})`),
            ...data.roots.map(block));
    }

    window.addEventListener('message', (e) => e.data.type === 'coverage' && render(e.data.data));
    render({ project: null });
    vscode.postMessage({ type: 'ready' });
})();
