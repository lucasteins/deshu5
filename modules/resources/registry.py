# -*- coding: utf-8 -*-
"""资源注册表：按机器名管理 ResourceProvider，惰性构造 + 线程安全。"""
import threading


class ResourceRegistry:
    """资源注册表。register 只登记 provider 类，首次 get 时才实例化
    （底层 preloader/rag 是重对象，惰性避免导入即建）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._factories = {}   # name -> provider 类/工厂
        self._instances = {}   # name -> provider 单例

    def register(self, provider_cls):
        """注册 provider 类（以类属性 name 为键）。"""
        name = getattr(provider_cls, 'name', '')
        if not name:
            raise ValueError(f'provider 缺少 name: {provider_cls!r}')
        with self._lock:
            self._factories[name] = provider_cls
            self._instances.pop(name, None)

    def get(self, name):
        """按机器名取 provider 单例；未注册返回 None。"""
        with self._lock:
            if name in self._instances:
                return self._instances[name]
            factory = self._factories.get(name)
        if factory is None:
            return None
        instance = factory()   # 构造可能较重，放锁外避免阻塞其他资源
        with self._lock:
            return self._instances.setdefault(name, instance)

    def all(self) -> list:
        """全部已注册 provider（触发惰性构造），按注册顺序。"""
        return [self.get(name) for name in list(self._factories)]

    def summary(self) -> list:
        """各资源类型摘要：name/label/count/description。"""
        items = []
        for provider in self.all():
            try:
                cnt = provider.count()
            except Exception:
                cnt = None
            items.append({
                'name': provider.name,
                'label': provider.label,
                'description': provider.description,
                'count': cnt,
            })
        return items


# 模块级单例
registry = ResourceRegistry()
