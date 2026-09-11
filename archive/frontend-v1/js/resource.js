/* ============================================================
   数据资源模块：目录模式 + 图谱模式（零依赖 Canvas 力导向图）
   ============================================================ */

let RES = { nodes: [], edges: [], loaded: false };
let currentRTab = 'catalog';

async function initResourceModule() {
    await loadSchemaGraph();
    renderCatalog();
    if (currentRTab === 'graph') initGraph();
}

async function loadSchemaGraph() {
    if (RES.loaded) return;
    try {
        const r = await fetch('/api/schema-graph');
        const d = await r.json();
        if (d.success) {
            RES.nodes = d.nodes;
            RES.edges = d.edges;
            RES.loaded = true;
            initGraphData();
        }
    } catch (e) {
        console.error('加载数据资源图谱失败:', e);
    }
}

function switchResourceTab(tab) {
    currentRTab = tab;
    document.querySelectorAll('[data-rtab]').forEach(el => el.classList.toggle('active', el.dataset.rtab === tab));
    document.querySelectorAll('#rtab-catalog, #rtab-graph, #rtab-code, #rtab-admin').forEach(el => el.classList.toggle('active', el.id === 'rtab-' + tab));
    if (tab === 'catalog') renderCatalog();
    else if (tab === 'graph') initGraph();
    else if (tab === 'code') loadCodeCatalog();
    else if (tab === 'admin') initResAdmin();
}

/* ==================== 目录模式 ==================== */

function renderCatalog() {
    const grid = document.getElementById('catalog-grid');
    if (!grid) return;
    const kw = (document.getElementById('res-search').value || '').trim().toLowerCase();
    const layer = document.getElementById('res-layer').value;

    let nodes = RES.nodes;
    if (layer) nodes = nodes.filter(n => n.layer === layer);
    if (kw) nodes = nodes.filter(n =>
        n.name.toLowerCase().includes(kw) || (n.comment || '').toLowerCase().includes(kw));

    const totalCols = nodes.reduce((s, n) => s + (n.column_count || 0), 0);
    document.getElementById('res-count').textContent = `共 ${nodes.length} 张表 · ${totalCols} 个字段`;

    if (!nodes.length) {
        grid.innerHTML = '<p class="placeholder">没有匹配的数据表</p>';
        return;
    }

    grid.innerHTML = nodes.map(n => {
        const layerBadge = n.layer === 'dim'
            ? '<span class="chip chip-hit">dim</span>'
            : '<span class="chip">dwd</span>';
        const pkText = (n.pk && n.pk.length) ? `PK: ${n.pk.join(', ')}` : '—';
        return `
        <div class="catalog-card" onclick="showTableDetail('${n.name}')">
            <div class="catalog-card-title">${_escapeHtml(n.comment || n.name)}</div>
            <div class="catalog-card-name">${n.name}</div>
            <div class="chip-list" style="margin:8px 0 6px">
                ${layerBadge}
                <span class="chip">${n.column_count} 字段</span>
                <span class="chip">${n.rel_count} 关联</span>
            </div>
            <div class="catalog-card-pk">${_escapeHtml(pkText)}</div>
        </div>`;
    }).join('');
}

/* ==================== 表详情抽屉 ==================== */

async function showTableDetail(table) {
    const drawer = document.getElementById('table-detail-drawer');
    drawer.style.display = 'flex';
    document.getElementById('drawer-comment').textContent = '加载中…';
    document.getElementById('drawer-table').textContent = table;
    document.getElementById('drawer-body').innerHTML = '';

    try {
        const r = await fetch(`/api/table-columns?table=${encodeURIComponent(table)}`);
        const d = await r.json();
        if (!d.success) {
            document.getElementById('drawer-comment').textContent = '加载失败';
            return;
        }
        document.getElementById('drawer-comment').textContent = d.comment || table;
        document.getElementById('drawer-table').textContent = table;

        const relRows = (d.relationships || []).map(rel => `
            <div class="drawer-rel" onclick="showTableDetail('${rel.table}')">
                <div class="drawer-rel-table">${_escapeHtml(rel.comment || rel.table)} <span class="drawer-rel-name">${rel.table}</span></div>
                <div class="drawer-rel-cond">${rel.join_conditions.map(_escapeHtml).join('<br>')}</div>
            </div>`).join('');

        const colRows = (d.columns || []).map(c => `
            <tr>
                <td class="mono">${c.name}${c.pk ? ' <span class="chip chip-hit" style="font-size:10px">PK</span>' : ''}</td>
                <td class="mono" style="color:var(--faint)">${c.type || ''}</td>
                <td>${_escapeHtml(c.comment || '')}</td>
            </tr>`).join('');

        document.getElementById('drawer-body').innerHTML = `
            <div class="cp-block">
                <div class="cp-label">关联关系（${(d.relationships || []).length}）</div>
                ${relRows || '<div class="qa-lib-date">无</div>'}
            </div>
            <div class="cp-block">
                <div class="cp-label">字段（${(d.columns || []).length}）</div>
                <table class="data-table">
                    <thead><tr><th style="width:34%">字段</th><th style="width:20%">类型</th><th>注释</th></tr></thead>
                    <tbody>${colRows}</tbody>
                </table>
            </div>
        `;
    } catch (e) {
        document.getElementById('drawer-comment').textContent = '加载失败: ' + e.message;
    }
}

function closeTableDetail() {
    document.getElementById('table-detail-drawer').style.display = 'none';
}

/* ==================== 图谱模式 ==================== */

const LAYER_COLORS = { dim: '#5aa2ff', dwd: '#ff7a72', other: '#9b9086' };
let G = null;   // 图谱状态

function initGraphData() {
    const nm = {};
    RES.nodes.forEach((n, i) => {
        const angle = (i / RES.nodes.length) * Math.PI * 2;
        const radius = 240 + (i % 5) * 30;
        nm[n.name] = {
            ...n,
            x: Math.cos(angle) * radius,
            y: Math.sin(angle) * radius,
            vx: 0, vy: 0,
            r: 10 + Math.min(12, (n.rel_count || 0) * 1.6)
        };
    });
    const adj = {};
    RES.edges.forEach(e => {
        // 悬空边防护（F3.1 对等修复，同新版 b29a418）：端点不在 nm 的边不进邻接表——
        // 后端「表清单」与「关系」不同步时会出现（如已标准化改名的旧表名），否则 physics
        // 取值 undefined 会中断渲染循环（本缺陷现存，当前数据下图谱页白屏）
        if (!nm[e.from] || !nm[e.to]) return;
        (adj[e.from] = adj[e.from] || []).push(e.to);
        (adj[e.to] = adj[e.to] || []).push(e.from);
    });
    G = {
        nm, adj,
        edges: RES.edges,
        sel: null,          // 选中节点名
        pathNodes: new Set(),
        pathEdges: new Set(),
        scale: 1, ox: 0, oy: 0,
        dragNode: null, panStart: null, hoverNode: null, hoverEdge: null,
        inited: false
    };
    // 搜索候选
    const dl = document.getElementById('graph-nl');
    if (dl) {
        dl.innerHTML = RES.nodes.map(n =>
            `<option value="${n.name}">${_escapeHtml(n.comment || '')}</option>`).join('');
    }
}

function initGraph() {
    if (!G) initGraphData();
    const cv = document.getElementById('graph-canvas');
    const wrap = cv.parentElement;
    cv.width = wrap.clientWidth;
    cv.height = 560;
    if (!G.inited) {
        G.inited = true;
        bindGraphEvents(cv);
        (function loop() {
            for (let i = 0; i < 4; i++) graphPhysics();
            graphDraw();
            requestAnimationFrame(loop);
        })();
    } else {
        graphDraw();
    }
}

function graphPhysics() {
    const rep = 6000, att = 0.02, damp = 0.86;
    const names = Object.keys(G.nm);
    for (const aName of names) {
        const a = G.nm[aName];
        if (a === G.dragNode) continue;
        let fx = 0, fy = 0;
        for (const bName of names) {
            if (aName === bName) continue;
            const b = G.nm[bName];
            const dx = a.x - b.x, dy = a.y - b.y;
            const d = Math.max(20, Math.hypot(dx, dy));
            const f = rep / (d * d);
            fx += dx / d * f; fy += dy / d * f;
        }
        for (const nb of (G.adj[aName] || [])) {
            const b = G.nm[nb];
            if (!b) continue;
            fx -= (a.x - b.x) * att;
            fy -= (a.y - b.y) * att;
        }
        a.vx = a.vx * damp + fx * 0.01;
        a.vy = a.vy * damp + fy * 0.01;
        a.x += a.vx; a.y += a.vy;
    }
}

function gw2s(wx, wy) {
    const cv = document.getElementById('graph-canvas');
    return [(wx + G.ox) * G.scale + cv.width / 2, (wy + G.oy) * G.scale + cv.height / 2];
}
function gs2w(sx, sy) {
    const cv = document.getElementById('graph-canvas');
    return [(sx - cv.width / 2) / G.scale - G.ox, (sy - cv.height / 2) / G.scale - G.oy];
}

function graphDraw() {
    const cv = document.getElementById('graph-canvas');
    if (!cv || !G) return;
    const ctx = cv.getContext('2d');
    ctx.clearRect(0, 0, cv.width, cv.height);

    const hasSel = !!G.sel;
    const neighborSet = new Set();
    if (hasSel) {
        neighborSet.add(G.sel);
        (G.adj[G.sel] || []).forEach(n => neighborSet.add(n));
    }
    const hasPath = G.pathNodes.size > 0;

    const edgeAlpha = (e) => {
        if (hasPath) return G.pathEdges.has(e) ? 1 : 0.06;
        if (hasSel) return (neighborSet.has(e.from) && neighborSet.has(e.to)) ? 0.85 : 0.06;
        return 0.45;
    };

    // 边
    for (const e of G.edges) {
        const a = G.nm[e.from], b = G.nm[e.to];
        if (!a || !b) continue;
        const [x1, y1] = gw2s(a.x, a.y);
        const [x2, y2] = gw2s(b.x, b.y);
        const alpha = edgeAlpha(e);
        const isHot = G.hoverEdge === e || (hasPath && G.pathEdges.has(e));
        ctx.strokeStyle = isHot ? '#ff7a1a' : `rgba(155, 144, 134, ${alpha})`;
        ctx.lineWidth = isHot ? 2 : 1;
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();
    }

    // 节点
    for (const name of Object.keys(G.nm)) {
        const n = G.nm[name];
        const [x, y] = gw2s(n.x, n.y);
        const r = n.r * G.scale;
        let alpha = 1;
        if (hasPath) alpha = G.pathNodes.has(name) ? 1 : 0.15;
        else if (hasSel) alpha = neighborSet.has(name) ? 1 : 0.15;

        const isSel = G.sel === name;
        const isHover = G.hoverNode === n;
        ctx.globalAlpha = alpha;
        ctx.fillStyle = LAYER_COLORS[n.layer] || LAYER_COLORS.other;
        ctx.beginPath();
        ctx.arc(x, y, Math.max(4, r), 0, Math.PI * 2);
        ctx.fill();
        if (isSel || isHover || (hasPath && G.pathNodes.has(name))) {
            ctx.strokeStyle = '#ff7a1a';
            ctx.lineWidth = 2.5;
            ctx.stroke();
        }
        // 标签：注释主标签 + 表名副标签
        ctx.globalAlpha = Math.min(1, alpha + 0.15);
        ctx.fillStyle = '#f2ece4';
        ctx.font = '600 11px "PingFang SC", "Microsoft YaHei", sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(n.comment || name, x, y + Math.max(4, r) + 14);
        ctx.fillStyle = '#9b9086';
        ctx.font = '9px "JetBrains Mono", Menlo, monospace';
        ctx.fillText(name, x, y + Math.max(4, r) + 26);
        ctx.globalAlpha = 1;
    }
}

/* ---------- 图谱交互 ---------- */

function graphPickNode(wx, wy) {
    const names = Object.keys(G.nm).reverse();
    for (const name of names) {
        const n = G.nm[name];
        const dx = wx - n.x, dy = wy - n.y;
        if (dx * dx + dy * dy < Math.pow(n.r * 1.6, 2)) return n;
    }
    return null;
}

function graphPickEdge(wx, wy) {
    for (const e of G.edges) {
        const a = G.nm[e.from], b = G.nm[e.to];
        if (!a || !b) continue;
        const d = distToSegment(wx, wy, a.x, a.y, b.x, b.y);
        if (d < 5 / G.scale + 2) return e;
    }
    return null;
}

function distToSegment(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1, dy = y2 - y1;
    const len2 = dx * dx + dy * dy || 1;
    let t = ((px - x1) * dx + (py - y1) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

function bindGraphEvents(cv) {
    const tip = document.getElementById('graph-tip');

    cv.addEventListener('mousedown', (ev) => {
        const rect = cv.getBoundingClientRect();
        const [wx, wy] = gs2w(ev.clientX - rect.left, ev.clientY - rect.top);
        const n = graphPickNode(wx, wy);
        if (n) {
            G.dragNode = n;
        } else {
            G.panStart = [ev.clientX, ev.clientY];
            // 点空白：清除选中
            G.sel = null;
            G.pathNodes.clear();
            G.pathEdges.clear();
            document.getElementById('graph-res').textContent = '';
            document.getElementById('graph-sql').style.display = 'none';
        }
    });

    cv.addEventListener('mousemove', (ev) => {
        const rect = cv.getBoundingClientRect();
        const [wx, wy] = gs2w(ev.clientX - rect.left, ev.clientY - rect.top);
        if (G.dragNode) {
            G.dragNode.x = wx; G.dragNode.y = wy;
            return;
        }
        if (G.panStart && ev.buttons) {
            G.ox += (ev.clientX - G.panStart[0]) / G.scale;
            G.oy += (ev.clientY - G.panStart[1]) / G.scale;
            G.panStart = [ev.clientX, ev.clientY];
            return;
        }
        // 悬停：节点优先，其次边
        const n = graphPickNode(wx, wy);
        G.hoverNode = n;
        G.hoverEdge = n ? null : graphPickEdge(wx, wy);
        if (n) {
            cv.style.cursor = 'pointer';
            tip.style.display = 'block';
            tip.style.left = (ev.clientX + 14) + 'px';
            tip.style.top = (ev.clientY - 10) + 'px';
            tip.innerHTML = `<b>${_escapeHtml(n.comment || n.name)}</b><br>` +
                `<span style="color:#9b9086;font-family:monospace">${n.name}</span><br>` +
                `字段 ${n.column_count} · 关联 ${n.rel_count}`;
        } else if (G.hoverEdge) {
            cv.style.cursor = 'pointer';
            tip.style.display = 'block';
            tip.style.left = (ev.clientX + 14) + 'px';
            tip.style.top = (ev.clientY - 10) + 'px';
            tip.innerHTML = `<b>JOIN 条件</b><br>` +
                G.hoverEdge.join_conditions.map(c => `<span style="font-family:monospace">${_escapeHtml(c)}</span>`).join('<br>');
        } else {
            cv.style.cursor = 'grab';
            tip.style.display = 'none';
        }
    });

    cv.addEventListener('mouseup', (ev) => {
        if (G.dragNode) {
            // 单击选中：高亮相邻 + 打开详情
            G.sel = G.dragNode.name;
            showTableDetail(G.dragNode.name);
            G.dragNode = null;
        }
        G.panStart = null;
    });

    cv.addEventListener('dblclick', (ev) => {
        const rect = cv.getBoundingClientRect();
        const [wx, wy] = gs2w(ev.clientX - rect.left, ev.clientY - rect.top);
        const n = graphPickNode(wx, wy);
        if (n) {
            G.ox = -n.x; G.oy = -n.y;
            G.scale = Math.min(2.2, G.scale * 1.6);
        }
    });

    cv.addEventListener('wheel', (ev) => {
        ev.preventDefault();
        const ns = G.scale * (ev.deltaY > 0 ? 0.9 : 1.1);
        G.scale = Math.max(0.2, Math.min(4, ns));
    }, { passive: false });

    cv.addEventListener('mouseleave', () => {
        document.getElementById('graph-tip').style.display = 'none';
        G.hoverNode = null; G.hoverEdge = null;
    });
}

/* ---------- 图谱工具条 ---------- */

function graphSearch() {
    const kw = (document.getElementById('graph-search').value || '').trim();
    if (!kw) return;
    const n = RES.nodes.find(x => x.name === kw) ||
              RES.nodes.find(x => (x.comment || '').includes(kw) || x.name.includes(kw));
    if (n) {
        const g = G.nm[n.name];
        G.sel = n.name;
        G.ox = -g.x; G.oy = -g.y;
        G.scale = Math.max(G.scale, 1.4);
        document.getElementById('graph-res').textContent = `已定位: ${n.comment || n.name}`;
        showTableDetail(n.name);
    } else {
        document.getElementById('graph-res').textContent = '未找到匹配的表';
    }
}

function graphPath() {
    const from = document.getElementById('graph-from').value.trim();
    const to = document.getElementById('graph-to').value.trim();
    if (!from || !to || !G.nm[from] || !G.nm[to]) {
        document.getElementById('graph-res').textContent = '请输入有效的起点和终点表名（可选自候选列表）';
        return;
    }
    // BFS 最短路（禁止 dim_cst_mgt_org 中转——它是万能枢纽，经过它的路径无业务意义）
    const BLOCKED_HUB = 'dim_cst_mgt_org';
    const prev = { [from]: null };
    const queue = [from];
    while (queue.length) {
        const cur = queue.shift();
        if (cur === to) break;
        for (const nb of (G.adj[cur] || [])) {
            if (nb === BLOCKED_HUB && from !== BLOCKED_HUB && to !== BLOCKED_HUB) continue;
            if (!(nb in prev)) {
                prev[nb] = cur;
                queue.push(nb);
            }
        }
    }
    if (!(to in prev)) {
        document.getElementById('graph-res').textContent = '两表之间不存在已定义的关联路径';
        return;
    }
    const path = [];
    for (let cur = to; cur !== null; cur = prev[cur]) path.unshift(cur);
    G.pathNodes = new Set(path);
    G.pathEdges = new Set(G.edges.filter(e =>
        path.includes(e.from) && path.includes(e.to) &&
        Math.abs(path.indexOf(e.from) - path.indexOf(e.to)) === 1));
    // 聚焦路径中点
    const mid = G.nm[path[Math.floor(path.length / 2)]];
    G.ox = -mid.x; G.oy = -mid.y;
    G.scale = Math.max(G.scale, 1.2);
    document.getElementById('graph-res').textContent = `路径（${path.length - 1} 跳）: ${path.join(' → ')}`;

    // 生成 JOIN SQL 预览
    let sql = '';
    for (let i = 0; i < path.length - 1; i++) {
        const a = path[i], b = path[i + 1];
        const edge = G.edges.find(e =>
            (e.from === a && e.to === b) || (e.from === b && e.to === a));
        const cond = edge ? edge.join_conditions.join(' AND ') : '?';
        sql += (i === 0 ? `FROM ${a}\n` : '') + `JOIN ${b} ON ${cond}\n`;
    }
    const sqlEl = document.getElementById('graph-sql');
    sqlEl.textContent = sql;
    sqlEl.style.display = 'block';
}

function graphAutoLayout() {
    initGraphData();
    G.ox = 0; G.oy = 0; G.scale = 1;
    document.getElementById('graph-res').textContent = '已重新布局';
}

function graphReset() {
    G.sel = null;
    G.pathNodes.clear();
    G.pathEdges.clear();
    G.ox = 0; G.oy = 0; G.scale = 1;
    document.getElementById('graph-search').value = '';
    document.getElementById('graph-from').value = '';
    document.getElementById('graph-to').value = '';
    document.getElementById('graph-res').textContent = '';
    document.getElementById('graph-sql').style.display = 'none';
    closeTableDetail();
}

/** 看板指标穿透：跳到数据资源模块并切到指定子页签 */
function drillResource(tab) {
    switchMode('resource');
    switchResourceTab(tab || 'catalog');
}

/* ==================== 码值库页签 ==================== */

let CODE_RES = { domains: [], loaded: false };

async function loadCodeCatalog() {
    if (!CODE_RES.loaded) {
        try {
            const r = await fetch('/api/code-value-domains');
            const d = await r.json();
            if (d.success) {
                CODE_RES.domains = d.items;
                CODE_RES.loaded = true;
            }
        } catch (e) {
            console.error('加载码值域失败:', e);
        }
    }
    renderCodeCatalog();
}

function renderCodeCatalog() {
    const el = document.getElementById('code-catalog');
    if (!el) return;
    const kw = (document.getElementById('code-search').value || '').trim().toLowerCase();
    let rows = CODE_RES.domains;
    if (kw) {
        rows = rows.filter(d =>
            d.code_name.toLowerCase().includes(kw) ||
            (d.cn_name || '').toLowerCase().includes(kw) ||
            (d.domain || '').toLowerCase().includes(kw));
    }
    const totalItems = rows.reduce((s, d) => s + (d.item_count || 0), 0);
    document.getElementById('code-count').textContent = `共 ${rows.length} 个码值域 · ${totalItems} 条明细`;

    if (!rows.length) {
        el.innerHTML = '<p class="placeholder">没有匹配的码值域</p>';
        return;
    }

    let html = '<table class="data-table"><thead><tr>';
    html += '<th>码值域</th><th style="width:22%">英文名</th><th style="width:12%">业务域</th>';
    html += '<th style="width:10%">明细数</th><th>落列字段</th>';
    html += '</tr></thead><tbody>';
    rows.forEach(d => {
        const cols = (d.columns || []).slice(0, 2).map(c => `<span class="chip">${c}</span>`).join('');
        html += `<tr class="log-row" onclick="toggleCodeItems('${d.code_name}')" style="cursor:pointer">`;
        html += `<td class="detail-question">${_escapeHtml(d.cn_name || d.code_name)}</td>`;
        html += `<td class="mono">${d.code_name}</td>`;
        html += `<td>${_escapeHtml(d.domain || '—')}</td>`;
        html += `<td style="text-align:center"><span class="chip chip-hit">${d.item_count}</span></td>`;
        html += `<td>${cols || '<span style="color:var(--faint)">未落列</span>'}</td>`;
        html += '</tr>';
        html += `<tr class="detail-row"><td colspan="5" class="detail-cell"><div id="code-items-${d.code_name}" style="display:none"></div></td></tr>`;
    });
    html += '</tbody></table>';
    el.innerHTML = html;
}

async function toggleCodeItems(codeName) {
    const box = document.getElementById('code-items-' + codeName);
    if (!box) return;
    if (box.style.display === 'block') {
        box.style.display = 'none';
        return;
    }
    box.innerHTML = '<p class="placeholder">加载中…</p>';
    box.style.display = 'block';
    try {
        const r = await fetch(`/api/code-value-items?code_name=${encodeURIComponent(codeName)}`);
        const d = await r.json();
        if (!d.success) {
            box.innerHTML = '<p>加载失败</p>';
            return;
        }
        if (!d.items.length) {
            box.innerHTML = '<p class="placeholder">该域无码值明细</p>';
            return;
        }
        box.innerHTML = `<div class="chip-list">` + d.items.map(it =>
            `<span class="chip" title="编码 ${it.code}">${_escapeHtml(it.name || it.code)}<span style="color:var(--faint);margin-left:4px">${it.code}</span></span>`
        ).join('') + `</div>`;
    } catch (e) {
        box.innerHTML = '<p>加载失败: ' + _escapeHtml(e.message) + '</p>';
    }
}


/* ==================== 资源管理（P2 通用资源编辑器） ====================
   基于 /api/resources + entry_schema：类型切换 → 条目表格 → 新建/编辑/删除/
   启用开关；模板下载与文件导入（JSON/CSV/xlsx）。
   schema_catalog / schema_graph 由 DDL 重建管道生产，本期只读（页面注明）。
   ======================================================================== */

let RES_ADMIN = {
    types: [],          // /api/resources 摘要
    rtype: '',
    schema: [],         // 当前类型 entry_schema
    items: [],
    page: 1,
    perPage: 20,
    total: 0,
    editingId: null,    // null=新建
    inited: false
};

// 只读资源（写按钮禁用 + 注明）
const RES_ADMIN_READONLY = {
    'schema_catalog': '表结构目录由 DDL 预加载管道生产（SchemaPreloader 每次启动重建），本期只读。',
    'schema_graph': '表关系图由 DDL 预加载管道生产（主外键关系），本期只读。'
};
// 条目标识字段优先级（各类型主键形态不一）
const RES_ADMIN_ID_FIELDS = ['id', 'code_name', 'table_name'];
// 长文本字段用 textarea
const RES_ADMIN_LONG_FIELDS = ['content', 'standard_sql', 'generated_sql', 'correct_sql',
    'error_detail', 'description', 'pattern_regex', 'trigger_words', 'doc_text'];

async function initResAdmin() {
    if (!RES_ADMIN.inited) {
        RES_ADMIN.inited = true;
        try {
            const r = await fetch('/api/resources');
            const d = await r.json();
            if (d.success) RES_ADMIN.types = d.items;
        } catch (e) {
            console.error('加载资源类型失败:', e);
        }
        const sel = document.getElementById('res-admin-type');
        sel.innerHTML = RES_ADMIN.types.map(t =>
            `<option value="${t.name}">${_escapeHtml(t.label)}（${t.name}，${t.count ?? '?'}）</option>`).join('');
        // 默认选中关键词-表关联
        if (RES_ADMIN.types.some(t => t.name === 'keyword_table_map')) {
            RES_ADMIN.rtype = 'keyword_table_map';
            sel.value = 'keyword_table_map';
        } else if (RES_ADMIN.types.length) {
            RES_ADMIN.rtype = RES_ADMIN.types[0].name;
        }
    }
    loadResAdminItems();
}

function onResAdminTypeChange() {
    RES_ADMIN.rtype = document.getElementById('res-admin-type').value;
    RES_ADMIN.page = 1;
    document.getElementById('res-admin-search').value = '';
    document.getElementById('res-admin-import-result').innerHTML = '';
    loadResAdminItems();
}

function resAdminSearch() {
    RES_ADMIN.page = 1;
    loadResAdminItems();
}

function resAdminPage(delta) {
    const maxPage = Math.max(1, Math.ceil(RES_ADMIN.total / RES_ADMIN.perPage));
    RES_ADMIN.page = Math.min(maxPage, Math.max(1, RES_ADMIN.page + delta));
    loadResAdminItems();
}

function _resAdminItemId(item) {
    for (const f of RES_ADMIN_ID_FIELDS) {
        if (item[f] !== undefined && item[f] !== null && item[f] !== '') return item[f];
    }
    return null;
}

async function loadResAdminItems() {
    const rt = RES_ADMIN.rtype;
    if (!rt) return;
    const q = (document.getElementById('res-admin-search').value || '').trim();
    const offset = (RES_ADMIN.page - 1) * RES_ADMIN.perPage;
    const readonlyNote = RES_ADMIN_READONLY[rt] || '';
    document.getElementById('res-admin-note').textContent = readonlyNote;
    document.getElementById('res-admin-new').style.display = readonlyNote ? 'none' : '';
    document.getElementById('res-admin-import-btn').style.display = readonlyNote ? 'none' : '';

    try {
        const [itemsResp, tplResp] = await Promise.all([
            fetch(`/api/resources/${rt}/items?limit=${RES_ADMIN.perPage}&offset=${offset}&q=${encodeURIComponent(q)}`),
            RES_ADMIN.schema.length && RES_ADMIN._schemaFor === rt ? null : fetch(`/api/resources/${rt}/template`)
        ]);
        const d = await itemsResp.json();
        if (!d.success) {
            document.getElementById('res-admin-table').innerHTML = `<p class="placeholder">加载失败: ${_escapeHtml(d.error || '')}</p>`;
            return;
        }
        if (tplResp) {
            const t = await tplResp.json();
            if (t.success) {
                RES_ADMIN.schema = t.template.entry_schema || [];
                RES_ADMIN._schemaFor = rt;
            }
        }
        RES_ADMIN.items = d.items;
        RES_ADMIN.total = d.total;
        renderResAdminTable();
    } catch (e) {
        document.getElementById('res-admin-table').innerHTML = `<p class="placeholder">加载失败: ${_escapeHtml(e.message)}</p>`;
    }
}

function renderResAdminTable() {
    const box = document.getElementById('res-admin-table');
    const readonly = !!RES_ADMIN_READONLY[RES_ADMIN.rtype];
    const items = RES_ADMIN.items;
    document.getElementById('res-admin-count').textContent = `共 ${RES_ADMIN.total} 条`;
    const maxPage = Math.max(1, Math.ceil(RES_ADMIN.total / RES_ADMIN.perPage));
    document.getElementById('res-admin-page').textContent = `第 ${RES_ADMIN.page} / ${maxPage} 页`;

    if (!items.length) {
        box.innerHTML = '<p class="placeholder">暂无条目</p>';
        return;
    }

    // 列：主键 + entry_schema 字段 + enabled（若条目带该字段）
    const idField = RES_ADMIN_ID_FIELDS.find(f => f in items[0]) || 'id';
    const cols = [idField];
    for (const spec of RES_ADMIN.schema) {
        if (!cols.includes(spec.field)) cols.push(spec.field);
    }
    if ('enabled' in items[0] && !cols.includes('enabled')) cols.push('enabled');
    // 补充条目里 schema 未覆盖的字段（如 ingest_time），最多再取 2 个，避免超宽
    for (const k of Object.keys(items[0])) {
        if (cols.length >= 8) break;
        if (!cols.includes(k) && !['updated_at'].includes(k)) cols.push(k);
    }

    const cellText = (v) => {
        if (v === null || v === undefined) return '';
        let s = (typeof v === 'object') ? JSON.stringify(v) : String(v);
        return s.length > 60 ? s.slice(0, 60) + '…' : s;
    };

    let html = '<table class="data-table"><thead><tr>';
    cols.forEach(c => { html += `<th>${_escapeHtml(c)}</th>`; });
    html += '<th style="width:110px">操作</th></tr></thead><tbody>';
    items.forEach(it => {
        const id = _resAdminItemId(it);
        html += '<tr>';
        cols.forEach(c => {
            if (c === 'enabled') {
                const checked = it.enabled ? 'checked' : '';
                html += readonly ? `<td>${it.enabled ? '启用' : '停用'}</td>`
                    : `<td style="text-align:center"><input type="checkbox" ${checked} ` +
                      `onchange="resAdminToggle('${_escapeHtml(String(id))}', this.checked)"></td>`;
            } else {
                html += `<td title="${_escapeHtml(String(it[c] ?? ''))}">${_escapeHtml(cellText(it[c]))}</td>`;
            }
        });
        html += readonly ? '<td><span class="hint">只读</span></td>'
            : `<td><button class="btn btn-sm btn-secondary" onclick="resAdminEdit('${_escapeHtml(String(id))}')">编辑</button> ` +
              `<button class="btn btn-sm btn-secondary" onclick="resAdminDelete('${_escapeHtml(String(id))}')">删除</button></td>`;
        html += '</tr>';
    });
    html += '</tbody></table>';
    box.innerHTML = html;
}

/* ---------- 新建 / 编辑弹窗 ---------- */

function resAdminNew() {
    RES_ADMIN.editingId = null;
    document.getElementById('res-admin-modal-title').textContent = `新建 ${RES_ADMIN.rtype}`;
    _resAdminBuildForm({});
    document.getElementById('res-admin-modal').style.display = 'flex';
}

async function resAdminEdit(id) {
    try {
        const r = await fetch(`/api/resources/${RES_ADMIN.rtype}/items/${encodeURIComponent(id)}`);
        const d = await r.json();
        if (!d.success) { alert(d.error || '加载失败'); return; }
        RES_ADMIN.editingId = id;
        document.getElementById('res-admin-modal-title').textContent = `编辑 ${RES_ADMIN.rtype} #${id}`;
        _resAdminBuildForm(d.item);
        document.getElementById('res-admin-modal').style.display = 'flex';
    } catch (e) {
        alert('加载失败: ' + e.message);
    }
}

function _resAdminBuildForm(item) {
    const form = document.getElementById('res-admin-form');
    if (!RES_ADMIN.schema.length) {
        form.innerHTML = '<p class="placeholder">该资源未提供录入模板</p>';
        return;
    }
    form.innerHTML = RES_ADMIN.schema.map(spec => {
        const f = spec.field;
        const req = spec.required ? ' <span style="color:#ff6a5a">*</span>' : '';
        let v = item[f];
        if (v === null || v === undefined) v = (f === 'enabled' ? 1 : '');
        const fid = `res-admin-f-${f}`;
        if (spec.type === 'int') {
            return `<div class="form-group"><label>${f}${req}</label>` +
                `<input type="number" class="filter-select" style="width:100%" id="${fid}" value="${_escapeHtml(String(v))}"></div>`;
        }
        if (spec.type === 'list' || spec.type === 'dict') {
            const text = v === '' ? '' : JSON.stringify(v, null, 1);
            return `<div class="form-group"><label>${f}${req}（JSON ${spec.type === 'list' ? '数组' : '对象'}）</label>` +
                `<textarea class="form-textarea" id="${fid}" rows="3">${_escapeHtml(text)}</textarea></div>`;
        }
        if (RES_ADMIN_LONG_FIELDS.includes(f) || String(v).length > 60) {
            return `<div class="form-group"><label>${f}${req}</label>` +
                `<textarea class="form-textarea" id="${fid}" rows="4">${_escapeHtml(String(v))}</textarea></div>`;
        }
        return `<div class="form-group"><label>${f}${req}</label>` +
            `<input type="text" class="filter-select" style="width:100%" id="${fid}" value="${_escapeHtml(String(v))}"></div>`;
    }).join('');
}

function closeResAdminModal() {
    document.getElementById('res-admin-modal').style.display = 'none';
}

async function resAdminSave() {
    const payload = {};
    for (const spec of RES_ADMIN.schema) {
        const el = document.getElementById(`res-admin-f-${spec.field}`);
        if (!el) continue;
        const raw = el.value.trim();
        if (raw === '') continue;   // 空值不提交（必填校验交给后端，返回错误列表）
        if (spec.type === 'int') {
            payload[spec.field] = parseInt(raw, 10);
        } else if (spec.type === 'list' || spec.type === 'dict') {
            try {
                payload[spec.field] = JSON.parse(raw);
            } catch (e) {
                alert(`字段 ${spec.field} 不是合法 JSON: ${e.message}`);
                return;
            }
        } else {
            payload[spec.field] = raw;
        }
    }
    const isNew = RES_ADMIN.editingId === null;
    const url = isNew ? `/api/resources/${RES_ADMIN.rtype}/items`
        : `/api/resources/${RES_ADMIN.rtype}/items/${encodeURIComponent(RES_ADMIN.editingId)}`;
    try {
        const r = await fetch(url, {
            method: isNew ? 'POST' : 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const d = await r.json();
        if (!d.success) {
            alert((d.errors && d.errors.join('\n')) || d.error || '保存失败');
            return;
        }
        closeResAdminModal();
        loadResAdminItems();
    } catch (e) {
        alert('保存失败: ' + e.message);
    }
}

async function resAdminDelete(id) {
    if (!confirm(`确认删除 ${RES_ADMIN.rtype} 条目 ${id}？`)) return;
    try {
        const r = await fetch(`/api/resources/${RES_ADMIN.rtype}/items/${encodeURIComponent(id)}`, { method: 'DELETE' });
        const d = await r.json();
        if (!d.success) { alert(d.error || '删除失败'); return; }
        loadResAdminItems();
    } catch (e) {
        alert('删除失败: ' + e.message);
    }
}

async function resAdminToggle(id, checked) {
    try {
        const r = await fetch(`/api/resources/${RES_ADMIN.rtype}/items/${encodeURIComponent(id)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: checked ? 1 : 0 })
        });
        const d = await r.json();
        if (!d.success) { alert(d.error || '更新失败'); loadResAdminItems(); }
    } catch (e) {
        alert('更新失败: ' + e.message);
        loadResAdminItems();
    }
}

/* ---------- 模板下载 / 文件导入 ---------- */

async function resAdminDownloadTemplate() {
    try {
        const r = await fetch(`/api/resources/${RES_ADMIN.rtype}/template`);
        const d = await r.json();
        if (!d.success) { alert(d.error || '获取模板失败'); return; }
        const blob = new Blob([JSON.stringify(d.template, null, 2)], { type: 'application/json' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `${RES_ADMIN.rtype}_template.json`;
        a.click();
        URL.revokeObjectURL(a.href);
    } catch (e) {
        alert('获取模板失败: ' + e.message);
    }
}

async function resAdminImport(input) {
    const file = input.files && input.files[0];
    input.value = '';
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    const box = document.getElementById('res-admin-import-result');
    box.innerHTML = '<p class="hint">导入中…</p>';
    try {
        const r = await fetch(`/api/resources/${RES_ADMIN.rtype}/import`, { method: 'POST', body: fd });
        const d = await r.json();
        if (!d.success) {
            box.innerHTML = `<p class="hint" style="color:#ff6a5a">导入失败: ${_escapeHtml(d.error || '')}</p>`;
            return;
        }
        let html = `<p class="hint">导入完成：接收 ${d.accepted} 条，拒绝 ${d.rejected.length} 条</p>`;
        if (d.rejected.length) {
            html += '<table class="data-table"><thead><tr><th style="width:50%">行内容</th><th>拒绝原因</th></tr></thead><tbody>';
            d.rejected.slice(0, 10).forEach(rj => {
                html += `<tr><td class="mono">${_escapeHtml(JSON.stringify(rj.row).slice(0, 120))}</td>` +
                    `<td>${_escapeHtml(rj.reason)}</td></tr>`;
            });
            html += '</tbody></table>';
            if (d.rejected.length > 10) html += `<p class="hint">… 其余 ${d.rejected.length - 10} 条从略</p>`;
        }
        box.innerHTML = html;
        loadResAdminItems();
    } catch (e) {
        box.innerHTML = `<p class="hint" style="color:#ff6a5a">导入失败: ${_escapeHtml(e.message)}</p>`;
    }
}
