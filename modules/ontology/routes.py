# -*- coding: utf-8 -*-
"""本体模型层管理面：/api/ontology/*

- 浏览：summary / classes / relations / enumerations / concepts（读已生效版本）
- 导出：export?format=owl|ttl|nt|jsonld（当前生效版本，文件下载）
- 变更：drift（漂移检测）→ proposals（提案）→ approve/reject（审批）
- 手动重建：rebuild（生成全量刷新提案，仍走审批）
"""
from flask import Blueprint, jsonify, Response

from core.ontology.export import export_ontology
from core.ontology.service import OntologyService

bp = Blueprint('ontology', __name__, url_prefix='/api/ontology')


def _svc() -> OntologyService:
    return OntologyService.get_instance()


def _err(e: Exception, code: int = 500):
    return jsonify({'success': False, 'error': str(e)}), code


# ==================== 总览与浏览 ====================

@bp.route('/summary', methods=['GET'])
def summary():
    """本体总览：计数 + 版本 + 构建时间 + 漂移状态（轻量指纹比对，不生成提案）。"""
    try:
        svc = _svc()
        svc._ensure_loaded()
        meta = svc.store.get_meta()
        data = {'available': svc._ont is not None, 'meta': meta}
        if svc._ont is not None:
            data['summary'] = svc._ont.summary()
        try:
            drift = svc.drift_check(auto_propose=False)
            data['drift'] = drift['drift']
            data['pending_proposal'] = svc.store.has_pending()
        except Exception as e:
            data['drift'] = None
            data['drift_error'] = str(e)
        data['success'] = True
        return jsonify(data)
    except Exception as e:
        return _err(e)


@bp.route('/classes', methods=['GET'])
def classes():
    """类列表。query: kind=dimension|fact|other（可选过滤）。"""
    try:
        from flask import request
        svc = _svc()
        svc._ensure_loaded()
        ont = svc._ont
        if ont is None:
            return jsonify({'success': True, 'items': []})
        kind = request.args.get('kind')
        prop_count = {}
        for p in ont.properties:
            prop_count[p.class_name] = prop_count.get(p.class_name, 0) + 1
        items = [{'name': c.name, 'label': c.label, 'kind': c.kind,
                  'property_count': prop_count.get(c.name, 0)}
                 for c in sorted(ont.classes.values(), key=lambda x: x.name)
                 if not kind or c.kind == kind]
        return jsonify({'success': True, 'items': items, 'total': len(items)})
    except Exception as e:
        return _err(e)


@bp.route('/classes/<name>', methods=['GET'])
def class_detail(name: str):
    """类详情：属性、出入关系、落列码值。"""
    try:
        svc = _svc()
        svc._ensure_loaded()
        ont = svc._ont
        if ont is None or name not in ont.classes:
            return jsonify({'success': False, 'error': f'类不存在: {name}'}), 404
        c = ont.classes[name]
        props = [p.to_dict() for p in ont.properties if p.class_name == name]
        rels = [r.to_dict() for r in ont.relations
                if r.from_class == name or r.to_class == name]
        enums = [{'code_name': e.code_name, 'cn_name': e.cn_name,
                  'column': ref.get('column'), 'form': ref.get('form')}
                 for e in ont.enumerations.values()
                 for ref in e.column_refs if ref.get('table') == name]
        return jsonify({'success': True, 'class': c.to_dict(), 'properties': props,
                        'relations': rels, 'enumerations': enums})
    except Exception as e:
        return _err(e)


@bp.route('/relations', methods=['GET'])
def relations():
    """关系列表。query: source=physical_fk|governance_doc|both（可选过滤）。"""
    try:
        from flask import request
        svc = _svc()
        svc._ensure_loaded()
        ont = svc._ont
        if ont is None:
            return jsonify({'success': True, 'items': []})
        source = request.args.get('source')
        items = [r.to_dict() for r in ont.relations if not source or r.source == source]
        return jsonify({'success': True, 'items': items, 'total': len(items)})
    except Exception as e:
        return _err(e)


@bp.route('/enumerations', methods=['GET'])
def enumerations():
    """码值域列表（含明细与落列）。query: code_name（可选单个详情）。"""
    try:
        from flask import request
        svc = _svc()
        svc._ensure_loaded()
        ont = svc._ont
        if ont is None:
            return jsonify({'success': True, 'items': []})
        code_name = request.args.get('code_name')
        if code_name:
            e = ont.enumerations.get(code_name)
            if e is None:
                return jsonify({'success': False, 'error': f'码值域不存在: {code_name}'}), 404
            return jsonify({'success': True, 'item': e.to_dict()})
        items = [{'code_name': e.code_name, 'cn_name': e.cn_name,
                  'domain': e.domain_l2 or e.domain_l1,
                  'item_count': len(e.items), 'column_refs': e.column_refs}
                 for e in sorted(ont.enumerations.values(), key=lambda x: x.code_name)]
        return jsonify({'success': True, 'items': items, 'total': len(items)})
    except Exception as e:
        return _err(e)


@bp.route('/concepts', methods=['GET'])
def concepts():
    """业务概念→实体映射 + 同义词组。"""
    try:
        svc = _svc()
        svc._ensure_loaded()
        ont = svc._ont
        if ont is None:
            return jsonify({'success': True, 'items': [], 'synonym_groups': {}})
        items = [c.to_dict() for c in sorted(ont.concepts.values(), key=lambda x: x.concept)]
        return jsonify({'success': True, 'items': items, 'total': len(items),
                        'synonym_groups': ont.synonym_groups})
    except Exception as e:
        return _err(e)


# ==================== 实体层（精炼设计） ====================

@bp.route('/entities', methods=['GET'])
def entities():
    """实体列表（已生效版本）。query: layer=master|business|report（可选过滤）。"""
    try:
        from flask import request
        svc = _svc()
        items = svc.get_entities(layer=request.args.get('layer'))
        return jsonify({'success': True, 'items': items, 'total': len(items)})
    except Exception as e:
        return _err(e)


@bp.route('/entities/<name>', methods=['GET'])
def entity_detail(name: str):
    """实体详情：成员表（含注释）、相关实体级关系。"""
    try:
        svc = _svc()
        e = svc.get_entity(name)
        if e is None:
            return jsonify({'success': False, 'error': f'实体不存在: {name}'}), 404
        ont = svc._ont
        members = []
        for t in e['member_tables']:
            c = ont.classes.get(t) if ont else None
            members.append({'table': t, 'label': c.label if c else '',
                            'exists': c is not None})
        rels = [r for r in svc.get_entity_relations()
                if name in (r['from_entity'], r['to_entity'])]
        svc.store.init_tables()
        return jsonify({'success': True, 'entity': e,
                        'def': svc.store.get_entity_def(name),
                        'members': members, 'entity_relations': rels})
    except Exception as e:
        return _err(e)


@bp.route('/entity-relations', methods=['GET'])
def entity_relations():
    """实体间关系（成员表级关系聚合推导）。"""
    try:
        items = _svc().get_entity_relations()
        return jsonify({'success': True, 'items': items, 'total': len(items)})
    except Exception as e:
        return _err(e)


@bp.route('/entity-defs', methods=['GET'])
def entity_defs():
    """实体映射定义（可编辑规则；与已生效版本可能不一致——编辑后需重建提案审批生效）。"""
    try:
        svc = _svc()
        svc.store.init_tables()
        items = svc.store.get_entity_defs()
        return jsonify({'success': True, 'items': items, 'total': len(items)})
    except Exception as e:
        return _err(e)


@bp.route('/entity-defs/<name>/describe', methods=['POST'])
def entity_def_describe(name: str):
    """LLM 生成实体描述。body: {apply?: bool}
    apply=false（默认）仅返回生成的描述（前端回填编辑框，人工确认后随保存落库）；
    apply=true 直接写入映射定义表（批量补全用，仍经「手动重建→审批」进生效版本）。"""
    try:
        from flask import request
        payload = {}
        try:
            payload = request.get_json(force=True) or {}
        except Exception:
            pass
        apply = bool(payload.get('apply'))
        svc = _svc()
        svc._ensure_loaded()
        svc.store.init_tables()
        d = svc.store.get_entity_def(name)
        if d is None:
            return jsonify({'success': False, 'error': f'实体定义不存在: {name}'}), 404
        # 组装真实 Schema 上下文：成员表 + 每表关键列（主键/有中文名的列，截前 12 个）
        ont = svc._ont
        lines = []
        for t in d['member_tables']:
            label = ont.classes[t].label if ont and t in ont.classes else ''
            cols = []
            if ont:
                cols = [f'{p.name}({p.label})' for p in ont.properties
                        if p.class_name == t and (p.is_pk or p.label)][:12]
            lines.append(f'- {t}（{label}）：{"; ".join(cols) if cols else "无列信息"}')
        context = '\n'.join(lines)
        from core.llm_config import call_chat
        prompt = (
            '你是电力营销与电网数据治理专家。请为以下业务实体写一段简洁的中文描述（120-200字），'
            '说明其业务含义、涵盖的数据范围与典型分析场景。'
            '只能基于给定信息概括，禁止编造未提及的表、列或数值。\n\n'
            f'实体名: {name}（{d["label"]}，层级: {d["layer"]}）\n'
            f'成员物理表及关键列:\n{context}\n\n'
            '直接输出描述正文，不要任何前后缀。'
        )
        resp = call_chat([{'role': 'user', 'content': prompt}],
                         max_tokens=2000, thinking=False)
        desc = (resp.get('content') or '').strip()
        if not desc:
            return jsonify({'success': False, 'error': 'LLM 返回空内容'}), 502
        if apply:
            svc.store.upsert_entity_def({**d, 'comment': desc})
        return jsonify({'success': True, 'name': name, 'comment': desc, 'applied': apply,
                        'note': ('描述已写入映射定义' if apply else '描述已生成（未保存，请在编辑框确认后保存）')
                                + '；需「手动重建」生成提案并审批后进入生效版本'})
    except ValueError as e:
        return _err(e, 400)
    except Exception as e:
        return _err(e, 502)


@bp.route('/entity-defs/<name>', methods=['PUT'])
def entity_def_upsert(name: str):
    """新增/更新实体定义。body: {label?, layer?, parent?, member_tables?, comment?, enabled?}"""
    try:
        from flask import request
        payload = request.get_json(force=True) or {}
        layer = payload.get('layer', 'business')
        if layer not in ('master', 'business', 'report'):
            return jsonify({'success': False,
                            'error': 'layer 仅支持 master/business/report'}), 400
        svc = _svc()
        svc.store.init_tables()
        svc.store.upsert_entity_def({
            'name': name,
            'label': payload.get('label', ''),
            'layer': layer,
            'parent': payload.get('parent', ''),
            'member_tables': payload.get('member_tables') or [],
            'comment': payload.get('comment', ''),
            'enabled': payload.get('enabled', 1),
        })
        return jsonify({'success': True,
                        'note': '映射已更新；需「手动重建」生成提案并审批后进入生效版本'})
    except Exception as e:
        return _err(e)


@bp.route('/entity-defs/<name>', methods=['DELETE'])
def entity_def_delete(name: str):
    """逻辑删除实体定义（enabled=0）。"""
    try:
        svc = _svc()
        if not svc.store.delete_entity_def(name):
            return jsonify({'success': False, 'error': f'实体定义不存在: {name}'}), 404
        return jsonify({'success': True,
                        'note': '已停用；需「手动重建」生成提案并审批后进入生效版本'})
    except Exception as e:
        return _err(e)


# ==================== 导出 ====================

@bp.route('/export', methods=['GET'])
def export():
    """导出当前生效版本。query: format=owl|ttl|nt|jsonld。"""
    try:
        from flask import request
        fmt = (request.args.get('format') or 'ttl').lower()
        svc = _svc()
        svc._ensure_loaded()
        if svc._ont is None:
            return jsonify({'success': False, 'error': '本体尚未构建'}), 404
        content, mimetype, ext = export_ontology(svc._ont, fmt)
        filename = f'marketing_ontology_v{svc._ont.version}{ext}'
        return Response(content, mimetype=mimetype,
                        headers={'Content-Disposition':
                                 f"attachment; filename*=UTF-8''{filename}"})
    except ValueError as e:
        return _err(e, 400)
    except Exception as e:
        return _err(e)


# ==================== 漂移检测与提案审批 ====================

@bp.route('/drift', methods=['GET'])
def drift():
    """跑一次漂移检测；有漂移且无 pending 提案时自动生成提案。"""
    try:
        result = _svc().drift_check(auto_propose=True)
        result['success'] = True
        return jsonify(result)
    except Exception as e:
        return _err(e)


@bp.route('/proposals', methods=['GET'])
def proposals():
    try:
        return jsonify({'success': True, 'items': _svc().store.list_proposals()})
    except Exception as e:
        return _err(e)


@bp.route('/proposals/<int:pid>', methods=['GET'])
def proposal_detail(pid: int):
    try:
        p = _svc().store.get_proposal(pid)
        if p is None:
            return jsonify({'success': False, 'error': f'提案不存在: {pid}'}), 404
        p['success'] = True
        return jsonify(p)
    except Exception as e:
        return _err(e)


@bp.route('/proposals/<int:pid>/approve', methods=['POST'])
def proposal_approve(pid: int):
    """批准：提案快照落为新生效版本，并联动失效问数模块缓存。"""
    try:
        svc = _svc()
        ont = svc.approve(pid)
        if ont is None:
            return jsonify({'success': False, 'error': '提案不存在或已处理'}), 404
        try:
            from modules.training.routes import invalidate_generator
            invalidate_generator()
        except Exception:
            pass
        return jsonify({'success': True, 'version': ont.version,
                        'summary': ont.summary()})
    except Exception as e:
        return _err(e)


@bp.route('/proposals/<int:pid>/reject', methods=['POST'])
def proposal_reject(pid: int):
    try:
        ok = _svc().reject(pid)
        if not ok:
            return jsonify({'success': False, 'error': '提案不存在或已处理'}), 404
        return jsonify({'success': True})
    except Exception as e:
        return _err(e)


@bp.route('/rebuild', methods=['POST'])
def rebuild():
    """手动全量重建：生成提案（仍走审批，不直接生效）。"""
    try:
        result = _svc().rebuild_proposal()
        result['success'] = True
        return jsonify(result)
    except Exception as e:
        return _err(e)
