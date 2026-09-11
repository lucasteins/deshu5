# -*- coding: utf-8 -*-
"""指标快照 —— KPI「真实趋势线 / 环比」的数据源（F1.2 增补）

## 为什么需要它

`GET /api/stats` 是**快照接口**：只返回「此刻各指标的值」，不含历史。因此统计看板的
KPI 卡只能对**恰好带时间列**的指标画趋势（目前仅问答对，靠 `qa_pairs.ingest_time`），
业务表 / 字段总数 / 码值域 / 执行成功率都画不出来。

本模块思路：**把每次 `/api/stats` 的指标值按天落一行**（幂等，一天一行）。
历史随系统被使用而自然积累，**无需调度器**；积累 ≥2 期后，前端即可绘出真实趋势线与环比。

## 契约

- 表：`stats_snapshots`（治理库），`UNIQUE(metric_key, captured_on)`
- 写入：`capture_and_load(values)` —— upsert 当日值，同一次连接内读完序列（少开一条连接）
- 读取：近 `days` 天逐日序列 + 与「≤7 天前最近一条」的差值（`delta7`）
- 护栏：**任何异常都不得影响 `/api/stats` 主流程**；调用方另有一层 try/except
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Mapping

from core.database import DatabaseManager

#: 参与快照的指标键（与前端 KPI 卡一一对应）
METRIC_KEYS = ('tables', 'columns', 'code_domains', 'qa_pairs', 'exec_success_rate')

_DDL = '''
    CREATE TABLE IF NOT EXISTS stats_snapshots (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        metric_key VARCHAR(48) NOT NULL,
        captured_on DATE NOT NULL,
        value DECIMAL(16,2) NOT NULL DEFAULT 0,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_metric_day (metric_key, captured_on)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
'''


def _ensure_table(conn) -> None:
    conn.execute(_DDL)


def init_stats_snapshots_table() -> None:
    """建表（幂等）——供 app 启动时的 init_all_tables() 调用"""
    db = DatabaseManager()
    with db.connect_governance() as conn:
        _ensure_table(conn)
        conn.commit()


def _to_number(value: Any) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def capture_and_load(values: Mapping[str, Any], days: int = 120) -> Dict[str, Any]:
    """写入当日快照 → 返回近 `days` 天序列 + 环比。

    返回：
        {
          'series': {'tables': [{'d': '2026-09-11', 'v': 35}, ...], ...},
          'delta7': {'tables': 0.0, ...},   # 缺失可比基准的指标不出现在此
          'days': 120,
        }
    """
    result: Dict[str, Any] = {'series': {}, 'delta7': {}, 'days': days}
    today = date.today()
    cutoff = today - timedelta(days=7)
    since = today - timedelta(days=days)

    db = DatabaseManager()
    with db.connect_governance() as conn:
        _ensure_table(conn)

        # ① 当日 upsert（幂等：同一天多次调用只更新值）
        for key in METRIC_KEYS:
            if key not in values:
                continue
            v = _to_number(values[key])
            conn.execute(
                'INSERT INTO stats_snapshots (metric_key, captured_on, value) '
                'VALUES (?, ?, ?) ON DUPLICATE KEY UPDATE value = ?',
                (key, today.isoformat(), v, v),
            )
        conn.commit()

        # ② 近 days 天逐日序列（升序）
        cursor = conn.execute(
            'SELECT metric_key, captured_on, value FROM stats_snapshots '
            'WHERE captured_on >= ? ORDER BY captured_on ASC',
            (since.isoformat(),),
        )
        rows = cursor.fetchall()

    series: Dict[str, list] = {}
    for metric_key, captured_on, value in rows:
        day = captured_on.isoformat() if hasattr(captured_on, 'isoformat') else str(captured_on)
        series.setdefault(metric_key, []).append({'d': day, 'v': _to_number(value)})

    cutoff_iso = cutoff.isoformat()
    delta7: Dict[str, float] = {}
    for metric_key, points in series.items():
        if len(points) < 2:
            continue
        latest = points[-1]['v']
        base = None
        for point in points:            # 升序：取「≤7 天前」的最后一条作为基准
            if point['d'] <= cutoff_iso:
                base = point['v']
        if base is not None:
            delta7[metric_key] = round(latest - base, 2)

    result['series'] = series
    result['delta7'] = delta7
    return result
