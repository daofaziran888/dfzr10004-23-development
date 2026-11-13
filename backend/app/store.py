"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        # 示例数据只作为初始态保留一份，重置时从这里重新拷贝，避免动作改过的字段串味。
        self._seed: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in self._seed.items()
        }
        # 快照、联动清单这类支撑表只在业务内部使用，不参与运营概览的模块统计。
        self._hidden: set[str] = {
            "report_snapshot", "report_generation_log", "audit_archive",
        }

    def reset(self) -> None:
        """把仓库恢复到初始示例数据；测试与本地反复演练时用。"""
        self._tables = {
            name: [dict(row) for row in rows] for name, rows in self._seed.items()
        }

    def module_names(self) -> list[str]:
        return sorted(name for name in self._tables if name not in self._hidden)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def drop(self, module: str, predicate: Any) -> list[dict[str, Any]]:
        """按条件移除行并返回被移除的行；用于同周期快照的原子替换。"""
        kept: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        for row in self.rows(module):
            if predicate(row):
                removed.append(row)
            else:
                kept.append(row)
        self._tables[module] = kept
        return removed

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
