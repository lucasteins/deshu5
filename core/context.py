# -*- coding: utf-8 -*-
"""进程级共享单例：连接工厂 + 重对象缓存

各业务模块统一从这里取实例，避免重复构造重对象
（RAGRetriever 预热、SchemaLoader 解析等）。
"""
import config
from core.database import DatabaseManager
from core.schema_loader import SchemaLoader
from core.rag_retriever import RAGRetriever

# 连接工厂（无状态，可安全共享）
db_manager = DatabaseManager()

# 重对象单例（启动时预热，见 app.py）
schema_loader = SchemaLoader()
rag_retriever = RAGRetriever(top_k=config.RAG_TOP_K)


def get_schema_preloader():
    """Schema 预加载器单例（惰性，模块内延迟导入避免循环依赖）。"""
    from core.schema_preloader import SchemaPreloader
    return SchemaPreloader.get_instance()


def switch_database(profile: str) -> dict:
    """运行时切换数据库档位（生产/暂存），并失效全部缓存对象。

    1. db_profile 持久化切换（连接层每次建连读取，即时生效）
    2. SchemaPreloader 清内存态 → 下次 preload 从新库 information_schema 重载（非破坏）
    3. schema_loader / rag_retriever 缓存失效
    4. 训练模块惰性生成器失效 + 会话缓存清空
    """
    from core import db_profile

    current = db_profile.switch(profile)
    print(f'[db_profile] 切换到 {current["label"]} '
          f'({current["business"]} / {current["governance"]} / {current["log"]})', flush=True)

    # SchemaPreloader：清内存态后重载（DB-first 路径，不重写治理文档）
    try:
        p = get_schema_preloader()
        p.parser = None
        p._global_context = ''
        p.preload()
    except Exception as e:
        print(f"[WARN] Schema 预加载重载失败: {e}")

    # SchemaLoader 清缓存
    try:
        schema_loader.load_schema(force_refresh=True)
    except Exception as e:
        print(f"[WARN] SchemaLoader 重载失败: {e}")

    # RAG 缓存失效（有效表/字段 + 码值索引世代号）
    try:
        rag_retriever.reset()
    except Exception as e:
        print(f"[WARN] RAG 缓存重置失败: {e}")

    # 训练模块：失效惰性生成器（SQLGenerator 等自持 SchemaLoader/RAG 实例）+ 清会话
    try:
        from modules.training.routes import invalidate_generator, session_cache
        invalidate_generator()
        session_cache.clear()
    except Exception as e:
        print(f"[WARN] 训练模块缓存重置失败: {e}")

    return current


def warmup():
    """启动预热：Schema 预加载 + RAG 分词/检索缓存。失败仅告警不阻断启动。"""
    try:
        get_schema_preloader().preload()
    except Exception as e:
        print(f"[WARN] Schema 预加载失败: {e}")
    try:
        rag_retriever.extract_keywords('预热分词')
        rag_retriever.retrieve('预热检索')
        schema_loader.load_schema()
    except Exception as e:
        print(f"[WARN] RAG/Schema 预热失败: {e}")
