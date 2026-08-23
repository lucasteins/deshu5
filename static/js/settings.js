/* 设置页：数据库配置（MySQL）+ LLM API 配置（多 Provider） */

let _settingsCache = null;  // GET /api/settings/llm 的全部配置（脱敏）

async function openSettings() {
    const modal = document.getElementById('settings-modal');
    modal.style.display = 'flex';
    document.getElementById('settings-test-result').textContent = '';
    loadDbSettings();
    try {
        const resp = await fetch('/api/settings/llm');
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || '加载失败');
        _settingsCache = data;
        document.getElementById('settings-provider').value = data.active;
        fillSettingsForm(data.active);
        updateSettingsStatus();
        loadWorkflowPresets();
    } catch (e) {
        document.getElementById('settings-status').textContent = '配置加载失败: ' + e.message;
    }
}

/** 加载当前 MySQL 连接配置（脱敏）+ 档位列表 + 三库连通状态 */
async function loadDbSettings() {
    const status = document.getElementById('db-settings-status');
    try {
        const resp = await fetch('/api/settings/db');
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || '加载失败');
        document.getElementById('db-settings-host').value = data.host;
        document.getElementById('db-settings-port').value = data.port;
        document.getElementById('db-settings-user').value = data.user;
        document.getElementById('db-settings-password').value = '';
        // 档位选择器
        const sel = document.getElementById('db-profile-select');
        const note = document.getElementById('db-profile-note');
        sel.innerHTML = (data.profiles || []).map(p =>
            `<option value="${p.name}">${p.label}（${p.business} / ${p.governance}）</option>`).join('');
        sel.value = data.current_profile;
        sel.disabled = false;
        document.getElementById('db-profile-btn').disabled = false;
        const cur = (data.profiles || []).find(p => p.name === data.current_profile);
        note.innerHTML = cur
            ? `当前：<b>${cur.label}</b>　${cur.business} / ${cur.governance} / ${cur.log}　` +
              (cur.connected ? '<span style="color:#2e7d32">三库连接正常</span>' : '<span style="color:#c62828">存在连接异常</span>')
            : '';
        const parts = (data.databases || []).map(d =>
            `${d.name}: ${d.connected ? '✓' : '✗ ' + (d.error || '')}`);
        document.getElementById('db-databases-status').textContent = parts.join('　|　');
        const allOk = (data.databases || []).every(d => d.connected);
        status.textContent = `数据库（MySQL）：${data.current_label} ${allOk ? '连接正常' : '存在连接异常'} @ ${data.host}:${data.port}`;
        status.style.color = allOk ? '#2e7d32' : '#c62828';
    } catch (e) {
        status.textContent = '数据库配置加载失败: ' + e.message;
        status.style.color = '#c62828';
    }
}

/** 切换数据库档位（生产/暂存），后端失效缓存并重载，免重启 */
async function switchDbProfile() {
    const sel = document.getElementById('db-profile-select');
    const btn = document.getElementById('db-profile-btn');
    const note = document.getElementById('db-profile-note');
    const name = sel.value;
    if (!name) return;
    btn.disabled = true;
    note.innerHTML = '切换中（缓存重载中）…';
    try {
        const resp = await fetch('/api/settings/db/profile', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile: name }),
        });
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || '切换失败');
        sel.value = data.current_profile;
        note.innerHTML = `已切换到 <b>${data.current_label}</b>（缓存已重载）`;
        note.style.color = '#2e7d32';
        // 刷新三库状态展示
        const parts = (data.databases || []).map(d =>
            `${d.name}: ${d.connected ? '✓' : '✗ ' + (d.error || '')}`);
        document.getElementById('db-databases-status').textContent = parts.join('　|　');
        document.getElementById('db-settings-status').textContent =
            `数据库（MySQL）：${data.current_label} 已生效`;
        document.getElementById('db-settings-status').style.color = '#2e7d32';
    } catch (e) {
        note.innerHTML = '切换失败: ' + e.message;
        note.style.color = '#c62828';
    } finally {
        btn.disabled = false;
    }
}

/** 按表单给定配置测试数据库连通性（不落库；密码留空=沿用当前配置） */
async function testDbConnection() {
    const btn = document.getElementById('db-test-btn');
    const result = document.getElementById('db-test-result');
    btn.disabled = true;
    result.textContent = '测试中…';
    result.style.color = '#666';
    try {
        const resp = await fetch('/api/settings/db/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                host: document.getElementById('db-settings-host').value.trim(),
                port: document.getElementById('db-settings-port').value.trim(),
                user: document.getElementById('db-settings-user').value.trim(),
                password: document.getElementById('db-settings-password').value,
            }),
        });
        const data = await resp.json();
        if (data.ok) {
            result.textContent = `✓ 连接成功（${data.elapsed_ms}ms）MySQL ${data.server_version}`;
            result.style.color = '#2e7d32';
        } else {
            result.textContent = '✗ 连接失败: ' + (data.error || '未知错误');
            result.style.color = '#c62828';
        }
    } catch (e) {
        result.textContent = '✗ 请求失败: ' + e.message;
        result.style.color = '#c62828';
    } finally {
        btn.disabled = false;
    }
}

/** 加载工作流预设列表并选中当前生效项 */
async function loadWorkflowPresets() {
    const sel = document.getElementById('settings-workflow');
    const note = document.getElementById('settings-workflow-note');
    try {
        const resp = await fetch('/api/workflows');
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || '加载失败');
        sel.innerHTML = (data.presets || []).map(p =>
            `<option value="${p.name}">${p.name}</option>`).join('');
        sel.value = data.current;
        note.textContent = describeWorkflow(data.presets, data.current);
        note.style.color = '#666';
    } catch (e) {
        note.textContent = '工作流预设加载失败: ' + e.message;
        note.style.color = '#c62828';
    }
}

/** 预设描述：备注 + 与 default 的差异键摘要 */
function describeWorkflow(presets, current) {
    const p = (presets || []).find(x => x.name === current);
    if (!p) return '';
    const keys = Object.keys(p.overrides || {});
    const diff = keys.length ? `差异键: ${keys.join(', ')}` : '与内置默认行为一致';
    return `${p.note ? p.note + '；' : ''}${diff}`;
}

/** 切换工作流预设（后端热生效：下次生成按新预设重建生成器） */
async function onWorkflowChange() {
    const sel = document.getElementById('settings-workflow');
    const note = document.getElementById('settings-workflow-note');
    const name = sel.value;
    note.textContent = '切换中…';
    note.style.color = '#666';
    try {
        const resp = await fetch('/api/workflows', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || '切换失败');
        note.textContent = `已切换到 ${name}（下次生成起生效）`;
        note.style.color = '#2e7d32';
    } catch (e) {
        note.textContent = '切换失败: ' + e.message;
        note.style.color = '#c62828';
    }
}

function closeSettings() {
    document.getElementById('settings-modal').style.display = 'none';
}

function onSettingsProviderChange() {
    fillSettingsForm(document.getElementById('settings-provider').value);
}

function fillSettingsForm(provider) {
    const all = (_settingsCache && _settingsCache.providers) || {};
    const p = all[provider] || {};
    document.getElementById('settings-api-url').value = p.api_url || '';
    document.getElementById('settings-model').value = p.model || '';
    document.getElementById('settings-temperature').value =
        (p.temperature === null || p.temperature === undefined) ? '' : p.temperature;
    const keyInput = document.getElementById('settings-api-key');
    keyInput.value = '';
    keyInput.placeholder = p.has_key ? `当前已配置（${p.api_key_masked}），留空表示不修改` : '未配置，请粘贴 API Key';
}

function updateSettingsStatus(msg) {
    const el = document.getElementById('settings-status');
    if (msg) { el.textContent = msg; return; }
    const active = _settingsCache && _settingsCache.active;
    const p = (_settingsCache && _settingsCache.providers || {})[active] || {};
    el.textContent = `当前生效：${active} / ${p.model || '-'}${p.has_key ? '' : '（未配置 Key）'}`;
}

function collectSettingsPayload() {
    const t = document.getElementById('settings-temperature').value.trim();
    return {
        provider: document.getElementById('settings-provider').value,
        api_url: document.getElementById('settings-api-url').value.trim(),
        model: document.getElementById('settings-model').value.trim(),
        api_key: document.getElementById('settings-api-key').value.trim(),
        temperature: t === '' ? null : parseFloat(t),
    };
}

async function testSettingsConnection() {
    const btn = document.getElementById('settings-test-btn');
    const result = document.getElementById('settings-test-result');
    btn.disabled = true;
    result.textContent = '测试中…';
    try {
        const resp = await fetch('/api/settings/llm/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(collectSettingsPayload()),
        });
        const data = await resp.json();
        if (data.ok) {
            result.textContent = `✓ 连接成功（${data.elapsed_ms}ms）模型 ${data.model}${data.reasoning ? '（推理模型）' : ''}`;
            result.style.color = '#2e7d32';
        } else {
            result.textContent = '✗ 连接失败: ' + (data.error || '未知错误');
            result.style.color = '#c62828';
        }
    } catch (e) {
        result.textContent = '✗ 请求失败: ' + e.message;
        result.style.color = '#c62828';
    } finally {
        btn.disabled = false;
    }
}

async function saveSettings() {
    const btn = document.getElementById('settings-save-btn');
    btn.disabled = true;
    try {
        const payload = collectSettingsPayload();
        payload.set_active = true;
        const resp = await fetch('/api/settings/llm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || '保存失败');
        _settingsCache = data;
        updateSettingsStatus();
        fillSettingsForm(data.active);
        alert(`已保存并启用：${data.active}`);
    } catch (e) {
        alert('保存失败: ' + e.message);
    } finally {
        btn.disabled = false;
    }
}
