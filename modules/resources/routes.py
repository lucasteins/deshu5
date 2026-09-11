# -*- coding: utf-8 -*-
"""数据资源模块路由：数据资源 CRUD + 码值库 + Schema 浏览 + 统计看板 + 运行日志

- /api/resources/*：通用资源 REST（经 registry 分发到各 provider）
- /api/code-value-*：码值域与明细
- /api/schema, /api/schema-graph, /api/table-columns：Schema 浏览
- /api/stats, /api/generation-logs, /api/human-review-sample：统计看板
"""
import json

from flask import Blueprint, jsonify, request

from core.context import db_manager, get_schema_preloader, schema_loader
from modules.resources import registry
from modules.resources.base import ReadOnlyResourceError

bp = Blueprint('resources', __name__)


# ==================== Schema 浏览 ====================

@bp.route('/api/schema')
def get_schema():
    """获取数据库 Schema（表/列/主外键/行数）"""
    try:
        schema = schema_loader.load_schema()
        simplified = {}
        for table, info in schema.items():
            simplified[table] = {
                'columns': [{'name': c['name'], 'type': c['type'], 'pk': c['pk']} for c in info['columns']],
                'pk': info['pk'],
                'foreign_keys': info['foreign_keys'],
                'row_count': info['stats'].get('row_count', 0)
            }
        return jsonify({'success': True, 'schema': simplified})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/schema-graph')
def get_schema_graph():
    """表节点 + 主外键关系边（全部来自启动预加载，零额外查询）"""
    try:
        preloader = get_schema_preloader()
        relationships = preloader.get_relationships()
        rel_count = {}
        edges = []
        for rel in relationships:
            a, b = rel['path'][0], rel['path'][1]
            rel_count[a] = rel_count.get(a, 0) + 1
            rel_count[b] = rel_count.get(b, 0) + 1
            edges.append({
                'from': a,
                'to': b,
                'join_conditions': rel.get('join_conditions', [])
            })
        nodes = []
        for t in preloader.get_table_names():
            info = preloader.parser.tables.get(t, {})
            cols = info.get('columns', [])
            pk = info.get('pk') or []
            nodes.append({
                'name': t,
                'comment': info.get('comment') or '',
                'layer': 'dim' if t.startswith('dim_') else ('dwd' if t.startswith('dwd_') else 'other'),
                'column_count': len(cols),
                'pk': pk,
                'rel_count': rel_count.get(t, 0)
            })
        return jsonify({'success': True, 'nodes': nodes, 'edges': edges})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/table-columns')
def get_table_columns():
    """单表全量字段（名称/类型/PK/注释）+ 关联关系"""
    table = request.args.get('table', '').strip()
    if not table:
        return jsonify({'success': False, 'error': '缺少 table 参数'}), 400
    try:
        preloader = get_schema_preloader()
        cols = preloader.get_columns(table)
        if not cols:
            return jsonify({'success': False, 'error': f'表不存在或无字段: {table}'}), 404
        comment = preloader.get_table_comment(table)
        rels = []
        for rel in preloader.get_relationships():
            a, b = rel['path'][0], rel['path'][1]
            if table in (a, b):
                other = b if a == table else a
                rels.append({'table': other, 'comment': preloader.get_table_comment(other),
                             'join_conditions': rel.get('join_conditions', [])})
        return jsonify({
            'success': True,
            'table': table,
            'comment': comment,
            'columns': cols,
            'relationships': rels
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 码值库 ====================

@bp.route('/api/code-value-domains')
def get_code_value_domains():
    """码值域清单（含明细数与落列映射）"""
    try:
        with db_manager.connect_governance() as conn:
            cursor = conn.execute('''
                SELECT v.code_name, v.code_cn_name, COALESCE(v.domain_l2, v.domain_l1) AS domain, COUNT(i.id) AS item_count
                FROM code_values v
                LEFT JOIN code_value_items i ON i.code_name = v.code_name
                GROUP BY v.code_name, v.code_cn_name, COALESCE(v.domain_l2, v.domain_l1)
                ORDER BY item_count DESC, v.code_name
            ''')
            rows = cursor.fetchall()
            # 列↔域映射（来自校核形态表）
            col_map = {}
            try:
                for t, c, cn in conn.execute(
                        'SELECT table_name, column_name, code_name FROM code_value_column_form'):
                    col_map.setdefault(cn, []).append(f'{t}.{c}')
            except Exception:
                pass
        items = [{
            'code_name': r[0],
            'cn_name': r[1] or '',
            'domain': r[2] or '',
            'item_count': r[3],
            'columns': col_map.get(r[0], [])
        } for r in rows]
        return jsonify({'success': True, 'total': len(items), 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/code-value-items')
def get_code_value_items_api():
    """单域的码值明细"""
    code_name = request.args.get('code_name', '').strip()
    if not code_name:
        return jsonify({'success': False, 'error': '缺少 code_name 参数'}), 400
    try:
        with db_manager.connect_governance() as conn:
            cursor = conn.execute(
                'SELECT item_code, item_name, sort_order FROM code_value_items WHERE code_name = ? ORDER BY sort_order, id',
                (code_name,)
            )
            rows = cursor.fetchall()
        return jsonify({
            'success': True,
            'code_name': code_name,
            'items': [{'code': r[0], 'name': r[1], 'sort_order': r[2]} for r in rows]
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 通用资源 REST（registry 分发）====================
# 响应约定：{'success': bool, ...}；错误映射：
#   未知 rtype -> 404；条目不存在(LookupError) -> 404；
#   schema 校验失败(ValueError) -> 400+错误列表；只读资源写入(ReadOnlyResourceError) -> 405。


def _get_resource_provider(rtype):
    """按机器名取资源 provider；未注册返回 None。"""
    return registry.get(rtype)


@bp.route('/api/resources')
def list_resources():
    """资源注册表摘要：各资源类型 name/label/count/description"""
    try:
        return jsonify({'success': True, 'items': registry.summary()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/resources/<rtype>/items')
def list_resource_items(rtype):
    """资源条目分页列表：?limit=&offset=&q=（q 为名称/内容子串过滤）"""
    provider = _get_resource_provider(rtype)
    if provider is None:
        return jsonify({'success': False, 'error': f'未知资源类型: {rtype}'}), 404
    try:
        limit = request.args.get('limit', 200, type=int)
        offset = request.args.get('offset', 0, type=int)
        filters = {'q': request.args.get('q', '').strip()}
        items = provider.list(filters=filters, limit=limit, offset=offset)
        return jsonify({'success': True, 'total': provider.count(),
                        'limit': limit, 'offset': offset, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/resources/<rtype>/items/<item_id>')
def get_resource_item(rtype, item_id):
    """单条资源条目"""
    provider = _get_resource_provider(rtype)
    if provider is None:
        return jsonify({'success': False, 'error': f'未知资源类型: {rtype}'}), 404
    try:
        item = provider.get(item_id)
        if item is None:
            return jsonify({'success': False, 'error': f'{provider.label}不存在: {item_id}'}), 404
        return jsonify({'success': True, 'item': item})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/resources/<rtype>/items', methods=['POST'])
def create_resource_item(rtype):
    """新建资源条目（body 为条目 JSON；schema 校验失败返回 400+错误列表）"""
    provider = _get_resource_provider(rtype)
    if provider is None:
        return jsonify({'success': False, 'error': f'未知资源类型: {rtype}'}), 404
    data = request.get_json(silent=True) or {}
    try:
        errors = provider.validate_item(data)
        if errors:
            return jsonify({'success': False, 'errors': errors}), 400
        item = provider.create(data)
        return jsonify({'success': True, 'item': item})
    except ReadOnlyResourceError as e:
        return jsonify({'success': False, 'error': str(e)}), 405
    except ValueError as e:
        return jsonify({'success': False, 'errors': str(e).split('; ')}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/resources/<rtype>/items/<item_id>', methods=['PUT'])
def update_resource_item(rtype, item_id):
    """部分更新资源条目"""
    provider = _get_resource_provider(rtype)
    if provider is None:
        return jsonify({'success': False, 'error': f'未知资源类型: {rtype}'}), 404
    data = request.get_json(silent=True) or {}
    try:
        errors = provider.validate_item(data, partial=True)
        if errors:
            return jsonify({'success': False, 'errors': errors}), 400
        item = provider.update(item_id, data)
        return jsonify({'success': True, 'item': item})
    except ReadOnlyResourceError as e:
        return jsonify({'success': False, 'error': str(e)}), 405
    except LookupError as e:
        return jsonify({'success': False, 'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'success': False, 'errors': str(e).split('; ')}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/resources/<rtype>/items/<item_id>', methods=['DELETE'])
def delete_resource_item(rtype, item_id):
    """删除资源条目"""
    provider = _get_resource_provider(rtype)
    if provider is None:
        return jsonify({'success': False, 'error': f'未知资源类型: {rtype}'}), 404
    try:
        if not provider.delete(item_id):
            return jsonify({'success': False, 'error': f'{provider.label}不存在: {item_id}'}), 404
        return jsonify({'success': True, 'message': f'{provider.label}已删除'})
    except ReadOnlyResourceError as e:
        return jsonify({'success': False, 'error': str(e)}), 405
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/resources/<rtype>/import', methods=['POST'])
def import_resource_items(rtype):
    """批量导入（双通道）：
    - JSON body {rows: [...]}：逐行按 entry_schema 校验，返回校验报告
    - multipart 文件上传（字段名 file）：支持 .json / .csv / .xlsx
    """
    provider = _get_resource_provider(rtype)
    if provider is None:
        return jsonify({'success': False, 'error': f'未知资源类型: {rtype}'}), 404
    try:
        if 'file' in request.files:
            rows = _parse_import_file(request.files['file'])
            if isinstance(rows, str):  # 解析失败，返回错误消息
                return jsonify({'success': False, 'error': rows}), 400
        else:
            data = request.get_json(silent=True) or {}
            rows = data.get('rows')
            if not isinstance(rows, list):
                return jsonify({'success': False, 'error': 'body 需为 {rows: [...]}'}), 400
        report = provider.import_rows(rows)
        return jsonify({'success': True, **report})
    except ReadOnlyResourceError as e:
        return jsonify({'success': False, 'error': str(e)}), 405
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _parse_import_file(file_storage):
    """解析导入文件为 rows 列表；失败返回错误消息字符串。"""
    filename = (file_storage.filename or '').lower()
    try:
        if filename.endswith('.json'):
            data = json.load(file_storage.stream)
            rows = data.get('rows') if isinstance(data, dict) else data
            if isinstance(data, dict) and 'example_rows' in data and rows is None:
                rows = data['example_rows']  # 兼容直接上传模板文件
            if not isinstance(rows, list):
                return 'JSON 文件需为条目数组或含 rows 字段的对象'
            return rows
        if filename.endswith('.csv'):
            import csv, io
            text = io.TextIOWrapper(file_storage.stream, encoding='utf-8-sig')
            return list(csv.DictReader(text))
        if filename.endswith(('.xlsx', '.xls')):
            try:
                import pandas as pd
            except ImportError:
                return '服务器未安装 pandas，xlsx 导入不可用；请改用 JSON/CSV 模板'
            df = pd.read_excel(file_storage.stream)
            df = df.astype(object).where(df.notna(), None)  # NaN -> None，避免校验误伤
            return df.to_dict('records')
        return f'不支持的文件类型: {filename}（支持 .json/.csv/.xlsx）'
    except Exception as e:
        return f'文件解析失败: {e}'


@bp.route('/api/resources/<rtype>/template')
def download_resource_template(rtype):
    """下载导入模板（JSON：entry_schema 字段说明 + 示例行）"""
    provider = _get_resource_provider(rtype)
    if provider is None:
        return jsonify({'success': False, 'error': f'未知资源类型: {rtype}'}), 404
    try:
        resp = jsonify({'success': True, 'template': provider.template()})
        resp.headers['Content-Disposition'] = f'attachment; filename="{rtype}_template.json"'
        return resp
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 统计看板 ====================

@bp.route('/api/stats')
def get_stats():
    """统计看板：题库/错题/生成/基础数据/人工标注全量指标"""
    try:
        with db_manager.connect_governance() as conn:
            cursor = conn.execute('SELECT COUNT(*) FROM qa_pairs')
            qa_total = cursor.fetchone()[0]

            cursor = conn.execute('SELECT COUNT(*) FROM error_records')
            error_total = cursor.fetchone()[0]

            cursor = conn.execute('SELECT COUNT(*) FROM error_records WHERE is_resolved = 1')
            error_resolved = cursor.fetchone()[0]

            cursor = conn.execute('''
                SELECT error_type, COUNT(*) as cnt 
                FROM error_records 
                GROUP BY error_type 
                ORDER BY cnt DESC
            ''')
            error_types = [{'type': row[0], 'count': row[1]} for row in cursor.fetchall()]

            # 难度/来源分布（看板图表 + 题库筛选栏统计）
            cursor = conn.execute('SELECT difficulty, COUNT(*) FROM qa_pairs GROUP BY difficulty')
            difficulty_dist = [{'name': row[0] or '未标注', 'count': row[1]} for row in cursor.fetchall()]
            cursor = conn.execute('SELECT source, COUNT(*) FROM qa_pairs GROUP BY source ORDER BY COUNT(*) DESC')
            source_dist = [{'name': row[0] or '未知', 'count': row[1]} for row in cursor.fetchall()]

            # 基础数据指标：表/字段/关系/码值/题库可用性
            basic = {}
            for key, sql in (
                ('tables', 'SELECT COUNT(*) FROM schema_table_docs'),
                ('columns', 'SELECT COUNT(*) FROM schema_column_docs'),
                ('relationships', 'SELECT COUNT(*) FROM schema_relationship_docs'),
                ('code_domains', 'SELECT COUNT(*) FROM code_values'),
                ('code_items', 'SELECT COUNT(*) FROM code_value_items'),
                ('qa_usable', 'SELECT COUNT(*) FROM qa_pairs WHERE is_usable = 1'),
                ('qa_pending', 'SELECT COUNT(*) FROM qa_pairs WHERE is_usable IS NULL OR is_usable = 0'),
            ):
                cursor = conn.execute(sql)
                basic[key] = cursor.fetchone()[0]

        # 表/列元数据权威源 = 业务库 information_schema（经 SchemaPreloader 单例缓存），
        # 覆盖上面的治理库文档计数；关系仍取治理库 schema_relationship_docs
        try:
            preloader = get_schema_preloader()
            table_names = preloader.get_table_names()
            basic['tables'] = len(table_names)
            basic['columns'] = sum(len(preloader.get_columns(t)) for t in table_names)
        except Exception as e:
            print(f"[WARN] information_schema 表/列计数失败，沿用治理库文档计数: {e}")

        # 运行日志统计（日志库）
        with db_manager.connect_log() as conn:
            cursor = conn.execute('SELECT COUNT(*) FROM generation_logs')
            gen_total = cursor.fetchone()[0]

            cursor = conn.execute('''
                SELECT user_judgment, COUNT(*) as cnt
                FROM generation_logs
                WHERE user_judgment IS NOT NULL
                GROUP BY user_judgment
            ''')
            judgments = {row[0]: row[1] for row in cursor.fetchall()}

            # 细粒度标注统计
            cursor = conn.execute('''
                SELECT
                    COUNT(*) as annotated,
                    SUM(CASE WHEN table_choice_correct = 1 THEN 1 ELSE 0 END) as table_correct,
                    SUM(CASE WHEN field_choice_correct = 1 THEN 1 ELSE 0 END) as field_correct,
                    SUM(CASE WHEN join_path_correct = 1 THEN 1 ELSE 0 END) as join_correct,
                    SUM(CASE WHEN where_condition_correct = 1 THEN 1 ELSE 0 END) as where_correct,
                    SUM(CASE WHEN aggregation_correct = 1 THEN 1 ELSE 0 END) as agg_correct
                FROM generation_logs
                WHERE table_choice_correct IS NOT NULL
            ''')
            annotation_row = cursor.fetchone()
            annotation_stats = {
                'annotated_count': annotation_row[0] or 0,
                'table_choice_correct': annotation_row[1] or 0,
                'field_choice_correct': annotation_row[2] or 0,
                'join_path_correct': annotation_row[3] or 0,
                'where_condition_correct': annotation_row[4] or 0,
                'aggregation_correct': annotation_row[5] or 0
            }

            # 生成健康：平均耗时、平均尝试次数、执行成功率
            cursor = conn.execute('''
                SELECT AVG(latency_ms), AVG(attempts),
                       SUM(CASE WHEN execution_status = 'success' THEN 1 ELSE 0 END) * 1.0 / COUNT(*)
                FROM generation_logs
            ''')
            health_row = cursor.fetchone()
            gen_health = {
                'avg_latency_ms': round(health_row[0] or 0),
                'avg_attempts': round(health_row[1] or 0, 2),
                'exec_success_rate': round((health_row[2] or 0) * 100, 1)
            }

        # ---- 指标快照（F1.2 增补）：把本次指标按天落库并取回近 120 天序列 + 环比 ----
        # KPI 卡的趋势线/环比需要历史，而本接口是快照；快照随使用自然积累，无需调度器。
        # 任一环节失败都不得影响看板主流程，故整段静默兜底。
        kpi_series = {'series': {}, 'delta7': {}}
        try:
            from core.stats_history import capture_and_load
            kpi_series = capture_and_load({
                'tables': basic.get('tables', 0),
                'columns': basic.get('columns', 0),
                'code_domains': basic.get('code_domains', 0),
                'qa_pairs': qa_total,
                'exec_success_rate': gen_health.get('exec_success_rate', 0),
            })
        except Exception as e:
            print(f"[WARN] 指标快照写入/读取失败（看板不受影响）: {e}")

        return jsonify({
            'success': True,
            'data': {
                'qa_pairs': {'total': qa_total},
                'error_records': {
                    'total': error_total,
                    'resolved': error_resolved,
                    'unresolved': error_total - error_resolved,
                    'type_distribution': error_types
                },
                'generation': {
                    'total': gen_total,
                    'correct': judgments.get('正确', 0),
                    'error': judgments.get('错误', 0),
                    'skipped': judgments.get('跳过', 0)
                },
                'qa_pairs_dist': {'difficulty': difficulty_dist, 'source': source_dist},
                'generation_health': gen_health,
                'basic': basic,
                'human_annotation': annotation_stats,
                # 指标历史序列（近 120 天逐日 + 与 ≤7 天前基准的差值）——KPI 趋势线/环比数据源
                'kpi_series': kpi_series
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/generation-logs')
def get_generation_logs():
    """运行日志分页列表（工作流页签穿透目标）"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        offset = (page - 1) * per_page

        with db_manager.connect_log() as conn:
            cursor = conn.execute(
                '''SELECT id, user_question, generated_sql, execution_status,
                          review_passed, user_judgment, attempts, latency_ms, row_count, created_at
                   FROM generation_logs ORDER BY id DESC LIMIT ? OFFSET ?''',
                (per_page, offset)
            )
            rows = cursor.fetchall()
            cursor = conn.execute('SELECT COUNT(*) FROM generation_logs')
            total = cursor.fetchone()[0]

        items = [{
            'id': r[0],
            'question': r[1] or '',
            'generated_sql': r[2] or '',
            'execution_status': r[3] or '',
            'review_passed': bool(r[4]),
            'user_judgment': r[5] or '',
            'attempts': r[6] or 1,
            'latency_ms': r[7] or 0,
            'row_count': r[8],
            'created_at': r[9]
        } for r in rows]

        return jsonify({'success': True, 'total': total, 'page': page, 'per_page': per_page, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/human-review-sample')
def get_human_review_sample():
    """随机抽取待人工复核的生成记录"""
    try:
        limit = request.args.get('limit', 5, type=int)
        with db_manager.connect_log() as conn:
            cursor = conn.execute('''
                SELECT id, session_id, user_question, generated_sql, review_passed, execution_status, user_judgment
                FROM generation_logs
                WHERE user_judgment IS NULL
                ORDER BY RAND()
                LIMIT ?
            ''', (limit,))
            rows = cursor.fetchall()

        items = []
        for row in rows:
            items.append({
                'log_id': row[0],
                'session_id': row[1],
                'question': row[2] or '',
                'generated_sql': row[3] or '',
                'review_passed': bool(row[4]),
                'execution_status': row[5] or '',
                'user_judgment': row[6] or ''
            })

        return jsonify({'success': True, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
