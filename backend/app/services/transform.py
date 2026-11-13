"""采集格式转换业务规则：状态流转、字段校验与筛选口径都收在这里。

模板的字段映射是统计报表快照的输入格式来源，因此模板版本必须跟着映射规则走：
每次修改 `映射规则` 都会把 `模板版本` 递增一档。已经生成的统计报表快照按生成时
冻结的版本展示，不受这里升版影响；只有新生成的快照才会采用新版本。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.report import next_template_version

MODULE = "transform"
REPORT_MODULE = "report"
SNAPSHOT_MODULE = "report_snapshot"
REQUIRED_FIELDS = ["模板编号", "输入格式", "输出格式"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用模板": "已启用", "停用模板": "已停用", "更新映射规则": None}
NEGATIVE_ACTIONS = ["停用模板"]


class TransformService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("模板编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = rows[start:start + size]
        items = [self._with_references(row) for row in page_rows]
        return items, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._with_references(entry) if entry else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in ["映射规则", "编码设置", "分隔符", "换行策略"]:
            entry[field] = values.get(field)
        entry["是否启用"] = values.get("是否启用") or "否"
        entry["模板版本"] = str(values.get("模板版本") or "v1")
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"转换模板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集格式转换可执行范围"
        if action == "更新映射规则":
            return self._update_mapping(entry, values or {})
        target = ACTION_RULES[action]
        if target is None or target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        entry["是否启用"] = "是" if target == "已启用" else "否"
        references = self._report_references(str(entry.get("模板编号", "")))
        if action == "停用模板" and references:
            return entry, (
                f"转换模板已停用；引用它的统计报表仍可查看历史快照，"
                f"再次生成时需重新选择模板（当前引用 {len(references)} 条报表）"
            )
        return entry, f"转换模板已{action}"

    def _update_mapping(
        self, entry: dict[str, Any], values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        mapping = values.get("映射规则") or values.get("mapping")
        if mapping is None or not str(mapping).strip():
            return None, "更新映射规则需要提供「映射规则」内容"
        entry["映射规则"] = str(mapping).strip()
        for field in ["输入格式", "输出格式", "编码设置", "分隔符", "换行策略"]:
            if values.get(field) not in (None, ""):
                entry[field] = str(values.get(field))
        entry["模板版本"] = next_template_version(str(entry.get("模板版本") or "v1"))
        entry["pending"] = True
        affected = self._report_references(str(entry.get("模板编号", "")))
        return entry, (
            f"转换模板映射规则已更新，版本升为 {entry['模板版本']}；"
            f"引用该模板的 {len(affected)} 条统计报表仍按生成时冻结版本展示，只影响后续生成"
        )

    # ------------------------------------------------------------------
    # 联动：模板被哪些统计报表引用
    # ------------------------------------------------------------------
    def _report_references(self, template_code: str) -> list[dict[str, Any]]:
        if not template_code:
            return []
        # 触发一次历史报表快照回填，让「被引用」口径与报表页看到的一致。
        from app.services.report import ReportService

        ReportService()._ensure_backfilled()
        references = []
        for report in store.rows(REPORT_MODULE):
            if str(report.get("模板编号", "")) != template_code:
                continue
            snapshots = [
                row for row in store.rows(SNAPSHOT_MODULE)
                if int(row.get("报表id", 0)) == int(report.get("id", 0))
            ]
            references.append({
                "报表id": report.get("id"),
                "报表编号": report.get("报表编号"),
                "统计维度": report.get("统计维度"),
                "统计周期": report.get("统计周期"),
                "报表状态": report.get("status"),
                "引用方式": "当前选用",
                "快照版本": sorted({str(row.get("模板版本")) for row in snapshots}),
                "快照数量": len(snapshots),
            })
        for snapshot in store.rows(SNAPSHOT_MODULE):
            if str(snapshot.get("模板编号", "")) != template_code:
                continue
            if any(int(item["报表id"]) == int(snapshot.get("报表id", 0)) for item in references):
                continue
            references.append({
                "报表id": snapshot.get("报表id"),
                "报表编号": snapshot.get("报表编号"),
                "统计维度": snapshot.get("统计维度"),
                "统计周期": snapshot.get("统计周期"),
                "报表状态": "仅快照引用",
                "引用方式": "历史快照",
                "快照版本": [snapshot.get("模板版本")],
                "快照数量": 1,
            })
        return references

    def _with_references(self, row: dict[str, Any]) -> dict[str, Any]:
        references = self._report_references(str(row.get("模板编号", "")))
        enriched = dict(row)
        enriched["引用报表数"] = len(references)
        enriched["引用报表"] = [
            {
                "报表编号": item["报表编号"],
                "统计周期": item["统计周期"],
                "报表状态": item["报表状态"],
                "引用方式": item["引用方式"],
                "快照版本": item["快照版本"],
            }
            for item in references
        ]
        return enriched

    def references_overview(self) -> dict[str, Any]:
        items = []
        for row in store.rows(MODULE):
            references = self._report_references(str(row.get("模板编号", "")))
            items.append({
                "模板id": row.get("id"),
                "模板编号": row.get("模板编号"),
                "模板版本": row.get("模板版本"),
                "模板状态": row.get("status"),
                "引用报表数": len(references),
                "引用报表": references,
            })
        return {
            "items": items,
            "total": len(items),
            "被引用模板数": sum(1 for item in items if item["引用报表数"] > 0),
        }
