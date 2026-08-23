// 智能问数训练系统 - 前端主逻辑
const API_BASE = '';

// 当前会话状态
let currentSession = {
    sessionId: null,
    qaId: null,
    question: null,
    sql: null,
    resultPreview: null,
    mode: 'training', // training | qa
    generated: false, // 题目是否来自智能出题
    questionRated: false,
    questionRating: null,
    questionSource: null
};

// 页面加载时初始化
document.addEventListener('DOMContentLoaded', () => {
    loadStats();
    loadErrorList();
    loadQALibList();
});

// 切换模式
function switchMode(mode) {
    document.querySelectorAll('.mode-section').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    
    document.getElementById('mode-' + mode).classList.add('active');
    document.querySelector(`.nav-item[data-mode="${mode}"]`).classList.add('active');
    
    if (mode === 'stats') loadStats();
    if (mode === 'errors') loadErrorList();
    if (mode === 'qa-lib') loadQALibList();
    if (mode === 'resource') initResourceModule();
}

// ==================== 训练模式 ====================

async function generateQuestion() {
    const btn = document.getElementById('btn-generate');
    btn.disabled = true;
    btn.textContent = '生成中...';
    updateFlowStep(1);
    resetContextPanel();

    try {
        const response = await fetch('/api/next-question');
        const data = await response.json();

        if (!data.success) {
            alert('生成失败: ' + data.error);
            return;
        }

        currentSession = {
            sessionId: data.session_id,
            qaId: data.qa_id,
            question: data.question,
            sql: null,
            raw_sql: null,
            resultPreview: null,
            mode: 'training',
            generated: data.generated,
            questionRated: false,
            questionRating: null,
            questionSource: data.source
        };

        document.getElementById('difficulty-badge').textContent = '难度: ' + (data.difficulty || '进阶题');
        document.getElementById('question-text').textContent = data.question;

        // 隐藏 SQL/结果/判断按钮，等待评价后生成
        document.getElementById('training-sql-section').style.display = 'none';
        document.getElementById('training-result-section').style.display = 'none';
        resetThinkingWindow('training');
        document.getElementById('generated-sql').textContent = '-- 等待生成...';
        document.getElementById('generated-sql-raw').textContent = '';
        document.getElementById('btn-generate-sql').style.display = 'none';
        document.getElementById('judge-actions').style.display = 'none';
        document.getElementById('btn-correct').disabled = true;
        document.getElementById('btn-error').disabled = true;
        document.getElementById('btn-skip').disabled = true;

        // 显示问题合理性评价区
        document.getElementById('question-evaluation').style.display = 'block';
        document.getElementById('question-feedback').value = '';
        updateFlowStep(2);

    } catch (e) {
        alert('请求失败: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '生成题目';
    }
}

async function evaluateQuestion(rating) {
    if (!currentSession.sessionId) return;

    const feedback = document.getElementById('question-feedback').value.trim();

    try {
        const response = await fetch('/api/evaluate-question', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                session_id: currentSession.sessionId,
                rating: rating,
                feedback: feedback
            })
        });

        const data = await response.json();
        if (!data.success) {
            alert('评价提交失败: ' + data.error);
            return;
        }

        currentSession.questionRated = true;
        currentSession.questionRating = rating;

        if (rating === '不合理') {
            // 不合理题目直接跳过，出下一题
            generateQuestion();
            return;
        }

        // 合理：隐藏评价区，直接生成 SQL
        document.getElementById('question-evaluation').style.display = 'none';
        updateFlowStep(3);
        generateTrainingSQL();

    } catch (e) {
        alert('评价请求失败: ' + e.message);
    }
}

async function generateTrainingSQL() {
    if (!currentSession.sessionId || !currentSession.question) return;

    const btn = document.getElementById('btn-generate-sql');
    btn.disabled = true;
    btn.textContent = '生成中...';

    try {
        // SSE 流式生成：阶段耗时实时显示到悬浮窗，思考事件实时输出到思考窗口
        resetLiveTimings('cp');
        resetThinkingWindow('training');
        const data = await streamGenerateSQL({
            question: currentSession.question,
            no_reference: currentSession.generated,  // 生成题不带参考 SQL
            mode: 'training',
            qa_id: currentSession.qaId,
            generated: currentSession.generated
        }, (evt) => {
            upsertTimingRow('cp', evt.stage, evt.ms);
        }, (t) => {
            appendThinking('training', t);
        });

        if (!data || !data.success) {
            showGenerationError('training', (data && data.error) || '未知错误', (data && (data.raw_sql || data.sql)) || '');
            return;
        }
        clearGenerationError('training');

        currentSession.sessionId = data.session_id;
        currentSession.sql = data.sql;
        currentSession.raw_sql = data.raw_sql || data.sql;
        currentSession.resultPreview = data.result_preview;
        currentSession.tables_involved = data.tables_involved || [];

        // 显示 SQL
        document.getElementById('training-sql-section').style.display = 'block';
        document.getElementById('generated-sql').textContent = data.sql;
        const toggleBtn = document.getElementById('btn-toggle-training-sql');
        if (data.raw_sql && data.raw_sql !== data.sql) {
            document.getElementById('generated-sql-raw').textContent = data.raw_sql;
            toggleBtn.style.display = 'inline-block';
            toggleBtn.textContent = '查看原始SQL';
            document.getElementById('generated-sql').style.display = 'block';
            document.getElementById('generated-sql-raw').style.display = 'none';
        } else {
            toggleBtn.style.display = 'none';
            document.getElementById('generated-sql-raw').textContent = '';
        }

        // 显示结果
        document.getElementById('training-result-section').style.display = 'block';
        renderResultTable('result-preview', data.result_preview);

        // 显示判断按钮
        btn.style.display = 'none';
        document.getElementById('judge-actions').style.display = 'inline-flex';
        document.getElementById('btn-correct').disabled = false;
        document.getElementById('btn-error').disabled = false;
        document.getElementById('btn-skip').disabled = false;
        updateFlowStep(4);
        renderContextPanel(data);

    } catch (e) {
        alert('SQL 请求失败: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '生成 SQL';
    }
}

async function submitJudgment(judgment) {
    if (!currentSession.sessionId) return;
    
    try {
        const response = await fetch('/api/judge', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                session_id: currentSession.sessionId,
                judgment: judgment,
                feedback: ''
            })
        });
        
        const data = await response.json();
        if (data.success) {
            if (judgment === '错误') {
                openModal();
            } else {
                // 正确或跳过，直接生成下一题
                generateQuestion();
            }
        }
    } catch (e) {
        alert('提交失败: ' + e.message);
    }
}

// ==================== 智能问答模式 ====================

async function generateSQLFromQuestion() {
    const input = document.getElementById('qa-question-input');
    const question = input.value.trim();
    if (!question) {
        alert('请输入业务问题');
        return;
    }
    
    // 显示生成过程，重置上下文面板与思考窗口
    document.getElementById('generation-process').style.display = 'block';
    resetContextPanel('qa-cp');
    resetThinkingWindow('qa');
    document.getElementById('rag-pairs').style.display = 'none';
    document.getElementById('qa-sql-output').style.display = 'none';
    document.getElementById('qa-result-output').style.display = 'none';
    document.getElementById('qa-actions').style.display = 'none';
    
    resetSteps();
    
    try {
        // SSE 流式生成：阶段进度实时更新到悬浮窗
        resetLiveTimings('qa-cp');
        const stageMap = {
            'rag': ['step-rag', 'step-schema'],
            'schema': ['step-schema', 'step-llm'],
            'llm': ['step-llm', 'step-execute'],
            'exec': ['step-execute', 'step-review'],
            'audit': ['step-review', null]
        };
        updateStep('step-rag', '进行中');
        const data = await streamGenerateSQL({question: question}, (evt) => {
            upsertTimingRow('qa-cp', evt.stage, evt.ms);
            const mapping = stageMap[evt.stage];
            if (mapping) {
                if (mapping[0]) updateStep(mapping[0], '完成', evt.ms);
                if (mapping[1]) updateStep(mapping[1], '进行中');
            }
        }, (t) => {
            appendThinking('qa', t);
        });

        if (!data || !data.success) {
            showGenerationError('qa', (data && data.error) || '未知错误', (data && (data.raw_sql || data.sql)) || '');
            return;
        }
        clearGenerationError('qa');
        // 兜底：未收到事件的阶段标记完成
        ['step-rag', 'step-schema', 'step-llm', 'step-execute', 'step-review'].forEach(id => {
            const st = document.getElementById(id).querySelector('.step-status').textContent;
            if (st === '等待中' || st === '进行中') updateStep(id, '完成');
        });
        
        currentSession = {
            sessionId: data.session_id,
            question: question,
            sql: data.sql,
            raw_sql: data.raw_sql || data.sql,  // 保存原始SQL
            resultPreview: data.result_preview,
            mode: 'qa'
        };
        
        // 显示参考问答对
        if (data.retrieved_pairs && data.retrieved_pairs.length > 0) {
            document.getElementById('rag-pairs').style.display = 'block';
            const pc = document.getElementById('pairs-count');
            if (pc) pc.textContent = `（${data.retrieved_pairs.length} 条）`;
            renderRAGPairs(data.retrieved_pairs);
        }
        
        // 显示SQL（带别名），如果有原始SQL则显示切换按钮
        document.getElementById('qa-sql-output').style.display = 'block';
        document.getElementById('qa-generated-sql').textContent = data.sql;
        const qaToggleBtn = document.getElementById('btn-toggle-qa-sql');
        if (data.raw_sql && data.raw_sql !== data.sql) {
            document.getElementById('qa-generated-sql-raw').textContent = data.raw_sql;
            qaToggleBtn.style.display = 'inline-block';
            qaToggleBtn.textContent = '查看原始SQL';
            document.getElementById('qa-generated-sql').style.display = 'block';
            document.getElementById('qa-generated-sql-raw').style.display = 'none';
        } else {
            // 没有原始SQL可切换，确保显示主SQL区域
            qaToggleBtn.style.display = 'none';
            document.getElementById('qa-generated-sql-raw').textContent = '';
            document.getElementById('qa-generated-sql').style.display = 'block';
            document.getElementById('qa-generated-sql-raw').style.display = 'none';
        }
        
        // 审查状态
        const reviewEl = document.getElementById('review-status');
        if (data.review_status) {
            if (data.review_status.passed) {
                reviewEl.className = 'review-status passed';
                reviewEl.textContent = '审查通过' + (data.attempts > 1 ? ` (经过 ${data.attempts} 次生成)` : '');
            } else {
                reviewEl.className = 'review-status failed';
                reviewEl.textContent = `审查警告: ${data.review_status.message || '存在潜在问题'}`;
            }
        }
        
        // 生成路径徽章 + 上下文面板（阶段耗时已在悬浮窗实时展示，SQL 上不再显示耗时徽章）
        const qaGenMode = document.getElementById('qa-gen-mode');
        if (qaGenMode && data.generation_mode) {
            qaGenMode.textContent = data.generation_mode;
            qaGenMode.style.display = 'inline-block';
        } else if (qaGenMode) {
            qaGenMode.style.display = 'none';
        }
        renderContextPanel(data, 'qa-cp');
        
        // 显示结果
        if (data.result_preview) {
            document.getElementById('qa-result-output').style.display = 'block';
            renderResultTable('qa-result-table', data.result_preview);
        }
        
        // 显示操作按钮
        document.getElementById('qa-actions').style.display = 'flex';
        
    } catch (e) {
        alert('请求失败: ' + e.message);
    }
}

function resetSteps() {
    ['step-rag', 'step-schema', 'step-llm', 'step-execute', 'step-review'].forEach(id => {
        const el = document.getElementById(id);
        el.querySelector('.step-icon').className = 'step-icon';
        el.querySelector('.step-status').className = 'step-status';
        el.querySelector('.step-status').textContent = '等待中';
    });
}

function updateStep(stepId, status) {
    const el = document.getElementById(stepId);
    const icon = el.querySelector('.step-icon');
    const statusEl = el.querySelector('.step-status');
    
    if (status === '完成') {
        icon.className = 'step-icon done';
        statusEl.className = 'step-status done';
    } else if (status === '进行中') {
        icon.className = 'step-icon';
        statusEl.className = 'step-status';
    } else if (status === '警告') {
        icon.className = 'step-icon warning';
        statusEl.className = 'step-status error';
    }
    statusEl.textContent = status;
}

function renderRAGPairs(pairs) {
    const container = document.getElementById('pairs-list');
    container.innerHTML = '';
    
    pairs.forEach(pair => {
        const div = document.createElement('div');
        div.className = 'pair-item';
        div.innerHTML = `
            <div class="pair-similarity">相似度: ${pair.similarity_score || pair.combined_score || '0.00'}</div>
            <div class="pair-question">${pair.question || ''}</div>
            <div class="pair-sql">${pair.standard_sql || ''}</div>
        `;
        container.appendChild(div);
    });
}

async function saveQA() {
    if (!currentSession.sql) return;
    
    try {
        const response = await fetch('/api/save-qa', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                question: currentSession.question,
                sql: currentSession.sql,
                result_preview: JSON.stringify(currentSession.resultPreview)
            })
        });
        
        const data = await response.json();
        if (data.success) {
            alert('已保存到问答对库');
        }
    } catch (e) {
        alert('保存失败: ' + e.message);
    }
}

function feedbackError() {
    openModal();
}

// ==================== 结果表格渲染 ====================

function _escapeHtml(text) {
    if (text === null || text === undefined) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function truncateText(text, maxLen) {
    if (!text) return '';
    return text.length > maxLen ? text.substring(0, maxLen) + '...' : text;
}

function renderResultTable(containerId, data) {
    const container = document.getElementById(containerId);
    
    if (!data) {
        container.innerHTML = '<p class="placeholder">无数据</p>';
        return;
    }
    
    // 如果执行失败，显示错误信息
    if (!data.success) {
        container.innerHTML = `<p class="placeholder error">执行失败: ${_escapeHtml(data.error || '未知错误')}</p>`;
        return;
    }
    
    if (!data.headers || data.headers.length === 0) {
        container.innerHTML = '<p class="placeholder">无数据</p>';
        return;
    }
    
    let html = '<table><thead><tr>';
    data.headers.forEach(h => {
        html += `<th>${_escapeHtml(h)}</th>`;
    });
    html += '</tr></thead><tbody>';
    
    (data.rows || []).forEach(row => {
        html += '<tr>';
        row.forEach(cell => {
            html += `<td>${cell !== null ? _escapeHtml(cell) : 'NULL'}</td>`;
        });
        html += '</tr>';
    });
    
    html += '</tbody></table>';
    container.innerHTML = html;
}

function formatTiming(timing) {
    if (!timing) return '';
    const parts = [];
    if (timing.total !== undefined) {
        parts.push(`总耗时 ${timing.total}ms`);
    }
    if (timing.rag !== undefined) {
        parts.push(`RAG ${timing.rag}ms`);
    }
    if (timing.gen !== undefined) {
        parts.push(`生成 ${timing.gen}ms`);
    }
    if (timing.exec !== undefined) {
        parts.push(`执行 ${timing.exec}ms`);
    }
    if (timing.audit !== undefined) {
        parts.push(`审计 ${timing.audit}ms`);
    }
    return parts.join(' · ');
}

// ==================== SQL 显示切换 ====================

function toggleTrainingSQL() {
    const displayEl = document.getElementById('generated-sql');
    const rawEl = document.getElementById('generated-sql-raw');
    const btn = document.getElementById('btn-toggle-training-sql');
    
    if (rawEl.style.display === 'none') {
        rawEl.style.display = 'block';
        displayEl.style.display = 'none';
        btn.textContent = '查看别名SQL';
    } else {
        rawEl.style.display = 'none';
        displayEl.style.display = 'block';
        btn.textContent = '查看原始SQL';
    }
}

function toggleQaSQL() {
    const displayEl = document.getElementById('qa-generated-sql');
    const rawEl = document.getElementById('qa-generated-sql-raw');
    const btn = document.getElementById('btn-toggle-qa-sql');
    
    if (rawEl.style.display === 'none') {
        rawEl.style.display = 'block';
        displayEl.style.display = 'none';
        btn.textContent = '查看别名SQL';
    } else {
        rawEl.style.display = 'none';
        displayEl.style.display = 'block';
        btn.textContent = '查看原始SQL';
    }
}

// ==================== 错题集 ====================

let currentErrorPage = 1;
let currentErrorSearchQuery = '';

async function loadErrorList() {
    // 如果有搜索查询，执行搜索；否则加载普通列表
    if (currentErrorSearchQuery) {
        searchErrors();
        return;
    }
    
    const filter = document.getElementById('error-filter').value;
    const resolvedFilter = document.getElementById('error-resolved-filter').value;
    
    try {
        const url = new URL('/api/error-list', window.location.origin);
        url.searchParams.set('page', currentErrorPage);
        url.searchParams.set('per_page', 20);
        if (filter) url.searchParams.set('error_type', filter);
        if (resolvedFilter) url.searchParams.set('resolved', resolvedFilter);
        
        const response = await fetch(url);
        const data = await response.json();
        
        if (!data.success) return;
        
        renderErrorItems(data.items);
        renderPagination('errors-pagination', data.total, 20, currentErrorPage, (newPage) => { currentErrorPage = newPage; loadErrorList(); });
        
    } catch (e) {
        console.error('加载错题集失败:', e);
    }
}

// 渲染错题表格
function renderErrorItems(items) {
    const container = document.getElementById('errors-list');
    container.innerHTML = '';
    // 缓存完整字段供详情面板使用
    window._errorItemsById = {};
    (items || []).forEach(it => { window._errorItemsById[it.id] = it; });
    
    if (!items || items.length === 0) {
        container.innerHTML = '<p class="placeholder">暂无错题</p>';
        return;
    }
    
    let html = '<table class="data-table"><thead><tr>';
    html += '<th>业务问题</th>';
    html += '<th style="width:100px">错误类型</th>';
    html += '<th style="width:80px">状态</th>';
    html += '<th style="width:80px">重复次数</th>';
    html += '<th style="width:140px">创建时间</th>';
    html += '<th style="width:200px">操作</th>';
    html += '</tr></thead><tbody>';
    
    items.forEach(item => {
        const statusClass = item.is_resolved ? 'tag-resolved' : 'tag-unresolved';
        const statusText = item.is_resolved ? '已修复' : '待修复';
        const freqBadge = item.frequency > 1 ? `<span class="freq-badge">${item.frequency}</span>` : '1';
        
        html += '<tr>';
        html += `<td class="td-question">${_escapeHtml(truncateText(item.business_question, 40))}</td>`;
        html += `<td><span class="tag-error-type">${_escapeHtml(item.error_type || '未知')}</span></td>`;
        html += `<td style="text-align:center"><span class="${statusClass}">${statusText}</span></td>`;
        html += `<td style="text-align:center">${freqBadge}</td>`;
        html += `<td>${item.created_at || ''}</td>`;
        html += `<td class="td-actions">`;
        html += `<button class="btn btn-sm btn-detail" onclick="toggleErrorDetail(${item.id})">详情</button>`;
        html += `<button class="btn btn-sm btn-edit" onclick="openEditModal(${item.id})">编辑</button>`;
        html += `<button class="btn btn-sm btn-delete" onclick="deleteError(${item.id})">删除</button>`;
        if (!item.is_resolved) {
            html += `<button class="btn btn-sm btn-resolve" onclick="resolveError(${item.id})">标记已修复</button>`;
        }
        html += `</td>`;
        html += '</tr>';
        html += `<tr class="detail-row"><td colspan="6" class="detail-cell"><div class="error-detail" id="error-detail-${item.id}" style="display:none"></div></td></tr>`;
    });
    
    html += '</tbody></table>';
    container.innerHTML = html;
}

// 语义搜索错题
async function searchErrors() {
    const query = document.getElementById('error-search-input').value.trim();
    if (!query) {
        currentErrorSearchQuery = '';
        currentErrorPage = 1;
        loadErrorList();
        return;
    }
    
    currentErrorSearchQuery = query;
    const infoEl = document.getElementById('search-result-info');
    infoEl.style.display = 'block';
    infoEl.textContent = '搜索中...';
    
    try {
        const response = await fetch('/api/error-search', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                query: query,
                limit: 20
            })
        });
        
        const data = await response.json();
        
        if (!data.success) {
            infoEl.textContent = '搜索失败: ' + (data.error || '未知错误');
            return;
        }
        
        const items = data.items || [];
        renderErrorItems(items);
        
        // 隐藏分页（搜索模式不分页）
        document.getElementById('errors-pagination').innerHTML = '';
        
        infoEl.textContent = `搜索 "${query}" 找到 ${items.length} 条相关错题（按语义相似度排序）`;
        
    } catch (e) {
        console.error('搜索失败:', e);
        infoEl.textContent = '搜索失败: ' + e.message;
    }
}

// 清空搜索
function clearErrorSearch() {
    document.getElementById('error-search-input').value = '';
    document.getElementById('search-result-info').style.display = 'none';
    currentErrorSearchQuery = '';
    currentErrorPage = 1;
    loadErrorList();
}

// 展开/收起错题详情
async function toggleErrorDetail(id) {
    const detailEl = document.getElementById('error-detail-' + id);
    if (!detailEl) return;

    if (detailEl.style.display === 'block') {
        detailEl.style.display = 'none';
        return;
    }

    let item = (window._errorItemsById || {})[id];
    if (!item) {
        try {
            const response = await fetch(`/api/error-detail?id=${id}`);
            const data = await response.json();
            if (data.success) item = data.item;
        } catch (e) { /* 忽略 */ }
    }
    if (!item) return;

    const statusBadge = item.is_resolved
        ? '<span class="tag-resolved">已修复</span>'
        : '<span class="tag-unresolved">待修复</span>';
    const freqBadge = item.frequency > 1 ? `<span class="freq-badge">×${item.frequency}</span>` : '';

    detailEl.innerHTML = `
        <div class="detail-flex">
            <div class="detail-main">
                <div class="error-detail-section">
                    <div class="error-detail-label">业务问题</div>
                    <div class="error-detail-text">${_escapeHtml(item.business_question || '')}</div>
                </div>
                <div class="error-detail-section">
                    <div class="error-detail-label" style="color:var(--danger)">错误 SQL</div>
                    <div class="error-detail-value">${_escapeHtml(item.generated_sql || '')}</div>
                </div>
                ${item.correct_sql ? `
                <div class="error-detail-section">
                    <div class="error-detail-label" style="color:var(--success)">修正 SQL</div>
                    <div class="error-correct">${_escapeHtml(item.correct_sql)}</div>
                </div>` : ''}
                ${item.error_detail ? `
                <div class="error-detail-section">
                    <div class="error-detail-label">详细说明</div>
                    <div class="error-detail-text">${_escapeHtml(item.error_detail)}</div>
                </div>` : ''}
            </div>
            <div class="detail-side">
                <div class="chip-list" style="margin-bottom:10px">
                    <span class="tag-error-type">${_escapeHtml(item.error_type || '未知')}</span>
                    ${statusBadge}${freqBadge}
                </div>
                <div class="qa-lib-date" style="margin-bottom:10px">${item.created_at || ''}</div>
                <div class="qa-actions" style="display:flex;flex-direction:column;gap:6px">
                    <button class="btn btn-sm btn-edit" onclick="openEditModal(${item.id})">编辑</button>
                    <button class="btn btn-sm btn-delete" onclick="deleteError(${item.id})">删除</button>
                    ${!item.is_resolved ? `<button class="btn btn-sm btn-resolve" onclick="resolveError(${item.id})">标记已修复</button>` : ''}
                </div>
            </div>
        </div>
    `;
    detailEl.style.display = 'block';
}

// 打开编辑模态框
async function openEditModal(id) {
    const modal = document.getElementById('edit-error-modal');
    
    try {
        const response = await fetch(`/api/error-detail?id=${id}`);
        const data = await response.json();
        
        if (!data.success) {
            alert('加载错题详情失败');
            return;
        }
        
        const item = data.item;
        document.getElementById('edit-error-id').value = item.id;
        document.getElementById('edit-question').value = item.business_question || '';
        document.getElementById('edit-error-sql').value = item.generated_sql || '';
        document.getElementById('edit-correct-sql').value = item.correct_sql || '';
        document.getElementById('edit-error-type').value = item.error_type || '其他';
        // error_detail 中可能包含"错误分析："和"SQL技巧："前缀，尝试解析
        let detail = item.error_detail || '';
        let analysis = '', tips = '';
        if (detail.includes('错误分析：') || detail.includes('SQL技巧：')) {
            const analysisMatch = detail.match(/错误分析：([^]*?)(?=SQL技巧：|$)/);
            const tipsMatch = detail.match(/SQL技巧：([^]*?)$/);
            if (analysisMatch) analysis = analysisMatch[1].replace(/^\n+|\n+$/g, '');
            if (tipsMatch) tips = tipsMatch[1].replace(/^\n+|\n+$/g, '');
        } else {
            analysis = detail;
        }
        document.getElementById('edit-error-analysis').value = analysis;
        document.getElementById('edit-sql-tips').value = tips;
        
        modal.style.display = 'flex';
        
    } catch (e) {
        alert('加载失败: ' + e.message);
    }
}

// 关闭编辑模态框
function closeEditModal() {
    document.getElementById('edit-error-modal').style.display = 'none';
}

// 保存编辑
async function saveErrorEdit() {
    const id = document.getElementById('edit-error-id').value;
    const question = document.getElementById('edit-question').value.trim();
    const errorSQL = document.getElementById('edit-error-sql').value.trim();
    const correctSQL = document.getElementById('edit-correct-sql').value.trim();
    const errorType = document.getElementById('edit-error-type').value;
    const errorAnalysis = document.getElementById('edit-error-analysis').value.trim();
    const sqlTips = document.getElementById('edit-sql-tips').value.trim();
    
    if (!question || !errorSQL) {
        alert('业务问题和错误SQL不能为空');
        return;
    }
    
    try {
        const response = await fetch('/api/error-update', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                id: parseInt(id),
                business_question: question,
                generated_sql: errorSQL,
                correct_sql: correctSQL || null,
                error_type: errorType,
                error_analysis: errorAnalysis || null,
                sql_tips: sqlTips || null
            })
        });
        
        const data = await response.json();
        if (data.success) {
            closeEditModal();
            loadErrorList();
            alert('修改已保存');
        } else {
            alert('保存失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        alert('保存失败: ' + e.message);
    }
}

// 标记已修复
async function resolveError(id) {
    if (!confirm('确定将此错题标记为已修复吗？')) return;
    
    try {
        const response = await fetch('/api/error-resolve', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id: id})
        });
        
        const data = await response.json();
        if (data.success) {
            loadErrorList();
        } else {
            alert('操作失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        alert('操作失败: ' + e.message);
    }
}

// 删除错题
async function deleteError(id) {
    if (!confirm('确定删除此错题吗？删除后不可恢复。')) return;
    
    try {
        const response = await fetch('/api/error-delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id: id})
        });
        
        const data = await response.json();
        if (data.success) {
            loadErrorList();
        } else {
            alert('删除失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        alert('删除失败: ' + e.message);
    }
}

// 点击模态框背景关闭
document.getElementById('edit-error-modal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeEditModal();
});

// ==================== 问答对库 ====================

let currentQALibPage = 1;
let currentQALibSearchQuery = '';

async function loadQALibList() {
    if (currentQALibSearchQuery) {
        searchQALib();
        return;
    }
    
    const difficulty = document.getElementById('qa-lib-difficulty').value;
    
    try {
        const url = new URL('/api/qa-pairs', window.location.origin);
        url.searchParams.set('page', currentQALibPage);
        url.searchParams.set('per_page', 20);
        
        const response = await fetch(url);
        const data = await response.json();
        
        if (!data.success) return;
        
        let items = data.items || [];
        if (difficulty) {
            items = items.filter(item => item.difficulty === difficulty);
        }
        
        renderQALibItems(items);
        renderPagination('qa-lib-pagination', data.total, 20, currentQALibPage, (newPage) => { currentQALibPage = newPage; loadQALibList(); });
        
    } catch (e) {
        console.error('加载问答对库失败:', e);
    }
}

function renderQALibItems(items) {
    const container = document.getElementById('qa-lib-list');
    container.innerHTML = '';
    // 缓存完整字段供详情面板使用
    window._qaLibItemsById = {};
    (items || []).forEach(it => { window._qaLibItemsById[it.id] = it; });
    
    if (!items || items.length === 0) {
        container.innerHTML = '<p class="placeholder">暂无可用的问答对</p>';
        return;
    }
    
    let html = '<table class="data-table"><thead><tr>';
    html += '<th>业务问题</th>';
    html += '<th style="width:40%">标准 SQL</th>';
    html += '<th style="width:80px">难度</th>';
    html += '<th style="width:100px">来源</th>';
    html += '<th style="width:140px">录入时间</th>';
    html += '<th style="width:160px">操作</th>';
    html += '</tr></thead><tbody>';
    
    items.forEach(item => {
        const diffClass = item.difficulty === '挑战题' ? 'tag-diff-hard' : 
                         item.difficulty === '基础题' ? 'tag-diff-easy' : 'tag-diff-medium';
        
        html += '<tr>';
        html += `<td class="td-question">${_escapeHtml(truncateText(item.question, 35))}</td>`;
        html += `<td class="td-sql"><code>${_escapeHtml(truncateText(item.standard_sql, 60))}</code></td>`;
        html += `<td style="text-align:center"><span class="${diffClass}">${item.difficulty || '进阶题'}</span></td>`;
        html += `<td>${_escapeHtml(item.source || '')}</td>`;
        html += `<td>${item.ingest_time || ''}</td>`;
        html += `<td class="td-actions">`;
        html += `<button class="btn btn-sm btn-detail" onclick="toggleQALibDetail(${item.id})">详情</button>`;
        html += `<button class="btn btn-sm btn-edit" onclick="openQaEditModal(${item.id})">编辑</button>`;
        html += `<button class="btn btn-sm btn-delete" onclick="deleteQAPair(${item.id})">删除</button>`;
        html += `</td>`;
        html += '</tr>';
        html += `<tr class="detail-row"><td colspan="6" class="detail-cell"><div class="qa-lib-detail" id="qa-lib-detail-${item.id}" style="display:none"></div></td></tr>`;
    });
    
    html += '</tbody></table>';
    container.innerHTML = html;
}

async function searchQALib() {
    const query = document.getElementById('qa-lib-search-input').value.trim();
    if (!query) {
        currentQALibSearchQuery = '';
        currentQALibPage = 1;
        loadQALibList();
        return;
    }
    
    currentQALibSearchQuery = query;
    const infoEl = document.getElementById('qa-lib-search-info');
    infoEl.style.display = 'block';
    infoEl.textContent = '搜索中...';
    
    try {
        const response = await fetch('/api/qa-search', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({query: query, limit: 20})
        });
        
        const data = await response.json();
        
        if (!data.success) {
            infoEl.textContent = '搜索失败: ' + (data.error || '未知错误');
            return;
        }
        
        const items = data.items || [];
        renderQALibItems(items);
        document.getElementById('qa-lib-pagination').innerHTML = '';
        infoEl.textContent = `搜索 "${query}" 找到 ${items.length} 条相关问答对（按语义相似度排序）`;
        
    } catch (e) {
        console.error('搜索失败:', e);
        infoEl.textContent = '搜索失败: ' + e.message;
    }
}

function clearQALibSearch() {
    document.getElementById('qa-lib-search-input').value = '';
    document.getElementById('qa-lib-search-info').style.display = 'none';
    currentQALibSearchQuery = '';
    currentQALibPage = 1;
    loadQALibList();
}

async function toggleQALibDetail(id) {
    const detailEl = document.getElementById('qa-lib-detail-' + id);
    if (!detailEl) return;

    if (detailEl.style.display === 'block') {
        detailEl.style.display = 'none';
        return;
    }

    // 优先用列表已加载的完整字段（含 is_usable/rating/tables_involved）
    let item = (window._qaLibItemsById || {})[id];
    if (!item) {
        try {
            const response = await fetch(`/api/qa-detail?id=${id}`);
            const data = await response.json();
            if (data.success) item = data.item;
        } catch (e) { /* 忽略 */ }
    }
    if (!item) return;

    const diffClass = item.difficulty === '挑战题' ? 'tag-diff-hard' :
                     item.difficulty === '基础题' ? 'tag-diff-easy' : 'tag-diff-medium';
    const usableBadge = item.is_usable === 1
        ? '<span class="tag-resolved">可用</span>'
        : '<span class="tag-unresolved">待评价</span>';
    const ratingBadge = item.question_rating
        ? `<span class="${item.question_rating === '合理' ? 'tag-resolved' : 'tag-unresolved'}">${_escapeHtml(item.question_rating)}</span>`
        : '';
    const tablesChips = (item.tables_involved || [])
        .map(t => `<span class="chip">${t}</span>`).join('');

    detailEl.innerHTML = `
        <div class="detail-flex">
            <div class="detail-main">
                <div class="error-detail-section">
                    <div class="error-detail-label">业务问题</div>
                    <div class="error-detail-text">${_escapeHtml(item.question || '')}</div>
                </div>
                <div class="error-detail-section">
                    <div class="error-detail-label">标准 SQL</div>
                    <div class="error-detail-value">${_escapeHtml(item.standard_sql || '（未生成）')}</div>
                </div>
                ${tablesChips ? `
                <div class="error-detail-section">
                    <div class="error-detail-label">涉及数据表</div>
                    <div class="chip-list">${tablesChips}</div>
                </div>` : ''}
            </div>
            <div class="detail-side">
                <div class="chip-list" style="margin-bottom:10px">
                    <span class="${diffClass}">${item.difficulty || '进阶题'}</span>
                    ${usableBadge}${ratingBadge}
                </div>
                <div class="qa-lib-date" style="margin-bottom:10px">${_escapeHtml(item.source || '')} · ${item.ingest_time || ''}</div>
                <div class="qa-actions" style="display:flex;flex-direction:column;gap:6px">
                    <button class="btn btn-sm btn-edit" onclick="openQaEditModal(${item.id})">编辑</button>
                    <button class="btn btn-sm btn-delete" onclick="deleteQAPair(${item.id})">删除</button>
                </div>
            </div>
        </div>
    `;
    detailEl.style.display = 'block';
}

async function openQaEditModal(id) {
    const modal = document.getElementById('edit-qa-modal');
    
    try {
        const response = await fetch(`/api/qa-detail?id=${id}`);
        const data = await response.json();
        
        if (!data.success) {
            alert('加载问答对详情失败');
            return;
        }
        
        const item = data.item;
        document.getElementById('edit-qa-id').value = item.id;
        document.getElementById('edit-qa-question').value = item.question || '';
        document.getElementById('edit-qa-sql').value = item.standard_sql || '';
        document.getElementById('edit-qa-difficulty').value = item.difficulty || '进阶题';
        document.getElementById('edit-qa-source').value = item.source || '';
        
        modal.style.display = 'flex';
        
    } catch (e) {
        alert('加载失败: ' + e.message);
    }
}

function closeQaEditModal() {
    document.getElementById('edit-qa-modal').style.display = 'none';
}

async function saveQaEdit() {
    const id = document.getElementById('edit-qa-id').value;
    const question = document.getElementById('edit-qa-question').value.trim();
    const sql = document.getElementById('edit-qa-sql').value.trim();
    const difficulty = document.getElementById('edit-qa-difficulty').value;
    
    if (!question || !sql) {
        alert('业务问题和标准SQL不能为空');
        return;
    }
    
    try {
        const response = await fetch('/api/qa-update', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                id: parseInt(id),
                question: question,
                standard_sql: sql,
                difficulty: difficulty
            })
        });
        
        const data = await response.json();
        if (data.success) {
            closeQaEditModal();
            loadQALibList();
            alert('修改已保存');
        } else {
            alert('保存失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        alert('保存失败: ' + e.message);
    }
}

async function deleteQAPair(id) {
    if (!confirm('确定删除此问答对吗？删除后不可恢复。')) return;
    
    try {
        const response = await fetch('/api/qa-delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id: id})
        });
        
        const data = await response.json();
        if (data.success) {
            loadQALibList();
        } else {
            alert('删除失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        alert('删除失败: ' + e.message);
    }
}

// 点击模态框背景关闭
document.getElementById('edit-qa-modal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeQaEditModal();
});

// ==================== 统计看板 ====================

async function loadStats() {
    try {
        const response = await fetch('/api/stats');
        const data = await response.json();
        
        if (!data.success) return;
        
        const s = data.data;
        // 基础数据 KPI
        if (s.basic) {
            document.getElementById('stat-tables').textContent = s.basic.tables;
            document.getElementById('stat-columns').textContent = s.basic.columns;
            document.getElementById('stat-relationships').textContent = s.basic.relationships;
            document.getElementById('stat-code-domains').textContent = s.basic.code_domains;
            document.getElementById('stat-code-items').textContent = s.basic.code_items;
            document.getElementById('stat-qa-usable').textContent = s.basic.qa_usable;
            document.getElementById('stat-qa-pending').textContent = s.basic.qa_pending;
        }
        // 知识库 KPI（两处问答对总数）
        document.getElementById('stat-qa-total').textContent = s.qa_pairs.total;
        document.getElementById('stat-kb-qa-total').textContent = s.qa_pairs.total;
        document.getElementById('stat-error-total').textContent = s.error_records.total;
        document.getElementById('stat-error-resolved').textContent = s.error_records.resolved;
        
        const total = s.generation.correct + s.generation.error + s.generation.skipped;
        const rate = total > 0 ? Math.round(s.generation.correct / total * 100) : 0;
        document.getElementById('stat-correct-rate').textContent = rate + '%';
        
        // 图表
        renderChartBars('error-type-chart', s.error_records.type_distribution.map(t => ({ name: t.type, count: t.count })));
        renderChartBars('diff-dist-chart', s.qa_pairs_dist.difficulty);
        renderChartBars('source-dist-chart', s.qa_pairs_dist.source);
        renderChartBars('judgment-dist-chart', [
            { name: '正确', count: s.generation.correct },
            { name: '错误', count: s.generation.error },
            { name: '跳过', count: s.generation.skipped }
        ]);

        // 生成健康
        const h = s.generation_health || {};
        document.getElementById('health-latency').textContent = h.avg_latency_ms ? (h.avg_latency_ms / 1000).toFixed(1) + 's' : '—';
        document.getElementById('health-attempts').textContent = h.avg_attempts || '—';
        document.getElementById('health-exec-rate').textContent = (h.exec_success_rate != null) ? h.exec_success_rate + '%' : '—';
        document.getElementById('health-gen-total').textContent = s.generation.total;
        
    } catch (e) {
        console.error('加载统计失败:', e);
    }
}

// ==================== 分页 ====================

function renderPagination(containerId, total, perPage, currentPage, onPageChange) {
    const totalPages = Math.ceil(total / perPage);
    const container = document.getElementById(containerId);
    container.innerHTML = '';
    
    if (totalPages <= 1) return;
    
    const prev = document.createElement('button');
    prev.className = 'page-btn';
    prev.textContent = '上一页';
    prev.disabled = currentPage <= 1;
    prev.onclick = () => onPageChange(currentPage - 1);
    container.appendChild(prev);
    
    for (let i = 1; i <= totalPages; i++) {
        const btn = document.createElement('button');
        btn.className = 'page-btn' + (i === currentPage ? ' active' : '');
        btn.textContent = i;
        btn.onclick = () => onPageChange(i);
        container.appendChild(btn);
    }
    
    const next = document.createElement('button');
    next.className = 'page-btn';
    next.textContent = '下一页';
    next.disabled = currentPage >= totalPages;
    next.onclick = () => onPageChange(currentPage + 1);
    container.appendChild(next);
}

// ==================== 模态框 ====================

function openModal() {
    document.getElementById('error-modal').style.display = 'flex';
}

function closeModal() {
    document.getElementById('error-modal').style.display = 'none';
}

async function submitErrorRecord() {
    if (!currentSession.sessionId) {
        alert('会话已失效，请重新生成题目');
        return;
    }

    const correctSQL = document.getElementById('correct-sql').value.trim();
    const feedback = document.getElementById('error-feedback').value.trim();

    // 收集错误维度
    const dimMap = [
        {key: 'table_choice_error', label: '表选择错误', id: 'err-dim-table'},
        {key: 'field_choice_error', label: '字段选择错误', id: 'err-dim-field'},
        {key: 'join_path_error', label: '关联条件错误', id: 'err-dim-join'},
        {key: 'where_condition_error', label: 'WHERE条件错误', id: 'err-dim-where'},
        {key: 'aggregation_error', label: '聚合方式错误', id: 'err-dim-agg'},
    ];
    const checkedDims = dimMap.filter(d => document.getElementById(d.id).checked);

    // 根据勾选的维度生成错误类型；未勾选时默认“其他”
    let errorType = '其他';
    if (checkedDims.length === 1) {
        errorType = checkedDims[0].label;
    } else if (checkedDims.length > 1) {
        errorType = '多维度错误';
    }

    const errorDimensions = {};
    dimMap.forEach(d => {
        errorDimensions[d.key] = document.getElementById(d.id).checked;
    });

    const submitBtn = document.querySelector('#error-modal .btn-primary');
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = '提交中...';
    }

    try {
        const response = await fetch('/api/error-record', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                session_id: currentSession.sessionId,
                error_type: errorType,
                correct_sql: correctSQL,
                error_detail: feedback,
                ...errorDimensions
            })
        });

        if (!response.ok) {
            const text = await response.text();
            throw new Error('服务器返回 ' + response.status + ': ' + text.slice(0, 200));
        }

        const data = await response.json();
        if (data.success) {
            // 清空表单
            document.getElementById('correct-sql').value = '';
            document.getElementById('error-feedback').value = '';
            dimMap.forEach(d => { document.getElementById(d.id).checked = false; });

            closeModal();
            loadErrorList();

            if (currentSession.mode === 'training') {
                generateQuestion();
            }
        } else {
            alert('提交失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        console.error('submitErrorRecord error:', e);
        alert('提交失败: ' + e.message);
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = '提交记录';
        }
    }
}

// 点击模态框背景关闭
document.getElementById('error-modal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeModal();
});

// ==================== 布局层：流程导轨与上下文面板 ====================

/** 训练流程导轨状态：activeStep 之前标 done，当前标 active */
function updateFlowStep(activeStep) {
    for (let i = 1; i <= 4; i++) {
        const el = document.getElementById('flow-step-' + i);
        if (!el) continue;
        el.classList.remove('active', 'done');
        if (i < activeStep) el.classList.add('done');
        else if (i === activeStep) el.classList.add('active');
    }
}

/** 上下文面板渲染（生成路径/定位表/码值命中/耗时分解），prefix 区分训练(cp)与问答(qa-cp) */
function renderContextPanel(data, prefix = 'cp') {
    const panel = document.getElementById(prefix === 'cp' ? 'context-panel' : prefix + '-panel');
    if (!panel) return;
    panel.style.display = 'block';
    document.getElementById(prefix + '-empty').style.display = 'none';
    document.getElementById(prefix + '-mode-block').style.display = 'block';

    // 生成模式徽章：模式 · 工作流预设 · LLM 调用数/token（可观测性透出）
    const modeParts = [data.generation_mode || '—'];
    if (data.workflow) modeParts.push('wf:' + data.workflow);
    const usage = data.usage || {};
    if (usage.llm_calls) modeParts.push(`LLM×${usage.llm_calls} · ${usage.total_tokens}tok`);
    document.getElementById(prefix + '-gen-mode').textContent = modeParts.join(' · ');

    const tables = data.located_tables || [];
    document.getElementById(prefix + '-tables-block').style.display = tables.length ? 'block' : 'none';
    document.getElementById(prefix + '-tables').innerHTML =
        tables.map(t => `<span class="chip">${t}</span>`).join('');

    const hits = data.code_value_hits || [];
    document.getElementById(prefix + '-cv-block').style.display = hits.length ? 'block' : 'none';
    document.getElementById(prefix + '-code-values').innerHTML = hits.map(h => {
        const hit = h.matched && h.matched.length;
        const title = hit ? `命中: ${h.matched.join('、')}` : (h.code_name || '');
        return `<span class="chip ${hit ? 'chip-hit' : ''}" title="${title}">${h.cn_name || h.code_name}</span>`;
    }).join('');

    const t = data.gen_timers || {};
    const rows = [
        ['rag', t.rag_retrieve], ['意图', t.intent_parse], ['草稿', t.draft_build],
        ['定位', t.table_locate], ['提示词', t.prompt_build], ['LLM', t.llm_call], ['校验', t.validation]
    ].filter(r => typeof r[1] === 'number');
    const total = rows.reduce((s, r) => s + r[1], 0) || 1;
    document.getElementById(prefix + '-timing-block').style.display = rows.length ? 'block' : 'none';
    document.getElementById(prefix + '-timing').innerHTML = rows.map(([n, v]) =>
        `<div class="timing-row"><span class="timing-name">${n}</span>` +
        `<div class="timing-track"><div class="timing-fill" style="width:${Math.max(2, v / total * 100)}%"></div></div>` +
        `<span class="timing-val">${Math.round(v)}ms</span></div>`).join('');
}

/** QA 模式：SQL 卡下方的定位表 chips */
function renderQAContextChips(tables) {
    const el = document.getElementById('qa-context-chips');
    if (!el) return;
    el.innerHTML = (tables || []).map(t => `<span class="chip">${t}</span>`).join('');
}

// QA 输入框 Ctrl+Enter 快捷生成
(function () {
    const input = document.getElementById('qa-question-input');
    if (input) {
        input.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault();
                generateSQLFromQuestion();
            }
        });
    }
})();

/** 上下文面板重置为占位态（新题目/新提问时调用，避免展示过期的上下文） */
function resetContextPanel(prefix = 'cp') {
    const empty = document.getElementById(prefix + '-empty');
    if (empty) empty.style.display = 'block';
    ['-mode-block', '-tables-block', '-cv-block', '-timing-block'].forEach(suffix => {
        const el = document.getElementById(prefix + suffix);
        if (el) el.style.display = 'none';
    });
}

// ==================== 布局层：左栏统计与迷你条 ====================

/** 通用迷你统计条渲染（可选点击筛选） */
function renderMiniBars(elId, rows, options = {}) {
    const el = document.getElementById(elId);
    if (!el) return;
    if (!rows || !rows.length) {
        el.innerHTML = '<div class="cp-empty">暂无数据</div>';
        return;
    }
    const max = Math.max(...rows.map(r => r.count), 1);
    el.innerHTML = rows.map(r => `
        <div class="mini-bar-row" ${options.onclick ? `data-name="${String(r.name).replace(/"/g, '&quot;')}" style="cursor:pointer"` : ''}>
            <span class="mini-bar-name" title="${r.name}">${r.name}</span>
            <div class="mini-bar-track"><div class="mini-bar-fill" style="width:${Math.max(3, r.count / max * 100)}%"></div></div>
            <span class="mini-bar-val">${r.count}</span>
        </div>`).join('');
    if (options.onclick) {
        el.querySelectorAll('.mini-bar-row').forEach(row => {
            row.addEventListener('click', () => options.onclick(row.dataset.name));
        });
    }
}

/** 问答对库左栏：难度分布 */
async function loadQALibRailStats() {
    if (window._qaLibRailStatsLoaded) return;
    window._qaLibRailStatsLoaded = true;
    try {
        const r = await fetch('/api/stats');
        const d = await r.json();
        if (d.success) {
            renderMiniBars('qa-lib-diff-stats', d.data.qa_pairs_dist.difficulty, {
                onclick: (name) => {
                    document.getElementById('qa-lib-difficulty').value = name;
                    currentQALibPage = 1;
                    loadQALibList();
                }
            });
        }
    } catch (e) { /* 统计失败不影响列表 */ }
}

/** 错题集左栏：错误类型分布（点击筛选） */
async function loadErrorRailStats() {
    if (window._errorRailStatsLoaded) return;
    window._errorRailStatsLoaded = true;
    try {
        const r = await fetch('/api/stats');
        const d = await r.json();
        if (d.success) {
            renderMiniBars('error-type-stats', d.data.error_records.type_distribution, {
                onclick: (name) => {
                    document.getElementById('error-filter').value = name;
                    currentErrorPage = 1;
                    loadErrorList();
                }
            });
        }
    } catch (e) { /* 统计失败不影响列表 */ }
}

/** 看板条形图渲染（name/count 行） */
function renderChartBars(elId, rows) {
    const el = document.getElementById(elId);
    if (!el) return;
    if (!rows || !rows.length) {
        el.innerHTML = '<p class="placeholder">暂无数据</p>';
        return;
    }
    const max = Math.max(...rows.map(r => r.count), 1);
    el.innerHTML = rows.map(r => `
        <div class="chart-bar">
            <div class="chart-label" title="${r.name}">${r.name}</div>
            <div class="chart-track"><div class="chart-fill" style="width:${Math.max(2, r.count / max * 100)}%"></div></div>
            <div class="chart-value">${r.count}</div>
        </div>`).join('');
}

// ==================== 统计看板：子页签与运行日志 ====================

let currentStatsTab = 'basic';
let currentGenLogsPage = 1;
let genLogsLoaded = false;

function switchStatsTab(tab) {
    currentStatsTab = tab;
    document.querySelectorAll('.sub-tab').forEach(el => {
        el.classList.toggle('active', el.dataset.stab === tab);
    });
    document.querySelectorAll('.stats-tab').forEach(el => {
        el.classList.toggle('active', el.id === 'stab-' + tab);
    });
    if (tab === 'workflow' && !genLogsLoaded) {
        loadGenLogs();
    }
}

function scrollToGenLogs() {
    const el = document.getElementById('gen-logs-section');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function loadGenLogs() {
    try {
        const url = new URL('/api/generation-logs', window.location.origin);
        url.searchParams.set('page', currentGenLogsPage);
        url.searchParams.set('per_page', 20);
        const response = await fetch(url);
        const data = await response.json();
        if (!data.success) return;
        genLogsLoaded = true;
        renderGenLogs(data.items);
        renderPagination('gen-logs-pagination', data.total, 20, currentGenLogsPage,
            (newPage) => { currentGenLogsPage = newPage; loadGenLogs(); });
    } catch (e) {
        console.error('加载运行日志失败:', e);
    }
}

function renderGenLogs(items) {
    const container = document.getElementById('gen-logs-list');
    container.innerHTML = '';
    if (!items || !items.length) {
        container.innerHTML = '<p class="placeholder">暂无运行日志</p>';
        return;
    }
    let html = '<table class="data-table"><thead><tr>';
    html += '<th>业务问题</th>';
    html += '<th style="width:90px">执行</th>';
    html += '<th style="width:90px">判定</th>';
    html += '<th style="width:70px">尝试</th>';
    html += '<th style="width:90px">耗时</th>';
    html += '<th style="width:150px">时间</th>';
    html += '</tr></thead><tbody>';

    items.forEach(item => {
        const execBadge = item.execution_status === 'success'
            ? '<span class="tag-resolved">成功</span>'
            : `<span class="tag-unresolved">${_escapeHtml(item.execution_status || '失败')}</span>`;
        const judgeBadge = item.user_judgment
            ? `<span class="${item.user_judgment === '正确' ? 'tag-resolved' : (item.user_judgment === '错误' ? 'tag-unresolved' : 'tag-error-type')}">${item.user_judgment}</span>`
            : '<span style="color:var(--faint)">—</span>';
        const latency = item.latency_ms ? (item.latency_ms / 1000).toFixed(1) + 's' : '—';

        html += `<tr class="log-row" onclick="toggleGenLogDetail(${item.id})" style="cursor:pointer">`;
        html += `<td class="td-question">${_escapeHtml(truncateText(item.question, 45))}</td>`;
        html += `<td>${execBadge}</td>`;
        html += `<td>${judgeBadge}</td>`;
        html += `<td style="text-align:center">${item.attempts || 1}</td>`;
        html += `<td>${latency}</td>`;
        html += `<td>${item.created_at || ''}</td>`;
        html += '</tr>';
        html += `<tr class="detail-row"><td colspan="6" class="detail-cell"><div id="gen-log-detail-${item.id}" style="display:none"></div></td></tr>`;
    });
    html += '</tbody></table>';
    container.innerHTML = html;
    window._genLogItemsById = {};
    items.forEach(it => { window._genLogItemsById[it.id] = it; });
}

function toggleGenLogDetail(id) {
    const detailEl = document.getElementById('gen-log-detail-' + id);
    const item = (window._genLogItemsById || {})[id];
    if (!detailEl || !item) return;
    if (detailEl.style.display === 'block') {
        detailEl.style.display = 'none';
        return;
    }
    detailEl.innerHTML = `
        <div class="error-detail-section">
            <div class="error-detail-label">业务问题</div>
            <div class="error-detail-text">${_escapeHtml(item.question || '')}</div>
        </div>
        <div class="error-detail-section">
            <div class="error-detail-label">生成 SQL</div>
            <div class="error-detail-value">${_escapeHtml(item.generated_sql || '')}</div>
        </div>
        <div class="error-detail-section">
            <div class="error-detail-label">元信息</div>
            <div class="error-detail-text">
                执行: ${_escapeHtml(item.execution_status || '')} | 
                审查: ${item.review_passed ? '通过' : '未通过'} | 
                判定: ${item.user_judgment || '未判定'} | 
                尝试: ${item.attempts || 1} 次 | 
                耗时: ${item.latency_ms ? (item.latency_ms / 1000).toFixed(1) + 's' : '—'} | 
                结果行数: ${item.row_count != null ? item.row_count : '—'}
            </div>
        </div>
    `;
    detailEl.style.display = 'block';
}

// ==================== SSE 流式生成：实时阶段进度 ====================

function formatMs(ms) {
    if (ms == null) return '';
    return ms >= 1000 ? (ms / 1000).toFixed(1) + 's' : Math.round(ms) + 'ms';
}

/** SSE 消费：POST /api/generate-sql-stream，阶段事件走 onStage，思考事件走 onThinking，返回最终结果 */
async function streamGenerateSQL(payload, onStage, onThinking) {
    const response = await fetch('/api/generate-sql-stream', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    });
    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        return {success: false, error: err.error || ('HTTP ' + response.status)};
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let finalResult = null;
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
            if (evt.thinking && onThinking) onThinking(evt.thinking);
            else if (evt.stage && onStage) onStage(evt);
            else if (evt.done) finalResult = evt.result;
            else if (evt.error) finalResult = {success: false, error: evt.error};
        }
    }
    return finalResult || {success: false, error: '流式响应中断'};
}

// ==================== 思考过程窗口 ====================

/** 思考窗口：重置为隐藏占位态（新生成开始时调用，避免展示过期过程） */
function resetThinkingWindow(mode) {
    const section = document.getElementById(mode + '-thinking-section');
    const win = document.getElementById(mode + '-thinking-window');
    if (win) win.innerHTML = '';
    if (section) section.style.display = 'none';
}

/** 思考窗口：追加一条思考事件（按 kind 渲染），并自动滚动到底部 */
function appendThinking(mode, evt) {
    const section = document.getElementById(mode + '-thinking-section');
    const win = document.getElementById(mode + '-thinking-window');
    if (!section || !win || !evt) return;
    // LLM 思考流式输出：追加到当前流式行（同一 phase 连续追加，phase 切换开新行）
    if (evt.kind === 'llm_stream') {
        section.style.display = 'block';
        const phaseLabel = evt.phase === 'reasoning' ? 'LLM 思考' : 'LLM 输出';
        let cur = win.querySelector('.think-stream.active');
        if (!cur || cur.dataset.phase !== evt.phase) {
            const act = win.querySelector('.think-stream.active');
            if (act) act.classList.remove('active');
            win.insertAdjacentHTML('beforeend',
                `<div class="think-line think-dim think-stream active" data-phase="${evt.phase}">` +
                `<span class="think-tag">[${phaseLabel}]</span><span class="stream-body"></span></div>`);
            cur = win.querySelector('.think-stream.active');
        }
        cur.querySelector('.stream-body').textContent += (evt.text || '');
        win.scrollTop = win.scrollHeight;
        return;
    }
    const act = win.querySelector('.think-stream.active');
    if (act) act.classList.remove('active');
    const html = _renderThinkingEvent(evt);
    if (!html) return;
    section.style.display = 'block';
    win.insertAdjacentHTML('beforeend', html);
    win.scrollTop = win.scrollHeight;
}

function _thinkLine(tag, body, cls) {
    return `<div class="think-line ${cls || ''}"><span class="think-tag">[${tag}]</span>${body}</div>`;
}

/** 思考事件渲染：intent/tables/columns/code_values/llm/repair/template/draft/review/exec/audit */
function _renderThinkingEvent(evt) {
    const esc = _escapeHtml;
    switch (evt.kind) {
        case 'intent': {
            const parts = [`类型=${esc(evt.question_type || '查询')}`];
            if ((evt.tables || []).length) parts.push(`规则表=${evt.tables.map(esc).join('、')}`);
            const fields = (evt.fields || []).map(f => f.agg ? `${f.agg}(${f.name || '*'})` : (f.name || '')).filter(Boolean);
            if (fields.length) parts.push(`字段=${fields.map(esc).join('、')}`);
            const filters = (evt.filters || []).map(f => `${f.field}${f.op}${f.value}`);
            if (filters.length) parts.push(`条件=${filters.map(esc).join('、')}`);
            return _thinkLine('意图', parts.join(' · '));
        }
        case 'tables': {
            const tables = evt.tables || [];
            const src = evt.sources || {};
            const srcNames = {rule: '规则', llm: 'LLM', draft: '草稿'};
            const srcParts = Object.keys(srcNames)
                .filter(k => (src[k] || []).length)
                .map(k => `${srcNames[k]}: ${src[k].map(esc).join('、')}`);
            return _thinkLine('定位', `合并定位 ${tables.length} 张表`) +
                `<div class="chip-list">${tables.map(t => `<span class="chip">${esc(t)}</span>`).join('')}</div>` +
                (srcParts.length ? `<div class="think-line think-dim">来源：${srcParts.join(' · ')}</div>` : '');
        }
        case 'columns': {
            const summary = evt.summary || [];
            if (!summary.length) return _thinkLine('字段', '未注入字段上下文', 'think-dim');
            return _thinkLine('字段', summary.map(s =>
                `${esc(s.table)} 注入 ${s.shown}/${s.total} 列`).join(' · '));
        }
        case 'code_values': {
            const items = evt.items || [];
            if (!items.length) return _thinkLine('码值', '未命中码值域', 'think-dim');
            return items.map(d => {
                const parts = [`${esc(d.cn_name || '')}(${esc(d.code_name || '')})`];
                if (d.form) parts.push(`形态=${esc(d.form)}`);
                if ((d.columns || []).length) parts.push(`落列=${d.columns.map(esc).join('、')}`);
                if ((d.matched || []).length) parts.push(`★命中=${d.matched.map(esc).join('、')}`);
                return _thinkLine('码值', parts.join(' · '));
            }).join('');
        }
        case 'llm':
            if (evt.phase === 'assemble')
                return _thinkLine('LLM', `组装 prompt ${evt.prompt_chars} 字符 · 模型 ${esc(evt.model || '-')} · 调用中…`);
            if (evt.phase === 'done')
                return _thinkLine('LLM', `返回完成 · 耗时 ${formatMs(evt.ms)} · SQL ${evt.sql_len} 字符`, 'think-ok');
            if (evt.phase === 'error')
                return _thinkLine('LLM', `调用失败: ${esc(evt.error || '')}`, 'think-err');
            return '';
        case 'audit_llm': {
            if (evt.phase === 'start')
                return _thinkLine('审计', `草稿试执行${evt.exec_ok ? `成功（${evt.row_count ?? 0} 行）` : '报错: ' + esc(evt.error || '')} · low 档合理性审计中…`);
            const vmap = {pass: '审计通过', fixed: '采纳审计修正', unavailable: '审计不可用（按草稿放行）',
                          fail_nosql: '审计不通过（未给修正）', fix_still_broken: '审计修正版仍不可执行',
                          fix_invalid: '审计修正版未过校验'};
            const ok = ['pass', 'fixed'].includes(evt.verdict);
            return _thinkLine('审计', `${vmap[evt.verdict] || esc(evt.verdict || '-')}` +
                `${evt.reason ? ' · ' + esc(evt.reason) : ''}${evt.ms != null ? ' · ' + formatMs(evt.ms) : ''}`,
                ok ? 'think-ok' : 'think-err');
        }
        case 'repair':
            return _thinkLine('修复', `第 ${evt.attempt} 轮定点修复 · 原因: ${esc(evt.reason || '')}`, 'think-err');
        case 'template':
            return _thinkLine('模板', `命中签名模板 ${esc(evt.name || '')}（${esc(evt.via || '')}）`, 'think-ok');
        case 'draft':
            return _thinkLine('草稿', evt.mode === 'direct'
                ? `草稿直出 · SQL ${evt.sql_len} 字符`
                : `LLM 未过校验，草稿兜底 · SQL ${evt.sql_len} 字符`, 'think-ok');
        case 'review':
            return _thinkLine('审查', `第 ${evt.attempt} 次生成未通过（${esc(evt.action || '')}）` +
                `${evt.message ? ': ' + esc(evt.message) : ''} · 重新生成`, 'think-err');
        case 'exec':
            return evt.status === 'success'
                ? _thinkLine('执行', `成功 · 返回 ${evt.row_count} 行 · ${formatMs(evt.ms)}`, 'think-ok')
                : _thinkLine('执行', `失败: ${esc(evt.error || '')}`, 'think-err');
        case 'audit':
            return _thinkLine('审计', `审查结论 ${esc(evt.status || '')}` +
                `${evt.post_audit ? ' · 已触发错题深度审计' : ''}`);
        default:
            return _thinkLine(esc(evt.kind || '?'), '', 'think-dim');
    }
}

/** 步骤状态更新（支持阶段耗时显示） */
function updateStep(stepId, status, timeMs) {
    const el = document.getElementById(stepId);
    if (!el) return;
    const icon = el.querySelector('.step-icon');
    const statusEl = el.querySelector('.step-status');

    if (status === '完成') {
        icon.className = 'step-icon done';
        statusEl.className = 'step-status done';
    } else if (status === '警告') {
        icon.className = 'step-icon warning';
        statusEl.className = 'step-status error';
    } else {
        icon.className = 'step-icon';
        statusEl.className = 'step-status';
    }
    statusEl.textContent = (timeMs != null) ? formatMs(timeMs) : status;
}

const STAGE_LABELS = {rag: 'RAG', schema: '上下文组装', llm: 'LLM', validate: '校验', exec: '执行', audit: '审计'};
const _liveTimings = {cp: {}, 'qa-cp': {}};

/** 悬浮窗耗时分解：阶段事件到达即实时更新该阶段耗时行 */
function upsertTimingRow(prefix, stage, ms) {
    _liveTimings[prefix][stage] = ms;
    const panel = document.getElementById(prefix === 'cp' ? 'context-panel' : prefix + '-panel');
    if (panel) panel.style.display = 'block';
    const empty = document.getElementById(prefix + '-empty');
    if (empty) empty.style.display = 'none';
    const block = document.getElementById(prefix + '-timing-block');
    if (block) block.style.display = 'block';
    const box = document.getElementById(prefix + '-timing');
    if (!box) return;
    const order = ['rag', 'schema', 'llm', 'validate', 'exec', 'audit'];
    const vals = order.filter(s => s in _liveTimings[prefix]).map(s => [STAGE_LABELS[s] || s, _liveTimings[prefix][s]]);
    const total = vals.reduce((a, r) => a + r[1], 0) || 1;
    box.innerHTML = vals.map(([n, v]) =>
        `<div class="timing-row"><span class="timing-name">${n}</span>` +
        `<div class="timing-track"><div class="timing-fill" style="width:${Math.max(2, v / total * 100)}%"></div></div>` +
        `<span class="timing-val">${formatMs(v)}</span></div>`).join('');
}

function resetLiveTimings(prefix) {
    _liveTimings[prefix] = {};
}

/** 生成失败时也展示错误 SQL（附错误横幅），便于定位问题 */
function showGenerationError(mode, error, sql) {
    const isQa = mode === 'qa';
    const section = document.getElementById(isQa ? 'qa-sql-output' : 'training-sql-section');
    const codeEl = document.getElementById(isQa ? 'qa-generated-sql' : 'generated-sql');
    if (!section || !codeEl) {
        alert('生成失败: ' + error);
        return;
    }
    section.style.display = 'block';
    let banner = document.getElementById(isQa ? 'qa-error-banner' : 'training-error-banner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = isQa ? 'qa-error-banner' : 'training-error-banner';
        banner.className = 'sql-error-banner';
        section.insertBefore(banner, codeEl);
    }
    banner.textContent = '生成失败: ' + error;
    banner.style.display = 'block';
    codeEl.textContent = sql || '-- 未生成 SQL';
    codeEl.classList.add('sql-error');
}

/** 生成成功后清除错误横幅与错误样式 */
function clearGenerationError(mode) {
    const isQa = mode === 'qa';
    const banner = document.getElementById(isQa ? 'qa-error-banner' : 'training-error-banner');
    if (banner) banner.style.display = 'none';
    const codeEl = document.getElementById(isQa ? 'qa-generated-sql' : 'generated-sql');
    if (codeEl) codeEl.classList.remove('sql-error');
}
