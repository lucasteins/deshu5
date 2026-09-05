/* 本体模型页签：总览 / 类·关系·枚举·概念浏览 / OWL-RDF 导出 / 漂移审批流 */

const ontoState = {
    loaded: false,
    classes: [],
    relations: [],
    enums: [],
    concepts: [],
    synonyms: {},
    entities: [],
    pendingId: null,
};

function _ontoEsc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

async function _ontoGet(url) {
    const r = await fetch(url);
    const d = await r.json();
    if (!d.success) throw new Error(d.error || ('请求失败: ' + url));
    return d;
}

async function _ontoPost(url) {
    const r = await fetch(url, { method: 'POST' });
    const d = await r.json();
    if (!d.success) throw new Error(d.error || ('请求失败: ' + url));
    return d;
}

function initOntologyModule() {
    loadOntoSummary();
    if (!ontoState.loaded) {
        ontoState.loaded = true;
        loadOntoEntities();
        loadOntoClasses();
        loadOntoRelations();
        loadOntoEnums();
        loadOntoConcepts();
        loadOntoProposals();
    }
}

// ==================== 总览与漂移横幅 ====================

async function loadOntoSummary() {
    try {
        const d = await _ontoGet('/api/ontology/summary');
        const grid = document.getElementById('onto-summary');
        if (!d.available) {
            grid.innerHTML = '<div class="hint">本体尚未构建（启动时自动引导，或检查本体库连通性）</div>';
            document.getElementById('onto-version').textContent = '';
            return;
        }
        const s = d.summary || {};
        const meta = d.meta || {};
        document.getElementById('onto-version').textContent =
            `当前版本 v${meta.version} · 构建于 ${meta.built_at || '-'}`;
        const cards = [
            [s.entities, '业务实体'], [s.classes, '物理表（类）'], [s.properties, '属性（列）'],
            [s.relations, '关系'], [s.enumerations, '码值域'], [s.concepts, '业务概念'],
        ];
        const layers = s.entities_by_layer || {};
        if (Object.keys(layers).length) {
            document.getElementById('onto-version').textContent =
                `当前版本 v${meta.version} · 构建于 ${meta.built_at || '-'} · ` +
                `主数据 ${layers.master || 0} / 业务数据 ${layers.business || 0} / 报表 ${layers.report || 0} 实体`;
        }
        grid.innerHTML = cards.map(([n, label]) =>
            `<div class="stat-box"><div class="stat-number">${n || 0}</div>` +
            `<div class="stat-label">${label}</div></div>`).join('');
        updateOntoDriftBanner(d.drift, d.pending_proposal);
    } catch (e) {
        document.getElementById('onto-summary').innerHTML =
            `<div class="hint">加载失败：${_ontoEsc(e.message)}</div>`;
    }
}

function updateOntoDriftBanner(drift, pendingId) {
    const banner = document.getElementById('onto-drift-banner');
    if (drift && pendingId) {
        ontoState.pendingId = pendingId;
        banner.style.display = 'block';
    } else {
        ontoState.pendingId = null;
        banner.style.display = 'none';
        document.getElementById('onto-diff-detail').style.display = 'none';
    }
}

async function ontoCheckDrift() {
    try {
        const d = await _ontoGet('/api/ontology/drift');
        if (d.drift && d.proposal_id) {
            ontoState.pendingId = d.proposal_id;
            renderOntoDiffText(d.diff);
            document.getElementById('onto-drift-banner').style.display = 'block';
        } else if (d.drift) {
            alert('检测到结构变化：' + (d.note || '但已存在待审批提案'));
            loadOntoSummary();
        } else {
            alert('底座结构与当前本体一致，无变更');
        }
        loadOntoProposals();
    } catch (e) {
        alert('漂移检测失败: ' + e.message);
    }
}

async function ontoShowPendingProposal() {
    if (!ontoState.pendingId) return;
    try {
        const d = await _ontoGet(`/api/ontology/proposals/${ontoState.pendingId}`);
        renderOntoDiffText(d.diff);
        const el = document.getElementById('onto-diff-detail');
        el.style.display = el.style.display === 'none' ? 'block' : 'none';
    } catch (e) {
        alert('加载提案失败: ' + e.message);
    }
}

function renderOntoDiffText(diff) {
    diff = diff || {};
    const c = diff.counts || {};
    document.getElementById('onto-drift-text').textContent =
        `+${c.classes_added || 0} 表 / -${c.classes_removed || 0} 表 / ` +
        `~${c.properties_changed || 0} 列改 / +${c.properties_added || 0} 列 / ` +
        `-${c.properties_removed || 0} 列 / ±${(c.relations_added || 0) + (c.relations_removed || 0)} 关系`;

    const section = (title, items, render) => {
        if (!items || !items.length) return '';
        return `<div style="margin:6px 0;"><strong>${title}（${items.length}）</strong><ul style="margin:4px 0 4px 18px;">` +
            items.slice(0, 50).map(render).join('') +
            (items.length > 50 ? `<li>… 共 ${items.length} 项</li>` : '') + '</ul></div>';
    };
    const cl = diff.classes || {}, pr = diff.properties || {}, rl = diff.relations || {};
    document.getElementById('onto-diff-detail').innerHTML =
        section('新增表', cl.added, x => `<li>${_ontoEsc(x)}</li>`) +
        section('删除表', cl.removed, x => `<li>${_ontoEsc(x)}</li>`) +
        section('表注释变更', cl.changed, x => `<li>${_ontoEsc(x)}</li>`) +
        section('新增列', pr.added, x => `<li>${_ontoEsc(x)}</li>`) +
        section('删除列', pr.removed, x => `<li>${_ontoEsc(x)}</li>`) +
        section('列变更', pr.changed, x =>
            `<li>${_ontoEsc(x.name)}：${_ontoEsc(JSON.stringify(x.old))} → ${_ontoEsc(JSON.stringify(x.new))}</li>`) +
        section('新增关系', rl.added, x => `<li>${_ontoEsc(x)}</li>`) +
        section('删除关系', rl.removed, x => `<li>${_ontoEsc(x)}</li>`) +
        section('关系变更', rl.changed, x => `<li>${_ontoEsc(x.key)}</li>`) ||
        '<div class="hint">无结构差异明细</div>';
}

async function ontoDecide(action) {
    if (!ontoState.pendingId) return;
    const verb = action === 'approve' ? '批准更新' : '驳回';
    if (!confirm(`确认${verb}该本体变更提案？`)) return;
    try {
        const d = await _ontoPost(`/api/ontology/proposals/${ontoState.pendingId}/${action}`);
        alert(action === 'approve' ? `已批准，生效版本 v${d.version}` : '已驳回，本体保持当前版本');
        ontoState.loaded = false;
        initOntologyModule();
    } catch (e) {
        alert(verb + '失败: ' + e.message);
    }
}

async function ontoRebuild() {
    if (!confirm('手动重建将基于当前底座生成全量刷新提案（需审批后生效），继续？')) return;
    try {
        const d = await _ontoPost('/api/ontology/rebuild');
        ontoState.pendingId = d.proposal_id;
        renderOntoDiffText(d.diff);
        document.getElementById('onto-drift-banner').style.display = 'block';
        loadOntoProposals();
    } catch (e) {
        alert('重建失败: ' + e.message);
    }
}

function ontoExport() {
    const fmt = document.getElementById('onto-export-fmt').value;
    window.open(`/api/ontology/export?format=${fmt}`, '_blank');
}

// ==================== 子页签 ====================

function switchOntoTab(tab) {
    document.querySelectorAll('[data-otab]').forEach(t => t.classList.remove('active'));
    document.querySelector(`[data-otab="${tab}"]`).classList.add('active');
    ['entities', 'egraph', 'classes', 'relations', 'enums', 'concepts', 'proposals'].forEach(t => {
        const el = document.getElementById('otab-' + t);
        if (el) el.classList.toggle('active', t === tab);
    });
    if (tab === 'proposals') loadOntoProposals();
    if (tab === 'egraph') ontoGraphInit();
}

// ==================== 实体视图（精炼层） ====================

const ONTO_LAYER_BADGE = { master: '主数据', business: '业务数据', report: '统计报表' };

async function loadOntoEntities() {
    try {
        const d = await _ontoGet('/api/ontology/entities');
        ontoState.entities = d.items || [];
        renderOntoEntities();
    } catch (e) {
        document.getElementById('onto-entity-grid').innerHTML =
            `<div class="hint">加载失败：${_ontoEsc(e.message)}</div>`;
    }
}

function renderOntoEntities() {
    const layer = document.getElementById('onto-entity-layer').value;
    const q = (document.getElementById('onto-entity-search').value || '').toLowerCase();
    const list = ontoState.entities.filter(e =>
        (!layer || e.layer === layer) &&
        (!q || e.name.toLowerCase().includes(q) || (e.label || '').toLowerCase().includes(q) ||
         (e.member_tables || []).some(t => t.toLowerCase().includes(q))));
    document.getElementById('onto-entity-count').textContent = `共 ${list.length} 个实体`;
    document.getElementById('onto-entity-grid').innerHTML = list.map(e =>
        `<div class="catalog-card" onclick="ontoEntityDetail('${_ontoEsc(e.name)}')" style="cursor:pointer">` +
        `<div class="catalog-card-title">${_ontoEsc(e.label || e.name)}</div>` +
        `<div class="catalog-card-name">${_ontoEsc(e.name)} · ${ONTO_LAYER_BADGE[e.layer] || e.layer} · ${e.member_count} 表</div>` +
        `</div>`).join('') || '<div class="hint">无匹配实体</div>';
}

async function ontoEntityDetail(name, targetEl) {
    try {
        const d = await _ontoGet(`/api/ontology/entities/${encodeURIComponent(name)}`);
        const e = d.entity;
        const el = document.getElementById(targetEl || 'onto-entity-detail');
        const members = (d.members || []).map(m =>
            `<tr><td><code>${_ontoEsc(m.table)}</code></td><td>${_ontoEsc(m.label)}</td>` +
            `<td>${m.exists ? '✔' : '✘ 不在库'}</td></tr>`).join('');
        const rels = (d.entity_relations || []).map(r =>
            `<li>${_ontoEsc(r.from_entity)} ↔ ${_ontoEsc(r.to_entity)}` +
            `<span class="hint">（${r.member_relations.length} 条表级关系${r.scenarios.length ?
                '；场景：' + r.scenarios.slice(0, 3).map(_ontoEsc).join('、') : ''}）</span></li>`).join('');
        el.innerHTML =
            `<h3>${_ontoEsc(e.label || e.name)} <span class="hint">${_ontoEsc(e.name)} · ` +
            `${ONTO_LAYER_BADGE[e.layer] || e.layer}</span></h3>` +
            `<table class="data-table"><thead><tr><th>成员物理表</th><th>中文名</th><th>在库</th></tr></thead>` +
            `<tbody>${members}</tbody></table>` +
            (rels ? `<h4 style="margin-top:10px;">实体间关系</h4><ul>${rels}</ul>` : '') +
            `<h4 style="margin-top:10px;">映射编辑（保存后需手动重建并审批生效）</h4>` +
            `<div style="display:flex; gap:8px; align-items:flex-start; flex-wrap:wrap;">` +
            `<input type="text" class="search-input" id="onto-ent-label" value="${_ontoEsc(e.label)}" placeholder="实体中文名" style="max-width:220px">` +
            `<select class="filter-select" id="onto-ent-layer">` +
            ['master', 'business', 'report'].map(l =>
                `<option value="${l}"${e.layer === l ? ' selected' : ''}>${ONTO_LAYER_BADGE[l]}</option>`).join('') +
            `</select></div>` +
            `<textarea id="onto-ent-members" style="width:100%; min-height:110px; margin-top:8px; font-family:monospace;"` +
            ` placeholder="成员物理表，每行一个">${(e.member_tables || []).map(_ontoEsc).join('\n')}</textarea>` +
            `<div style="margin-top:6px;">` +
            `<button class="btn btn-sm btn-primary" onclick="ontoEntitySave('${_ontoEsc(e.name)}')">保存映射</button>` +
            `<span class="hint">保存写入映射定义表，经「手动重建 → 提案审批」后进入生效版本</span></div>`;
        el.style.display = 'block';
        el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (e) {
        alert('加载实体详情失败: ' + e.message);
    }
}

async function ontoEntitySave(name) {
    const label = document.getElementById('onto-ent-label').value.trim();
    const layer = document.getElementById('onto-ent-layer').value;
    const members = document.getElementById('onto-ent-members').value
        .split(/[\s,，、]+/).map(s => s.trim()).filter(Boolean);
    try {
        const d = await fetch(`/api/ontology/entity-defs/${encodeURIComponent(name)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ label, layer, member_tables: members }),
        }).then(r => r.json());
        if (!d.success) throw new Error(d.error || '保存失败');
        alert(d.note || '已保存');
    } catch (e) {
        alert('保存失败: ' + e.message);
    }
}

// ==================== 类浏览 ====================

async function loadOntoClasses() {
    try {
        const d = await _ontoGet('/api/ontology/classes');
        ontoState.classes = d.items || [];
        renderOntoClasses();
    } catch (e) {
        document.getElementById('onto-class-grid').innerHTML =
            `<div class="hint">加载失败：${_ontoEsc(e.message)}</div>`;
    }
}

function renderOntoClasses() {
    const q = (document.getElementById('onto-class-search').value || '').toLowerCase();
    const kind = document.getElementById('onto-class-kind').value;
    const list = ontoState.classes.filter(c =>
        (!kind || c.kind === kind) &&
        (!q || c.name.toLowerCase().includes(q) || (c.label || '').toLowerCase().includes(q)));
    document.getElementById('onto-class-count').textContent = `共 ${list.length} 个类`;
    const kindBadge = { dimension: '维度', fact: '事实', other: '其他' };
    document.getElementById('onto-class-grid').innerHTML = list.map(c =>
        `<div class="catalog-card" onclick="ontoClassDetail('${_ontoEsc(c.name)}')" style="cursor:pointer">` +
        `<div class="catalog-card-title">${_ontoEsc(c.label || c.name)}</div>` +
        `<div class="catalog-card-name">${_ontoEsc(c.name)} · ${kindBadge[c.kind] || c.kind} · ${c.property_count} 属性</div>` +
        `</div>`).join('') || '<div class="hint">无匹配类</div>';
}

async function ontoClassDetail(name) {
    try {
        const d = await _ontoGet(`/api/ontology/classes/${encodeURIComponent(name)}`);
        const el = document.getElementById('onto-class-detail');
        const props = (d.properties || []).map(p =>
            `<tr><td>${_ontoEsc(p.name)}</td><td>${_ontoEsc(p.label)}</td>` +
            `<td>${_ontoEsc(p.data_type)}</td><td>${p.is_pk ? '✔' : ''}</td></tr>`).join('');
        const rels = (d.relations || []).map(r =>
            `<li>${_ontoEsc(r.from_class)} ↔ ${_ontoEsc(r.to_class)}` +
            `（${_ontoEsc((r.join_conditions || []).join(' AND '))}）</li>`).join('');
        const enums = (d.enumerations || []).map(e =>
            `<li>${_ontoEsc(e.column)} → ${_ontoEsc(e.cn_name || e.code_name)}` +
            `${e.form ? '（存' + _ontoEsc(e.form) + '）' : ''}</li>`).join('');
        el.innerHTML =
            `<h3>${_ontoEsc(d.class.label || d.class.name)} <span class="hint">${_ontoEsc(d.class.name)}</span></h3>` +
            `<table class="data-table"><thead><tr><th>列名</th><th>中文名</th><th>类型</th><th>主键</th></tr></thead>` +
            `<tbody>${props}</tbody></table>` +
            (rels ? `<h4 style="margin-top:10px;">关系</h4><ul>${rels}</ul>` : '') +
            (enums ? `<h4 style="margin-top:10px;">落列码值</h4><ul>${enums}</ul>` : '');
        el.style.display = 'block';
        el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (e) {
        alert('加载类详情失败: ' + e.message);
    }
}

// ==================== 关系 ====================

async function loadOntoRelations() {
    try {
        const d = await _ontoGet('/api/ontology/relations');
        ontoState.relations = d.items || [];
        renderOntoRelations();
    } catch (e) {
        document.getElementById('onto-rel-list').innerHTML =
            `<div class="hint">加载失败：${_ontoEsc(e.message)}</div>`;
    }
}

function renderOntoRelations() {
    const src = document.getElementById('onto-rel-source').value;
    const q = (document.getElementById('onto-rel-search').value || '').toLowerCase();
    const list = ontoState.relations.filter(r =>
        (!src || r.source === src) &&
        (!q || r.from_class.toLowerCase().includes(q) || r.to_class.toLowerCase().includes(q)));
    document.getElementById('onto-rel-count').textContent = `共 ${list.length} 条`;
    const srcBadge = { physical_fk: '物理外键', governance_doc: '治理文档', both: '双源一致' };
    document.getElementById('onto-rel-list').innerHTML = list.map(r =>
        `<div style="padding:8px 0; border-bottom:1px solid var(--track, #eee);">` +
        `<strong>${_ontoEsc(r.from_class)} → ${_ontoEsc(r.to_class)}</strong> ` +
        `<span class="hint">${srcBadge[r.source] || _ontoEsc(r.source)}</span>` +
        `<div style="font-size:12px; color:#666;">${(r.join_conditions || []).map(_ontoEsc).join(' AND ')}</div>` +
        (r.business_scenarios && r.business_scenarios.length ?
            `<div style="font-size:12px;">场景：${r.business_scenarios.map(_ontoEsc).join('、')}</div>` : '') +
        `</div>`).join('') || '<div class="hint">无匹配关系</div>';
}

// ==================== 码值枚举 ====================

async function loadOntoEnums() {
    try {
        const d = await _ontoGet('/api/ontology/enumerations');
        ontoState.enums = d.items || [];
        renderOntoEnums();
    } catch (e) {
        document.getElementById('onto-enum-list').innerHTML =
            `<div class="hint">加载失败：${_ontoEsc(e.message)}</div>`;
    }
}

function renderOntoEnums() {
    const q = (document.getElementById('onto-enum-search').value || '').toLowerCase();
    const list = ontoState.enums.filter(e =>
        !q || e.code_name.toLowerCase().includes(q) ||
        (e.cn_name || '').toLowerCase().includes(q) || (e.domain || '').toLowerCase().includes(q));
    document.getElementById('onto-enum-count').textContent = `共 ${list.length} 个码值域`;
    document.getElementById('onto-enum-list').innerHTML = list.map(e =>
        `<div class="catalog-card" onclick="ontoEnumDetail('${_ontoEsc(e.code_name)}')" style="cursor:pointer">` +
        `<div class="catalog-card-title">${_ontoEsc(e.cn_name || e.code_name)}</div>` +
        `<div class="catalog-card-name">${_ontoEsc(e.code_name)} · ${_ontoEsc(e.domain || '-')} · ${e.item_count} 项 · 落 ${e.column_refs.length} 列</div>` +
        `</div>`).join('') || '<div class="hint">无匹配码值域</div>';
}

async function ontoEnumDetail(codeName) {
    try {
        const d = await _ontoGet(`/api/ontology/enumerations?code_name=${encodeURIComponent(codeName)}`);
        const e = d.item;
        const el = document.getElementById('onto-enum-detail');
        const items = (e.items || []).slice(0, 100).map(it =>
            `<tr><td>${_ontoEsc(it.code)}</td><td>${_ontoEsc(it.name)}</td></tr>`).join('');
        const refs = (e.column_refs || []).map(r =>
            `<li>${_ontoEsc(r.table)}.${_ontoEsc(r.column)}${r.form ? '（存' + _ontoEsc(r.form) + '）' : ''}</li>`).join('');
        el.innerHTML =
            `<h3>${_ontoEsc(e.cn_name || e.code_name)} <span class="hint">${_ontoEsc(e.code_name)}</span></h3>` +
            `<div class="hint">业务域：${_ontoEsc([e.domain_l1, e.domain_l2, e.domain_l3].filter(Boolean).join(' / ') || '-')} · 共 ${(e.items || []).length} 项（最多显示 100）</div>` +
            (refs ? `<h4 style="margin-top:8px;">落列</h4><ul>${refs}</ul>` : '') +
            `<table class="data-table"><thead><tr><th>编码</th><th>名称</th></tr></thead><tbody>${items}</tbody></table>`;
        el.style.display = 'block';
        el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (e) {
        alert('加载码值域失败: ' + e.message);
    }
}

// ==================== 业务概念 ====================

async function loadOntoConcepts() {
    try {
        const d = await _ontoGet('/api/ontology/concepts');
        ontoState.concepts = d.items || [];
        ontoState.synonyms = d.synonym_groups || {};
        renderOntoConcepts();
    } catch (e) {
        document.getElementById('onto-concept-list').innerHTML =
            `<div class="hint">加载失败：${_ontoEsc(e.message)}</div>`;
    }
}

function renderOntoConcepts() {
    const q = (document.getElementById('onto-concept-search').value || '').toLowerCase();
    const list = ontoState.concepts.filter(c =>
        !q || c.concept.toLowerCase().includes(q) ||
        (c.maps_to || []).some(t => t.toLowerCase().includes(q)));
    document.getElementById('onto-concept-count').textContent = `共 ${list.length} 个概念`;
    document.getElementById('onto-concept-list').innerHTML = list.map(c =>
        `<div style="padding:6px 0; border-bottom:1px solid var(--track, #eee);">` +
        `<strong>${_ontoEsc(c.concept)}</strong> → ${(c.maps_to || []).map(t => `<code>${_ontoEsc(t)}</code>`).join('、')}` +
        `</div>`).join('') || '<div class="hint">无匹配概念</div>';
    const syn = ontoState.synonyms;
    const keys = Object.keys(syn);
    document.getElementById('onto-synonym-block').innerHTML = keys.length ?
        `<h4>同义词组（${keys.length}）</h4>` + keys.map(k =>
            `<div style="font-size:12px; padding:2px 0;">${_ontoEsc(k)} = ${(syn[k] || []).map(_ontoEsc).join(' / ')}</div>`).join('') : '';
}

// ==================== 提案历史 ====================

async function loadOntoProposals() {
    try {
        const d = await _ontoGet('/api/ontology/proposals');
        const el = document.getElementById('onto-proposal-list');
        if (!d.items || !d.items.length) {
            el.innerHTML = '<div class="hint">暂无变更提案</div>';
            return;
        }
        const statusBadge = { pending: '待审批', approved: '已批准', rejected: '已驳回' };
        el.innerHTML = d.items.map(p => {
            const c = p.counts || {};
            return `<div style="padding:8px 0; border-bottom:1px solid var(--track, #eee);">` +
                `<strong>#${p.id}</strong> <span class="hint">${statusBadge[p.status] || p.status}</span> ` +
                `<span class="hint">${_ontoEsc(p.created_at)}</span>` +
                `<div style="font-size:12px;">+${c.classes_added || 0} 表 / -${c.classes_removed || 0} 表 / ` +
                `+${c.properties_added || 0} 列 / -${c.properties_removed || 0} 列 / ` +
                `~${c.properties_changed || 0} 列改 / ±${(c.relations_added || 0) + (c.relations_removed || 0)} 关系</div>` +
                `</div>`;
        }).join('');
    } catch (e) {
        document.getElementById('onto-proposal-list').innerHTML =
            `<div class="hint">加载失败：${_ontoEsc(e.message)}</div>`;
    }
}


// ==================== 实体图谱（力导向图，二级页签） ====================

const ONTO_LAYER_COLORS = { master: '#5aa2ff', business: '#ff7a72', report: '#4ecb8d' };
let OG = null;   // 图谱状态

async function ontoGraphInit() {
    if (OG && OG.inited) { ontoGraphResize(); ontoGraphDraw(); return; }
    try {
        const [entRes, relRes] = await Promise.all([
            _ontoGet('/api/ontology/entities'),
            _ontoGet('/api/ontology/entity-relations'),
        ]);
        const entities = entRes.items || [];
        const rels = relRes.items || [];
        const deg = {};
        rels.forEach(r => {
            deg[r.from_entity] = (deg[r.from_entity] || 0) + 1;
            deg[r.to_entity] = (deg[r.to_entity] || 0) + 1;
        });
        const nm = {};
        entities.forEach((e, i) => {
            const angle = (i / Math.max(entities.length, 1)) * Math.PI * 2;
            const radius = 200 + (i % 5) * 28;
            nm[e.name] = Object.assign({}, e, {
                x: Math.cos(angle) * radius,
                y: Math.sin(angle) * radius,
                vx: 0, vy: 0,
                r: 13 + Math.min(10, (e.member_count || 1) * 1.5) + Math.min(8, (deg[e.name] || 0) * 0.8),
            });
        });
        const adj = {};
        const edges = [];
        rels.forEach(r => {
            if (!nm[r.from_entity] || !nm[r.to_entity]) return;
            (adj[r.from_entity] = adj[r.from_entity] || []).push(r.to_entity);
            (adj[r.to_entity] = adj[r.to_entity] || []).push(r.from_entity);
            edges.push(r);
        });
        OG = {
            nm: nm, adj: adj, edges: edges,
            sel: null, scale: 1, ox: 0, oy: 0,
            dragNode: null, panStart: null, hoverNode: null, inited: false,
            hiddenLayers: new Set(),   // 被图例关闭的层级（master/business/report）
            hiddenNodes: new Set(),    // 被单个隐藏的节点（右键节点）
        };
        const dl = document.getElementById('onto-graph-nl');
        if (dl) {
            dl.innerHTML = entities.map(e =>
                '<option value="' + _ontoEsc(e.name) + '">' + _ontoEsc(e.label || '') + '</option>').join('');
        }
        document.getElementById('onto-graph-res').textContent =
            entities.length + ' 实体 · ' + edges.length + ' 关系';
        ontoGraphResize();
        OG.inited = true;
        ontoGraphBindEvents();
        (function loop() {
            for (let i = 0; i < 4; i++) ontoGraphPhysics();
            ontoGraphDraw();
            requestAnimationFrame(loop);
        })();
    } catch (e) {
        document.getElementById('onto-graph-res').textContent = '图谱加载失败: ' + e.message;
    }
}

function ontoGraphResize() {
    const cv = document.getElementById('onto-graph-canvas');
    if (!cv || !cv.parentElement) return;
    cv.width = cv.parentElement.clientWidth;
    cv.height = 560;
}

function ogVisible(name) {
    return OG && !OG.hiddenLayers.has(OG.nm[name].layer) && !OG.hiddenNodes.has(name);
}

function ontoGraphPhysics() {
    if (!OG) return;
    const rep = 9000, att = 0.02, damp = 0.86;
    // 隐藏层不参与力学（既不被推动，也不作为力源）
    const names = Object.keys(OG.nm).filter(ogVisible);
    for (const aName of names) {
        const a = OG.nm[aName];
        // 拖拽中与钉住（拖拽松手即钉住，不回弹）的节点不动
        if (a === OG.dragNode || a.pinned) continue;
        let fx = 0, fy = 0;
        for (const bName of names) {
            if (aName === bName) continue;
            const b = OG.nm[bName];
            const dx = a.x - b.x, dy = a.y - b.y;
            const d = Math.max(24, Math.hypot(dx, dy));
            const f = rep / (d * d);
            fx += dx / d * f; fy += dy / d * f;
        }
        for (const nb of (OG.adj[aName] || [])) {
            const b = OG.nm[nb];
            if (!b || !ogVisible(nb)) continue;
            fx -= (a.x - b.x) * att;
            fy -= (a.y - b.y) * att;
        }
        a.vx = a.vx * damp + fx * 0.01;
        a.vy = a.vy * damp + fy * 0.01;
        a.x += a.vx; a.y += a.vy;
    }
}

function ogW2S(wx, wy) {
    const cv = document.getElementById('onto-graph-canvas');
    return [(wx + OG.ox) * OG.scale + cv.width / 2, (wy + OG.oy) * OG.scale + cv.height / 2];
}
function ogS2W(sx, sy) {
    const cv = document.getElementById('onto-graph-canvas');
    return [(sx - cv.width / 2) / OG.scale - OG.ox, (sy - cv.height / 2) / OG.scale - OG.oy];
}

function ontoGraphDraw() {
    const cv = document.getElementById('onto-graph-canvas');
    if (!cv || !OG) return;
    const ctx = cv.getContext('2d');
    ctx.clearRect(0, 0, cv.width, cv.height);

    const neighborSet = new Set();
    if (OG.sel) {
        neighborSet.add(OG.sel);
        (OG.adj[OG.sel] || []).forEach(n => neighborSet.add(n));
    }
    // 边（任一端被隐藏层关闭则不画）
    OG.edges.forEach(e => {
        const a = OG.nm[e.from_entity], b = OG.nm[e.to_entity];
        if (!a || !b || !ogVisible(e.from_entity) || !ogVisible(e.to_entity)) return;
        const p1 = ogW2S(a.x, a.y), p2 = ogW2S(b.x, b.y);
        const active = !OG.sel || (neighborSet.has(e.from_entity) && neighborSet.has(e.to_entity));
        ctx.strokeStyle = active ? 'rgba(155,144,134,0.5)' : 'rgba(155,144,134,0.1)';
        ctx.lineWidth = Math.min(3, 0.7 + e.member_relations.length * 0.4) * OG.scale;
        ctx.beginPath(); ctx.moveTo(p1[0], p1[1]); ctx.lineTo(p2[0], p2[1]); ctx.stroke();
    });
    // 节点（隐藏层不画）
    Object.keys(OG.nm).forEach(name => {
        if (!ogVisible(name)) return;
        const n = OG.nm[name];
        const p = ogW2S(n.x, n.y);
        const dim = OG.sel && !neighborSet.has(name);
        ctx.globalAlpha = dim ? 0.25 : 1;
        ctx.beginPath();
        ctx.arc(p[0], p[1], n.r * OG.scale, 0, Math.PI * 2);
        ctx.fillStyle = ONTO_LAYER_COLORS[n.layer] || '#9b9086';
        ctx.fill();
        if (name === OG.sel || name === OG.hoverNode) {
            ctx.lineWidth = 2.5; ctx.strokeStyle = '#ff7a1a'; ctx.stroke();
        }
        if (n.pinned) {
            // 钉住标记：中心小圆点（双击可取消固定）
            ctx.beginPath();
            ctx.arc(p[0], p[1], Math.max(2.5, 3 * OG.scale), 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(255,255,255,0.9)';
            ctx.fill();
        }
        ctx.globalAlpha = 1;
        ctx.font = Math.max(10, 12 * OG.scale) + 'px sans-serif';
        ctx.fillStyle = '#f2ece4';
        ctx.textAlign = 'center';
        ctx.fillText(n.label || name, p[0], p[1] + n.r * OG.scale + 14 * OG.scale);
    });
}

function ontoGraphNodeAt(sx, sy) {
    const w = ogS2W(sx, sy);
    const names = Object.keys(OG.nm);
    for (let i = 0; i < names.length; i++) {
        if (!ogVisible(names[i])) continue;
        const n = OG.nm[names[i]];
        if (Math.hypot(w[0] - n.x, w[1] - n.y) <= n.r + 4) return names[i];
    }
    return null;
}

function ontoGraphBindEvents() {
    const cv = document.getElementById('onto-graph-canvas');
    const tip = document.getElementById('onto-graph-tip');

    cv.addEventListener('mousedown', ev => {
        const rect = cv.getBoundingClientRect();
        const name = ontoGraphNodeAt(ev.clientX - rect.left, ev.clientY - rect.top);
        OG._moved = false;
        if (name) {
            OG.dragNode = OG.nm[name];
        } else {
            OG.panStart = { x: ev.clientX, y: ev.clientY, ox: OG.ox, oy: OG.oy };
        }
    });
    window.addEventListener('mousemove', ev => {
        if (!OG) return;
        const rect = cv.getBoundingClientRect();
        if (OG.dragNode) {
            const w = ogS2W(ev.clientX - rect.left, ev.clientY - rect.top);
            OG.dragNode.x = w[0]; OG.dragNode.y = w[1];
            OG.dragNode.vx = OG.dragNode.vy = 0;
            OG._moved = true;
        } else if (OG.panStart) {
            OG.ox = OG.panStart.ox + (ev.clientX - OG.panStart.x) / OG.scale;
            OG.oy = OG.panStart.oy + (ev.clientY - OG.panStart.y) / OG.scale;
        } else {
            const name = ontoGraphNodeAt(ev.clientX - rect.left, ev.clientY - rect.top);
            OG.hoverNode = name;
            if (name) {
                const n = OG.nm[name];
                tip.innerHTML = '<b>' + _ontoEsc(n.label || name) + '</b> ' +
                    '<span style="color:#9b9086">' + _ontoEsc(name) + '</span><br>' +
                    (ONTO_LAYER_BADGE[n.layer] || n.layer) + ' · ' + n.member_count + ' 成员表 · ' +
                    (OG.adj[name] || []).length + ' 实体关系' + (n.pinned ? ' · 已固定' : '') + '<br>' +
                    '<span class="hint">点击查看明细 · 拖拽固定 · 双击取消固定 · 右键隐藏</span>';
                tip.style.display = 'block';
                tip.style.left = (ev.clientX + 14) + 'px';
                tip.style.top = (ev.clientY + 10) + 'px';
            } else {
                tip.style.display = 'none';
            }
        }
    });
    window.addEventListener('mouseup', () => {
        if (!OG) return;
        // 拖拽松手即钉住（不回弹），双击节点取消固定
        if (OG.dragNode && OG._moved) OG.dragNode.pinned = true;
        OG.dragNode = null; OG.panStart = null;
    });
    cv.addEventListener('dblclick', ev => {
        const rect = cv.getBoundingClientRect();
        const name = ontoGraphNodeAt(ev.clientX - rect.left, ev.clientY - rect.top);
        if (name && OG.nm[name].pinned) OG.nm[name].pinned = false;
    });
    // 右键节点：单独隐藏（工具栏「恢复隐藏」一键还原）
    cv.addEventListener('contextmenu', ev => {
        ev.preventDefault();
        const rect = cv.getBoundingClientRect();
        const name = ontoGraphNodeAt(ev.clientX - rect.left, ev.clientY - rect.top);
        if (!name) return;
        OG.hiddenNodes.add(name);
        if (OG.sel === name) {
            OG.sel = null;
            const el = document.getElementById('onto-graph-detail');
            if (el) el.style.display = 'none';
        }
        tip.style.display = 'none';
        ontoGraphUpdateCount();
    });
    cv.addEventListener('click', ev => {
        if (OG._moved) return;  // 拖拽结束的 click 不触发选中
        const rect = cv.getBoundingClientRect();
        const name = ontoGraphNodeAt(ev.clientX - rect.left, ev.clientY - rect.top);
        OG.sel = name || null;
        if (name) {
            ontoGraphDraw();
            // 在图谱模块下方展示实体明细（不跳转实体视图页签）
            ontoEntityDetail(name, 'onto-graph-detail');
        } else {
            const el = document.getElementById('onto-graph-detail');
            if (el) el.style.display = 'none';
        }
    });
    cv.addEventListener('wheel', ev => {
        ev.preventDefault();
        const factor = ev.deltaY < 0 ? 1.12 : 0.89;
        OG.scale = Math.min(3, Math.max(0.25, OG.scale * factor));
    }, { passive: false });
}

function ontoGraphToggleLayer(layer) {
    if (!OG) return;
    if (OG.hiddenLayers.has(layer)) {
        OG.hiddenLayers.delete(layer);
    } else {
        OG.hiddenLayers.add(layer);
        // 选中节点被隐藏时取消选中并收起明细
        if (OG.sel && !ogVisible(OG.sel)) {
            OG.sel = null;
            const el = document.getElementById('onto-graph-detail');
            if (el) el.style.display = 'none';
        }
    }
    document.getElementById('og-layer-' + layer).classList.toggle('off', OG.hiddenLayers.has(layer));
    ontoGraphUpdateCount();
}

function ontoGraphUpdateCount() {
    const vis = Object.keys(OG.nm).filter(ogVisible).length;
    const evis = OG.edges.filter(e => ogVisible(e.from_entity) && ogVisible(e.to_entity)).length;
    document.getElementById('onto-graph-res').textContent =
        vis + ' 实体 · ' + evis + ' 关系' +
        (OG.hiddenLayers.size || OG.hiddenNodes.size ? '（已隐藏部分）' : '');
    // 单节点隐藏的恢复入口
    const unhideBtn = document.getElementById('onto-graph-unhide');
    if (unhideBtn) {
        unhideBtn.style.display = OG.hiddenNodes.size ? 'inline-block' : 'none';
        unhideBtn.textContent = `恢复隐藏(${OG.hiddenNodes.size})`;
    }
}

function ontoGraphUnhideAll() {
    if (!OG) return;
    OG.hiddenNodes.clear();
    ontoGraphUpdateCount();
}

function ontoGraphSearch() {
    if (!OG) return;
    const kw = (document.getElementById('onto-graph-search').value || '').trim().toLowerCase();
    if (!kw) return;
    const names = Object.keys(OG.nm).filter(ogVisible);
    for (let i = 0; i < names.length; i++) {
        const n = OG.nm[names[i]];
        if (names[i].toLowerCase().includes(kw) || (n.label || '').toLowerCase().includes(kw)) {
            OG.sel = names[i];
            OG.ox = -n.x; OG.oy = -n.y;
            document.getElementById('onto-graph-res').textContent = '已定位: ' + (n.label || names[i]);
            return;
        }
    }
}

function ontoGraphReset() {
    if (!OG) return;
    OG.sel = null; OG.scale = 1; OG.ox = 0; OG.oy = 0;
    OG.hiddenLayers.clear();
    OG.hiddenNodes.clear();
    Object.keys(OG.nm).forEach(name => { OG.nm[name].pinned = false; });
    ['master', 'business', 'report'].forEach(l =>
        document.getElementById('og-layer-' + l).classList.remove('off'));
    document.getElementById('onto-graph-search').value = '';
    const el = document.getElementById('onto-graph-detail');
    if (el) el.style.display = 'none';
    ontoGraphUpdateCount();
}
