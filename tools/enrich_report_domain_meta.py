# -*- coding: utf-8 -*-
"""报表实体域元数据补全（让规划器/检索能"理解"本体报表层，2026-09-04）

背景：report 层 5 个域实体（ReportMarketing/Grid/Project/Urban/Stats）的 comment 只有一句话，
规划器 _entity_vocab 按意图词匹配 名称/标签/注释/成员表 时命中面太窄。
本脚本把每个域实体的 comment 富化为：域说明 + 成员表中文名清单 + 主题词清单，
并重建本体换版（幂等）。

用法：python tools/enrich_report_domain_meta.py [--dry-run]
"""
import argparse
import json
import sys
from datetime import datetime

sys.path.insert(0, r'D:\codex\deshu5')
import pymysql

import config

DB_GOV = 'fz01_governance'
DB_ONT = 'fz01_ontology'

# 域实体 → 主题词（供意图命中；由成员表实际内容提炼）
DOMAIN_TOPICS = {
    'ReportMarketing': '业扩报装 户数 用电户数 电量电费 应收电费 电费回收 台账 计费 营销 客户 新装 销户',
    'ReportGrid': '全社会用电量 昨日电量 负荷 负荷曲线 装机容量 光伏 双碳 碳排放 电网 日用电量 96点',
    'ReportProject': '行业用电 分行业 全社会用电情况 区县用电 发展报告 发电生产 电厂 综合情况 专题',
    'ReportUrban': '城市建设 供水 用水人口 供水管道 排水 污水 污水处理 再生水 污泥 年鉴 城市 省',
    'ReportStats': '统计局 月度卡片 GDP 地区生产总值 工业 投资 固定资产 消费 社会消费品零售 进出口 外经 价格 CPI 财政 金融 保险 就业 居民收入 居民收支 信心指数 能源 用电量 交通 邮电 服务业 主要指标 国民经济',
}


def connect(db):
    return pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                           user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                           database=db, charset='utf8mb4')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    gov = connect(DB_GOV)
    ont = connect(DB_ONT)
    now = datetime.now()

    with gov.cursor() as gcur, ont.cursor() as ocur:
        ocur.execute("SELECT name, label, member_tables FROM ontology_entity_defs "
                     "WHERE layer='report' AND name LIKE 'Report%'")
        domains = ocur.fetchall()
        for name, label, mt in domains:
            tables = json.loads(mt or '[]')
            # 成员表中文名（治理库 table_comment，去括注）
            cn_names = []
            if tables:
                gcur.execute('SELECT table_name, table_comment FROM schema_table_docs '
                             'WHERE table_name IN (%s)' % ','.join(['%s'] * len(tables)),
                             tables)
                cn_map = {t: (c or '').split('（')[0].strip() for t, c in gcur.fetchall()}
                cn_names = [cn_map.get(t, '') for t in tables if cn_map.get(t)]
            comment = (f'{label}：按业务域聚合的统计报表实体类。'
                       f'成员报表：{"、".join(cn_names)}。'
                       f'主题词：{DOMAIN_TOPICS.get(name, "")}')
            if args.dry_run:
                print(f'[dry] {name}（{label}）: {len(tables)} 表，comment {len(comment)} 字')
                print('      ', comment[:150])
                continue
            ocur.execute('UPDATE ontology_entity_defs SET comment=%s, updated_at=%s WHERE name=%s',
                         (comment, now, name))
            print(f'[ok] {name}: comment {len(comment)} 字')
    if not args.dry_run:
        ont.commit()
    ont.close()

    if args.dry_run:
        return
    # 本体换版
    from core.ontology.service import OntologyService
    svc = OntologyService()
    result = svc.rebuild_proposal()
    ont_new = svc.approve(result['proposal_id'])
    print(f'本体提案 #{result["proposal_id"]} 已批准，版本 v{ont_new.version}')

    gov.close()
    print('[done] 报表实体域元数据补全完成')


if __name__ == '__main__':
    main()
