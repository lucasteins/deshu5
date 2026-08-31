// 综合问答/报告生成模块
// 意图 → 分解标准化问题（可编辑）→ SSE 并发取数 → Markdown 报告拼装/导出/历史

let reportPlan = null;        // 当前分解计划
let reportMd = '';            // 当前报告 Markdown
let reportRunId = null;       // 当前 run id（导出用）
let reportInited = false;

function initReportModule() {
    if (reportInited) return;
    reportInited = true;
    loadReportTemplatesIntoSelect();
    loadReportRuns();
}

// ==================== 二级页签 & 模式切换 ====================

function switchReportTab(tab) {
    document.querySelectorAll('[data-rptab]').forEach(t => t.classList.remove('active'));
    document.querySelector(`[data-rptab="${tab}"]`).classList.add('active');
    ['gen', 'runs', 'tpl'].forEach(t => {
        const el = document.getElementById('rptab-' + t);
        if (el) el.classList.toggle('active', t === tab);
    });
    if (tab === 'runs') loadReportRuns();
    if (tab === 'tpl') loadReportTemplates();
}

let reportMode = 'intent';  // intent: 意图识别 | template: 选择模板

function switchReportMode(mode) {
    if (mode === reportMode) return;
    reportMode = mode;
    document.getElementById('rmode-intent').style.display = mode === 'intent' ? 'block' : 'none';
    document.getElementById('rmode-template').style.display = mode === 'template' ? 'block' : 'none';
    document.getElementById('rmode-btn-intent').classList.toggle('active', mode === 'intent');
    document.getElementById('rmode-btn-template').classList.toggle('active', mode === 'template');
    // 切换模式时清空工作区，避免另一模式遗留的计划/结果串台
    reportPlan = null;
    _setGenerateEnabled(false);
    for (const id of ['report-plan-section', 'report-progress-section', 'report-result-section'])
        document.getElementById(id).style.display = 'none';
}

function _setGenerateEnabled(on) {
    document.getElementById('btn-report-generate').disabled = !on;
    document.getElementById('btn-report-generate-tpl').disabled = !on;
}

// ==================== 计划 ====================

/** 路径一：意图识别 → 匹配模板（命中则展示推荐模板）/ 自由分解 */
async function makeReportPlan() {
    const intent = document.getElementById('report-intent').value.trim();
    if (!intent) { alert('请输入报告意图'); return; }
    const btn = document.getElementById('btn-report-plan');
    btn.disabled = true; btn.textContent = '分解中...';
    document.getElementById('report-result-section').style.display = 'none';
    document.getElementById('report-progress-section').style.display = 'none';
    try {
        const resp = await fetch('/api/report/plan', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({intent_text: intent})
        });
        const data = await resp.json();
        if (!data.success) { alert('分解失败: ' + data.error); return; }
        reportPlan = data.plan;
        renderReportPlan();
        _setGenerateEnabled(true);
    } catch (e) {
        alert('分解失败: ' + e.message);
    } finally {
        btn.disabled = false; btn.textContent = '匹配模板并分解';
    }
}

/** 路径二：直接选择模板 → 加载模板问题（可编辑）→ 生成 */
async function loadTemplateQuestions() {
    const tplId = document.getElementById('report-template-select').value;
    if (!tplId) { alert('请先选择模板'); return; }
    const period = document.getElementById('report-period').value.trim();
    const org = document.getElementById('report-org').value.trim();
    try {
        const resp = await fetch('/api/report/plan', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({template_id: parseInt(tplId), period, org})
        });
        const data = await resp.json();
        if (!data.success) { alert('加载失败: ' + data.error); return; }
        reportPlan = data.plan;
        renderReportPlan();
        _setGenerateEnabled(true);
    } catch (e) {
        alert('加载失败: ' + e.message);
    }
}

function renderReportPlan() {
    const sec = document.getElementById('report-plan-section');
    const list = document.getElementById('report-plan-list');
    const meta = document.getElementById('report-plan-meta');
    if (!reportPlan) { sec.style.display = 'none'; return; }
    const readonly = (reportMode === 'template');
    meta.textContent = `《${reportPlan.report_title}》 ${(reportPlan.org_scope || []).join('、')} ${reportPlan.period || ''}`
        + (reportPlan.template_name ? ` | 模板: ${reportPlan.template_name}` : '')
        + ` | 共 ${reportPlan.question_count} 题`
        + (readonly ? ' | 模板问题只读（修改请到「模板管理」）' : '');
    let html = '';
    reportPlan.sections.forEach((s, si) => {
        html += `<div class="report-plan-sec"><div class="report-plan-sec-title">${escapeHtmlRp(s.section_title)}</div>`;
        s.questions.forEach(q => {
            const badge = q.source === 'qa_pair'
                ? `<span class="report-src-badge qa">标准问答对#${q.qa_id} ${(q.match_score || 0).toFixed(2)}</span>`
                : (q.from_template
                    ? `<span class="report-src-badge qa" style="border-color:#8e44ad;color:#8e44ad;background:rgba(142,68,173,0.12);">模板问题#${q.qa_id || ''}</span>`
                    : '<span class="report-src-badge gen">实时生成</span>');
            if (readonly) {
                html += `<div class="report-plan-q" data-qid="${q.qid}">
                    <span class="report-q-readonly">${escapeHtmlRp(q.question)}</span>${badge}
                </div>`;
            } else {
                html += `<div class="report-plan-q" data-qid="${q.qid}">
                    <input type="text" class="search-input report-q-input" value="${escapeAttrRp(q.question)}">
                    ${badge}
                    <button class="btn btn-error btn-sm" onclick="removeReportQuestion(${si},'${q.qid}')">删除</button>
                </div>`;
            }
        });
        if (!readonly) {
            html += `<div class="report-plan-q"><input type="text" class="search-input report-q-input" id="report-newq-${si}" placeholder="手动新增问题，回车添加" onkeydown="if(event.key==='Enter'){addReportQuestion(${si});return false;}"></div>`;
        }
        html += '</div>';
    });
    list.innerHTML = html;
    sec.style.display = 'block';
}

/** 意图模式：手动新增问题到指定章节 */
function addReportQuestion(si) {
    const input = document.getElementById(`report-newq-${si}`);
    const text = input.value.trim();
    if (!text) return;
    reportPlan.sections[si].questions.push({
        qid: 'q' + Date.now(), question: text, source: 'generated'
    });
    reportPlan.question_count = reportPlan.sections.reduce((n, s) => n + s.questions.length, 0);
    renderReportPlan();
    _setGenerateEnabled(reportPlan.question_count > 0);
}

function removeReportQuestion(si, qid) {
    const sec = reportPlan.sections[si];
    sec.questions = sec.questions.filter(q => q.qid !== qid);
    if (!sec.questions.length) reportPlan.sections.splice(si, 1);
    reportPlan.question_count = reportPlan.sections.reduce((n, s) => n + s.questions.length, 0);
    renderReportPlan();
    _setGenerateEnabled(reportPlan.question_count > 0);
}

/** 把前端编辑后的题干收回 plan；题干被人工改动后，原命中的标准 SQL 不再可信，降级为实时生成 */
function collectReportPlan() {
    const edited = {};
    document.querySelectorAll('#report-plan-list .report-plan-q').forEach(el => {
        edited[el.dataset.qid] = el.querySelector('.report-q-input').value.trim();
    });
    for (const s of reportPlan.sections) {
        for (const q of s.questions) {
            const text = edited[q.qid];
            if (!text || text === q.question) continue;
            q.question = text;
            if (q.source === 'qa_pair') {
                q.source = 'generated';
                delete q.qa_id; delete q.standard_sql; delete q.match_score; delete q.qa_question;
            }
        }
    }
    return reportPlan;
}

// ==================== 生成（SSE） ====================

async function generateReport() {
    if (!reportPlan) { alert('请先分解问题'); return; }
    collectReportPlan();
    const btn = reportMode === 'intent'
        ? document.getElementById('btn-report-generate')
        : document.getElementById('btn-report-generate-tpl');
    btn.disabled = true; btn.textContent = '生成中...';

    const progSec = document.getElementById('report-progress-section');
    const progList = document.getElementById('report-progress-list');
    progSec.style.display = 'block';
    document.getElementById('report-result-section').style.display = 'none';
    const rows = {};
    let html = '';
    for (const s of reportPlan.sections) for (const q of s.questions) {
        html += `<div class="report-progress-item" id="rp-${q.qid}"><span class="rp-status">⏳</span> ${escapeHtmlRp(q.question)}</div>`;
    }
    progList.innerHTML = html;

    try {
        const response = await fetch('/api/report/generate', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({plan: reportPlan})
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.error || ('HTTP ' + response.status));
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '', finalResult = null;
        while (true) {
            const {done, value} = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, {stream: true});
            const parts = buffer.split('\n\n');
            buffer = parts.pop();
            for (const part of parts) {
                const line = part.trim();
                if (!line.startsWith('data:')) continue;
                let evt;
                try { evt = JSON.parse(line.slice(5)); } catch { continue; }
                if (evt.kind === 'question' && evt.detail) {
                    const d = evt.detail;
                    const el = document.getElementById('rp-' + d.qid);
                    if (el) el.innerHTML = d.status === 'ok'
                        ? `<span class="rp-status ok">✓</span> ${escapeHtmlRp(d.question)} <span class="hint">${d.row_count}行 / ${formatMs(d.ms)}</span>`
                        : `<span class="rp-status fail">✗</span> ${escapeHtmlRp(d.question)} <span class="hint">${escapeHtmlRp((d.error || '').slice(0, 80))}</span>`;
                } else if (evt.kind === 'composed') {
                    // 拼装完成，等待 done
                } else if (evt.done) {
                    finalResult = evt.result;
                } else if (evt.error) {
                    throw new Error(evt.error);
                }
            }
        }
        if (!finalResult) throw new Error('流式响应中断');
        reportMd = finalResult.report_md || '';
        reportRunId = finalResult.run_id;
        document.getElementById('report-result-meta').textContent =
            `run#${finalResult.run_id} | 成功 ${finalResult.usage.questions_ok}/${finalResult.usage.questions_total} 题`
            + ` | ${formatMs(finalResult.duration_ms)}` + (finalResult.degraded ? ' | 降级拼装' : '');
        document.getElementById('report-rendered').innerHTML = renderMarkdownRp(reportMd);
        document.getElementById('report-result-section').style.display = 'block';
        loadReportRuns();
    } catch (e) {
        alert('生成失败: ' + e.message);
    } finally {
        btn.disabled = false; btn.textContent = '生成报告';
    }
}

function copyReportMd() {
    if (!reportMd) return;
    navigator.clipboard.writeText(reportMd).then(() => alert('已复制 Markdown'));
}

function exportReport() {
    if (reportRunId) { window.open(`/api/report/runs/${reportRunId}/export`); return; }
    if (!reportMd) return;
    const blob = new Blob([reportMd], {type: 'text/markdown;charset=utf-8'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'report.md';
    a.click();
    URL.revokeObjectURL(a.href);
}

/** 模板提炼：从当前报告提炼可复用模板，填入模板表单供用户修改后保存 */
async function distillReportTemplate() {
    if (!reportRunId) { alert('仅支持对已落库的报告提炼模板'); return; }
    const btn = document.getElementById('btn-report-distill');
    btn.disabled = true; btn.textContent = '提炼中...';
    try {
        const resp = await fetch('/api/report/distill-template', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({run_id: reportRunId})
        });
        const data = await resp.json();
        if (!data.success) { alert('提炼失败: ' + data.error); return; }
        const t = data.template;
        resetReportTemplateForm();
        document.getElementById('tpl-name').value = t.name || '';
        document.getElementById('tpl-triggers').value = (t.trigger_words || []).join(',');
        document.getElementById('tpl-remark').value = t.remark || '';
        // 提炼的问题对象（含 standard_sql/qa_id）直接进表格编辑器
        tplSections = (t.outline || []).map(s => ({
            section_title: s.section_title || '',
            hint: s.hint || '',
            questions: (s.questions || []).map(q => (typeof q === 'object'
                ? {question: q.question || '', qa_id: q.qa_id || null,
                   has_sql: !!q.standard_sql, is_usable: true, _sql: q.standard_sql || ''}
                : {question: q, qa_id: null, has_sql: false, is_usable: false})),
        }));
        if (!tplSections.length) tplSections = [{section_title: '', hint: '', questions: []}];
        renderTplEditor();
        // 跳到模板管理页签供用户自助修改
        switchReportTab('tpl');
    } catch (e) {
        alert('提炼失败: ' + e.message);
    } finally {
        btn.disabled = false; btn.textContent = '模板提炼';
    }
}

// ==================== 历史 ====================

async function loadReportRuns() {
    const list = document.getElementById('report-runs-list');
    try {
        const resp = await fetch('/api/report/runs?per_page=20');
        const data = await resp.json();
        if (!data.success) { list.innerHTML = '<div class="hint">加载失败</div>'; return; }
        if (!data.items.length) { list.innerHTML = '<div class="hint">暂无历史报告</div>'; return; }
        list.innerHTML = data.items.map(it => `
            <div class="report-run-item" onclick="viewReportRun(${it.id})">
                <span class="report-run-title">${escapeHtmlRp(it.report_title)}</span>
                <span class="hint">${escapeHtmlRp(it.created_at || '')} | ${it.status} | ${formatMs(it.duration_ms)}</span>
                <span class="report-run-actions">
                    <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); exportReportRun(${it.id})">导出</button>
                    <button class="btn btn-error btn-sm" onclick="event.stopPropagation(); deleteReportRun(${it.id})">删除</button>
                </span>
            </div>`).join('');
    } catch (e) {
        list.innerHTML = '<div class="hint">加载失败: ' + escapeHtmlRp(e.message) + '</div>';
    }
}

function exportReportRun(id) {
    window.open(`/api/report/runs/${id}/export`);
}

async function deleteReportRun(id) {
    if (!confirm(`确认删除报告 run#${id}？此操作不可恢复。`)) return;
    try {
        const resp = await fetch(`/api/report/runs/${id}`, {method: 'DELETE'});
        const data = await resp.json();
        if (!data.success) { alert('删除失败: ' + data.error); return; }
        if (reportRunId === id) {
            document.getElementById('report-result-section').style.display = 'none';
            reportRunId = null; reportMd = '';
        }
        loadReportRuns();
    } catch (e) {
        alert('删除失败: ' + e.message);
    }
}

async function viewReportRun(id) {
    try {
        const resp = await fetch(`/api/report/runs/${id}`);
        const data = await resp.json();
        if (!data.success) { alert(data.error); return; }
        reportMd = data.item.report_md || '';
        reportRunId = id;
        document.getElementById('report-result-meta').textContent =
            `run#${id} | ${escapeHtmlRp(data.item.created_at || '')} | ${data.item.status}`;
        document.getElementById('report-rendered').innerHTML = renderMarkdownRp(reportMd);
        document.getElementById('report-result-section').style.display = 'block';
        document.getElementById('report-result-section').scrollIntoView({behavior: 'smooth'});
    } catch (e) {
        alert('加载失败: ' + e.message);
    }
}

// ==================== 模板管理（表格化编辑器） ====================

async function loadReportTemplatesIntoSelect() {
    try {
        const resp = await fetch('/api/report/templates');
        const data = await resp.json();
        if (!data.success) return;
        const sel = document.getElementById('report-template-select');
        sel.innerHTML = '<option value="">— 请选择模板 —</option>' +
            data.items.filter(t => t.enabled).map(t =>
                `<option value="${t.id}">${escapeHtmlRp(t.name)}</option>`).join('');
    } catch (e) { /* 忽略 */ }
}

async function loadReportTemplates() {
    const list = document.getElementById('report-tpl-list');
    try {
        const resp = await fetch('/api/report/templates');
        const data = await resp.json();
        if (!data.success) { list.innerHTML = '<div class="hint">加载失败</div>'; return; }
        if (!data.items.length) { list.innerHTML = '<div class="hint">暂无模板，可在下方新增</div>'; return; }
        list.innerHTML = data.items.map(t => `
            <div class="report-run-item">
                <span class="report-run-title">${t.enabled ? '' : '（停用）'}${escapeHtmlRp(t.name)}</span>
                <span class="hint">触发词: ${(t.trigger_words || []).join('、') || '—'} | ${(t.outline || []).length} 章节</span>
                <span>
                    <button class="btn btn-secondary btn-sm" onclick="editReportTemplate(${t.id})">编辑</button>
                    <button class="btn btn-error btn-sm" onclick="deleteReportTemplate(${t.id})">删除</button>
                </span>
            </div>`).join('');
        window.__reportTemplates = data.items;
    } catch (e) {
        list.innerHTML = '<div class="hint">加载失败: ' + escapeHtmlRp(e.message) + '</div>';
    }
}

/** 表格编辑器状态：sections 数组 [{section_title, hint, questions:[{question, qa_id, has_sql, is_usable}]}] */
let tplSections = [];

async function editReportTemplate(id) {
    // 拉详情接口：问题带 qa_id/has_sql 实时状态
    let t = null;
    try {
        const resp = await fetch(`/api/report/templates/${id}`);
        const data = await resp.json();
        if (data.success) t = data.item;
    } catch (e) { /* 回退列表缓存 */ }
    if (!t) t = (window.__reportTemplates || []).find(x => x.id === id);
    if (!t) return;
    document.getElementById('tpl-id').value = t.id;
    document.getElementById('tpl-name').value = t.name;
    document.getElementById('tpl-triggers').value = (t.trigger_words || []).join(',');
    document.getElementById('tpl-remark').value = t.remark || '';
    tplSections = (t.outline || []).map(s => ({
        section_title: s.section_title || '',
        hint: s.hint || '',
        questions: (s.questions || []).map(q => (typeof q === 'object'
            ? {question: q.question || '', qa_id: q.qa_id || null,
               has_sql: !!q.has_sql || !!q.standard_sql, is_usable: q.is_usable !== false}
            : {question: q, qa_id: null, has_sql: false, is_usable: false})),
    }));
    if (!tplSections.length) tplSections = [{section_title: '', hint: '', questions: []}];
    renderTplEditor();
    document.getElementById('rptab-tpl').scrollIntoView({behavior: 'smooth'});
}

function resetReportTemplateForm() {
    for (const id of ['tpl-id', 'tpl-name', 'tpl-triggers', 'tpl-remark'])
        document.getElementById(id).value = '';
    tplSections = [{section_title: '', hint: '', questions: []}];
    renderTplEditor();
}

/** 渲染章节-问题表格编辑器 */
function renderTplEditor() {
    const box = document.getElementById('tpl-sections-editor');
    box.innerHTML = tplSections.map((s, si) => `
        <div class="tpl-sec-block">
            <div class="tpl-sec-head">
                <input type="text" class="search-input" placeholder="章节名，如：设备概况"
                    value="${escapeAttrRp(s.section_title)}" onchange="tplSections[${si}].section_title=this.value">
                <input type="text" class="search-input" placeholder="取数提示（可选）"
                    value="${escapeAttrRp(s.hint)}" onchange="tplSections[${si}].hint=this.value">
                <button class="btn btn-error btn-sm" onclick="tplRemoveSection(${si})">删章节</button>
            </div>
            <table class="tpl-q-table">
                <thead><tr><th style="width:58%;">业务问题（写入问答对库）</th><th>SQL 状态</th><th>操作</th></tr></thead>
                <tbody>
                    ${s.questions.map((q, qi) => `
                    <tr>
                        <td><input type="text" class="search-input" style="width:100%;" value="${escapeAttrRp(q.question)}"
                            onchange="tplEditQuestion(${si},${qi},this.value)"></td>
                        <td>${q.qa_id
                            ? (q.has_sql ? '<span class="report-src-badge qa">已验证#' + q.qa_id + '</span>'
                                         : '<span class="report-src-badge gen">待生成#' + q.qa_id + '</span>')
                            : '<span class="hint">新问题</span>'}</td>
                        <td><button class="btn btn-error btn-sm" onclick="tplRemoveQuestion(${si},${qi})">删除</button></td>
                    </tr>`).join('')}
                    <tr><td colspan="3">
                        <input type="text" class="search-input" style="width:70%;" placeholder="新增业务问题，回车添加"
                            onkeydown="if(event.key==='Enter'){tplAddQuestion(${si},this);return false;}">
                    </td></tr>
                </tbody>
            </table>
        </div>`).join('');
}

function tplAddSection() {
    tplSections.push({section_title: '', hint: '', questions: []});
    renderTplEditor();
}

function tplRemoveSection(si) {
    tplSections.splice(si, 1);
    renderTplEditor();
}

function tplAddQuestion(si, input) {
    const text = input.value.trim();
    if (!text) return;
    tplSections[si].questions.push({question: text, qa_id: null, has_sql: false, is_usable: false});
    renderTplEditor();
}

function tplRemoveQuestion(si, qi) {
    tplSections[si].questions.splice(qi, 1);
    renderTplEditor();
}

function tplEditQuestion(si, qi, text) {
    tplSections[si].questions[qi].question = text.trim();
}

async function saveReportTemplate() {
    const id = document.getElementById('tpl-id').value;
    const name = document.getElementById('tpl-name').value.trim();
    if (!name) { alert('模板名称不能为空'); return; }
    const outline = tplSections
        .map(s => ({
            section_title: (s.section_title || '').trim(),
            hint: (s.hint || '').trim(),
            questions: (s.questions || [])
                .map(q => {
                    const text = (q.question || '').trim();
                    if (!text) return null;
                    const item = {question: text};
                    if (q.qa_id) item.qa_id = q.qa_id;
                    if (q._sql) item.standard_sql = q._sql;  // 提炼携带的验证 SQL
                    return item;
                })
                .filter(Boolean),
        }))
        .filter(s => s.section_title);
    const payload = {
        name,
        trigger_words: document.getElementById('tpl-triggers').value.split(/[,，]/).map(s => s.trim()).filter(Boolean),
        outline,
        remark: document.getElementById('tpl-remark').value.trim(),
        enabled: true,
    };
    try {
        const resp = await fetch(id ? `/api/report/templates/${id}` : '/api/report/templates', {
            method: id ? 'PUT' : 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const data = await resp.json();
        if (!data.success) { alert('保存失败: ' + data.error); return; }
        if (data.sync) {
            const s = data.sync;
            alert(`模板已保存。问答对联动：新增 ${s.inserted} 条，变更 ${s.changed} 条，删除 ${s.deleted} 条（已置无效），关联已有 ${s.linked} 条。\n新增/变更的问题正在后台生成 SQL。`);
        }
        resetReportTemplateForm();
        loadReportTemplates();
        loadReportTemplatesIntoSelect();
    } catch (e) {
        alert('保存失败: ' + e.message);
    }
}

async function deleteReportTemplate(id) {
    if (!confirm('确认删除该模板？关联的问答对会置为无效。')) return;
    try {
        const resp = await fetch(`/api/report/templates/${id}`, {method: 'DELETE'});
        const data = await resp.json();
        if (!data.success) { alert('删除失败: ' + data.error); return; }
        loadReportTemplates();
        loadReportTemplatesIntoSelect();
    } catch (e) {
        alert('删除失败: ' + e.message);
    }
}


// ==================== 极简 Markdown 渲染（无外部依赖） ====================

function escapeHtmlRp(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function escapeAttrRp(s) { return escapeHtmlRp(s).replace(/'/g, '&#39;'); }

function renderInlineRp(s) {
    return escapeHtmlRp(s)
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/`([^`]+)`/g, '<code>$1</code>');
}

function renderMarkdownRp(md) {
    if (!md) return '';
    const lines = md.replace(/\r\n/g, '\n').split('\n');
    let html = '', i = 0;
    while (i < lines.length) {
        const line = lines[i];
        // 表格块
        if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s\-:|]+\|\s*$/.test(lines[i + 1])) {
            const headers = line.split('|').slice(1, -1).map(c => c.trim());
            i += 2;
            const rows = [];
            while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
                rows.push(lines[i].split('|').slice(1, -1).map(c => c.trim()));
                i++;
            }
            html += '<table class="report-table"><thead><tr>' +
                headers.map(h => `<th>${renderInlineRp(h)}</th>`).join('') + '</tr></thead><tbody>' +
                rows.map(r => '<tr>' + r.map(c => `<td>${renderInlineRp(c)}</td>`).join('') + '</tr>').join('') +
                '</tbody></table>';
            continue;
        }
        const h = line.match(/^(#{1,4})\s+(.*)$/);
        if (h) { html += `<h${h[1].length}>${renderInlineRp(h[2])}</h${h[1].length}>`; i++; continue; }
        if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
            html += '<ul>';
            while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
                html += `<li>${renderInlineRp(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ''))}</li>`;
                i++;
            }
            html += '</ul>';
            continue;
        }
        if (/^\s*---+\s*$/.test(line)) { html += '<hr>'; i++; continue; }
        if (line.trim() === '') { i++; continue; }
        html += `<p>${renderInlineRp(line)}</p>`;
        i++;
    }
    return html;
}
