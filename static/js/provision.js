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

async function loadReview(runId) {
    const resp = await fetch(`/api/provision/${runId}/review`);
    const data = await resp.json();
    if (!data.success) { alert('复核队列加载失败：' + (data.error || resp.status)); return; }
    pvShow('pv-review-block');
    const box = document.getElementById('pv-review');
    if (!data.items.length) {
        box.innerHTML = '<div class="hint">无待复核记录</div>';
        return;
    }
    box.innerHTML = '<table class="pv-table"><tr><th>ID</th><th>Sheet</th><th>行</th><th>目标</th><th>字段</th>' +
        '<th>来源</th><th>值（可编辑）</th><th>操作</th></tr>' +
        data.items.map(it => `<tr id="pv-rev-${it.id}">
            <td>${it.id}</td><td>${pvEsc(it.sheet_name)}</td><td>${it.src_row}</td>
            <td>${pvEsc(it.target_table)}<br><span class="hint">${pvEsc((it.target_key || '').slice(0, 30))}</span></td>
            <td>${pvEsc(it.field_name)}</td>
            <td>${pvBadge(it.source_kind, it.source_ref)}</td>
            <td><input class="pv-value-input" id="pv-val-${it.id}" value="${pvEsc(it.field_value || '')}"></td>
            <td><button class="btn btn-primary btn-sm" onclick="confirmProv(${it.id})">确认</button></td>
        </tr>`).join('') + '</table>';
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
