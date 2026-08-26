// ==================== 素材提资（四步工作流：上传→预览→执行→复核） ====================

const provisionState = { runId: null, fileName: null };

function pvSetStep(n) {
    document.querySelectorAll('#pv-steps .pv-step').forEach(s => {
        s.classList.toggle('active', +s.dataset.step <= n);
    });
}

function pvShow(id, show) {
    const el = document.getElementById(id);
    if (el) el.style.display = show === false ? 'none' : 'block';
}

function pvEsc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/** 溯源徽标：4 色（direct绿/system蓝/llm紫/manual橙），点击显示 source_ref */
function pvBadge(kind, ref) {
    const label = {direct: '直接转换', system: '系统推导', llm: 'LLM标注', manual: '人工标注'}[kind] || kind;
    return `<span class="pv-badge pv-${pvEsc(kind)}" title="点击看溯源" onclick="alert('溯源：${pvEsc(ref || kind)}')">${label}</span>`;
}

async function uploadProvision() {
    const fileInput = document.getElementById('pv-file');
    const pathInput = document.getElementById('pv-path');
    const msg = document.getElementById('pv-upload-msg');
    const btn = document.getElementById('pv-upload-btn');
    msg.textContent = '上传解析中…';
    btn.disabled = true;
    try {
        let resp;
        if (fileInput.files && fileInput.files.length) {
            const fd = new FormData();
            fd.append('file', fileInput.files[0]);
            resp = await fetch('/api/provision/upload', {method: 'POST', body: fd});
        } else if (pathInput.value.trim()) {
            resp = await fetch('/api/provision/upload', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({path: pathInput.value.trim()})
            });
        } else {
            msg.textContent = '请选择文件或填写服务器路径';
            return;
        }
        const data = await resp.json();
        if (!data.success) { msg.textContent = '失败：' + (data.error || resp.status); return; }
        provisionState.runId = data.run_id;
        provisionState.fileName = data.file_name;
        msg.textContent = `解析完成：${data.file_name}（run_id=${data.run_id}）`;
        renderValidation(data.validation);
        pvSetStep(2);
        await loadPreview(data.run_id);
    } catch (e) {
        msg.textContent = '异常：' + e;
    } finally {
        btn.disabled = false;
    }
}

function renderValidation(v) {
    pvShow('pv-validation-block');
    const sum = document.getElementById('pv-validation-summary');
    const iss = document.getElementById('pv-validation-issues');
    sum.innerHTML = '<table class="pv-table"><tr><th>Sheet</th><th>ok</th><th>warn</th><th>fail</th></tr>' +
        Object.entries(v || {}).map(([k, s]) =>
            `<tr><td>${pvEsc(k)}</td><td class="pv-ok">${s.ok}</td><td class="pv-warn">${s.warn}</td><td class="pv-fail">${s.fail}</td></tr>`
        ).join('') + '</table>';
}

async function loadPreview(runId) {
    const resp = await fetch(`/api/provision/${runId}/preview`);
    const data = await resp.json();
    if (!data.success) { alert('预览失败：' + (data.error || resp.status)); return; }
    pvShow('pv-preview-block');
    const iss = document.getElementById('pv-validation-issues');
    const allIssues = data.sheets.flatMap(sh => (sh.issues || []).map(i => ({sheet: sh.key, ...i})));
    iss.innerHTML = allIssues.length
        ? '<table class="pv-table"><tr><th>Sheet</th><th>行</th><th>级别</th><th>说明</th></tr>' +
          allIssues.map(i => `<tr><td>${pvEsc(i.sheet)}</td><td>${i.row}</td>` +
            `<td class="pv-${i.level === 'fail' ? 'fail' : 'warn'}">${pvEsc(i.level)}</td><td>${pvEsc(i.msg)}</td></tr>`).join('') +
          '</table>'
        : '<div class="hint">无校验告警</div>';
    const box = document.getElementById('pv-preview');
    box.innerHTML = data.sheets.map(sh => `
        <div class="pv-sheet">
            <h4>${pvEsc(sh.title)} <span class="hint">ok=${sh.stats.ok} warn=${sh.stats.warn} fail=${sh.stats.fail}</span></h4>
            ${sh.rows.map(r => `
                <div class="pv-row ${r.status === 'fail' ? 'pv-row-fail' : ''}">
                    <span class="pv-row-no">#${r.src_row}</span>
                    ${r.fields.map(f => `<span class="pv-field"><b>${pvEsc(f.name)}</b>=${pvEsc(String(f.value == null ? '' : f.value)).slice(0, 60)} ${pvBadge(f.kind, f.ref)}</span>`).join('')}
                </div>`).join('')}
        </div>`).join('');
}

async function executeProvision() {
    if (!provisionState.runId) return;
    pvSetStep(3);
    pvShow('pv-execute-block');
    document.getElementById('pv-execute-btn').disabled = true;
    const log = document.getElementById('pv-execute-log');
    const resultBox = document.getElementById('pv-execute-result');
    log.innerHTML = '';
    resultBox.innerHTML = '';
    const resp = await fetch(`/api/provision/${provisionState.runId}/execute`, {method: 'POST'});
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        resultBox.innerHTML = `<div class="pv-fail">执行失败：${pvEsc(err.error || resp.status)}</div>`;
        return;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
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
            if (evt.error) {
                resultBox.innerHTML = `<div class="pv-fail">执行失败：${pvEsc(evt.error)}</div>`;
            } else if (evt.done) {
                const r = evt.result || {};
                resultBox.innerHTML = `<div class="pv-ok">执行完成：转换 ${JSON.stringify(r.converted)}；` +
                    `跳过 ${JSON.stringify(r.skipped || {})}；溯源 ${r.provenance_rows} 条；` +
                    `LLM 标注 ${r.llm_annotated} 条、降级 ${r.llm_degraded} 条；待复核 ${r.pending_review} 条</div>`;
                pvSetStep(4);
                await loadReview(provisionState.runId);
            } else if (evt.msg) {
                log.innerHTML += `<div>[${pvEsc(evt.stage || '')}] ${pvEsc(evt.msg)}</div>`;
                log.scrollTop = log.scrollHeight;
            }
        }
    }
}

// 当前复核队列条目（供批量同意收集 id）
let pvReviewItems = [];

async function loadReview(runId) {
    const resp = await fetch(`/api/provision/${runId}/review`);
    const data = await resp.json();
    if (!data.success) { alert('复核队列加载失败：' + (data.error || resp.status)); return; }
    pvShow('pv-review-block');
    const box = document.getElementById('pv-review');
    pvReviewItems = data.items || [];
    if (!pvReviewItems.length) {
        box.innerHTML = '<div class="hint">无待复核记录</div>';
        return;
    }
    // 按复核优先级分组（高→中→低，服务端已排序）
    const groups = {'高': [], '中': [], '低': []};
    for (const it of pvReviewItems) (groups[it.priority] || groups['低']).push(it);
    box.innerHTML = pvRenderPriSection('高', groups['高'], 50) +
                    pvRenderPriSection('中', groups['中']) +
                    pvRenderPriSection('低', groups['低']);
    filterReviewRows();
}

/** 优先级分组区块：高优先级逐条人工确认（每批最多显示 cap 条）；中/低支持批量同意 */
function pvRenderPriSection(pri, items, cap) {
    if (!items.length) return '';
    const cls = {'高': 'pv-pri-high', '中': 'pv-pri-mid', '低': 'pv-pri-low'}[pri];
    const shown = cap ? items.slice(0, cap) : items;
    const batchBtn = (pri === '高') ? '' :
        ` <button class="btn btn-primary btn-sm" onclick="batchConfirm('${pri}')">批量同意本节（${items.length} 条）</button>`;
    const capHint = (cap && items.length > cap) ?
        ` <span class="hint">共 ${items.length} 条，本批显示前 ${cap} 条，处理后可刷新换下一批</span>` : '';
    const rows = shown.map(it => `<tr id="pv-rev-${it.id}">
        <td>${it.id}</td><td>${pvEsc(it.sheet_name)}</td><td>${it.src_row}</td>
        <td>${pvEsc(it.target_table)}<br><span class="hint">${pvEsc((it.target_key || '').slice(0, 40))}</span></td>
        <td>${pvEsc(it.field_name)}</td>
        <td class="hint">${pvEsc(it.priority_reason || '')}${(it.source_ref || '').startsWith('冲突') ? '<br>' + pvEsc(it.source_ref) : ''}</td>
        <td><input class="pv-value-input" id="pv-val-${it.id}" value="${pvEsc(it.field_value || '')}"></td>
        <td><button class="btn btn-primary btn-sm" onclick="confirmProv(${it.id})">确认</button></td>
    </tr>`).join('');
    return `<div class="pv-pri-section">
        <h4><span class="pv-pri ${cls}">${pri}优先级</span>${items.length} 条${batchBtn}${capHint}</h4>
        <table class="pv-table"><tr><th>ID</th><th>Sheet</th><th>行</th><th>目标</th><th>字段</th>
        <th>优先级原因/冲突</th><th>值（可编辑）</th><th>操作</th></tr>${rows}</table>
    </div>`;
}

/** 批量同意一个优先级分组（仅中/低；高优先级服务端会拒绝） */
async function batchConfirm(pri) {
    const ids = pvReviewItems.filter(it => it.priority === pri).map(it => it.id);
    if (!ids.length) return;
    if (!confirm(`确认批量同意「${pri}」优先级的 ${ids.length} 条？\n将按模板值覆盖库内对应字段。`)) return;
    const resp = await fetch('/api/provision/review/batch-confirm', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ids})
    });
    const data = await resp.json();
    if (!data.success) { alert('批量确认失败：' + (data.error || resp.status)); return; }
    alert(`已确认 ${data.confirmed} 条` +
        (data.refused && data.refused.length ? `；服务端拒绝 ${data.refused.length} 条（高优先级需逐条确认）` : '') +
        (data.failed && data.failed.length ? `；失败 ${data.failed.length} 条` : ''));
    await initProvisionPage();
}

/** 进入素材提资页：加载有 pending 的历史 run（此前复核队列仅当次执行后可见，历史 pending 无入口） */
async function initProvisionPage() {
    try {
        const resp = await fetch('/api/provision/runs');
        const data = await resp.json();
        if (!data.success) return;
        const withPending = (data.items || []).filter(r => r.pending > 0);
        const sel = document.getElementById('pv-review-run');
        if (!sel || !withPending.length) return;
        sel.innerHTML = withPending.map(r =>
            `<option value="${pvEsc(r.run_id)}">${pvEsc(r.file_name || '')}｜${pvEsc(r.run_id)}｜待复核 ${r.pending}</option>`
        ).join('');
        // 不覆盖正在进行的当次会话（刚执行完的 run 优先保持）
        if (provisionState.runId && withPending.some(r => r.run_id === provisionState.runId)) {
            sel.value = provisionState.runId;
        } else {
            provisionState.runId = sel.value;
        }
        pvShow('pv-review-block');
        await loadReview(sel.value);
    } catch (e) { /* 静默：不影响上传/执行主流程 */ }
}

function onReviewRunChange() {
    const sel = document.getElementById('pv-review-run');
    provisionState.runId = sel.value;
    loadReview(sel.value);
}

/** 复核队列客户端过滤（目标表/字段/值关键字） */
function filterReviewRows() {
    const kw = (document.getElementById('pv-review-filter').value || '').trim().toLowerCase();
    document.querySelectorAll('#pv-review tr[id^="pv-rev-"]').forEach(tr => {
        tr.style.display = (!kw || tr.textContent.toLowerCase().includes(kw)) ? '' : 'none';
    });
}

async function confirmProv(provId) {
    const input = document.getElementById(`pv-val-${provId}`);
    const resp = await fetch(`/api/provision/review/${provId}/confirm`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({field_value: input ? input.value : null})
    });
    const data = await resp.json();
    if (!data.success) { alert('确认失败：' + (data.error || resp.status)); return; }
    const tr = document.getElementById(`pv-rev-${provId}`);
    if (tr) {
        tr.classList.add('pv-row-confirmed');
        tr.querySelector('td:last-child').innerHTML = '<span class="pv-ok">confirmed</span>';
    }
}

async function finishProvision() {
    if (!provisionState.runId) return;
    const resp = await fetch(`/api/provision/${provisionState.runId}/finish`, {method: 'POST'});
    const data = await resp.json();
    if (!data.success) { alert('收尾失败：' + (data.error || resp.status)); return; }
    alert(`本批次已完成。剩余待复核：${data.pending_review} 条`);
}
