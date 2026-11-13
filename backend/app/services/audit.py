"""采集审计日志业务规则：状态流转、字段校验与筛选口径都收在这里。

统计报表生成成功后，报表服务会登记一条审计记录并生成一条待归档清单；
这里只负责展示盘点和回写查询，真正的写入动作在 `ReportService.generate_snapshot`
的提交阶段完成，保证失败时不会留下半条清单。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "audit"
ARCHIVE_MODULE = "audit_archive"
REQUIRED_FIELDS = ["记录编号", "操作类型", "采集计划"]
STATUS_ORDER = ["已记录", "已复核", "已归档"]
ACTION_RULES = {"标记复核": "已记录", "归档记录": "已复核"}
NEGATIVE_ACTIONS = []


class AuditService:
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
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"审计记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集审计日志可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        entry["复核状态"] = "已复核" if action == "标记复核" else entry.get("复核状态") or "待复核"
        if action == "归档记录":
            self._sync_archive(entry, "已归档")
        return entry, f"审计记录已{action}"

    def _sync_archive(self, audit_record: dict[str, Any], status: str) -> None:
        archive_code = str(audit_record.get("归档清单编号") or "")
        for archive in store.rows(ARCHIVE_MODULE):
            if str(archive.get("清单编号", "")) == archive_code:
                archive["状态"] = status
                archive["回写状态"] = f"审计清单{status}"
                archive["复核状态"] = (
                    "已复核" if status == "已归档" else archive.get("复核状态")
                )
                return

    # ------------------------------------------------------------------
    # 联动入口：待归档清单
    # ------------------------------------------------------------------
    def archive_clause(self, *, keyword: str | None = None) -> dict[str, Any]:
        """采集审计记录生成待归档清单。

        清单来源是报表生成动作登记的审计记录；这里按「待归档」口径汇总，
        已取代的清单单独标记，便于审计人员区分同一周期的成功版本。
        """
        items = []
        totals = {"待归档": 0, "已归档": 0, "已取代": 0}
        for archive in sorted(store.rows(ARCHIVE_MODULE), key=lambda row: int(row.get("id", 0))):
            status = str(archive.get("状态") or "待归档")
            totals[status] = totals.get(status, 0) + 1
            if keyword and keyword not in str(archive.get("报表编号", "")) \
                    and keyword not in str(archive.get("清单编号", "")):
                continue
            items.append(dict(archive))
        return {"items": items, "total": len(items), "统计": totals}

    def audit_backwrite(self, audit_id: int | None = None) -> dict[str, Any]:
        """生成结果回写到审计清单：把审计记录与对应归档清单拼成一条可核对的结果。"""
        items = []
        for audit in store.rows(MODULE):
            if audit_id is not None and int(audit.get("id", 0)) != audit_id:
                continue
            if audit.get("操作类型") != "统计报表生成":
                continue
            archive_code = str(audit.get("归档清单编号") or "")
            archive = next(
                (
                    row for row in store.rows(ARCHIVE_MODULE)
                    if str(row.get("清单编号", "")) == archive_code
                ),
                None,
            )
            items.append({
                "审计记录id": audit.get("id"),
                "记录编号": audit.get("记录编号"),
                "采集计划": audit.get("采集计划"),
                "执行人": audit.get("执行人"),
                "执行时间": audit.get("执行时间"),
                "结果摘要": audit.get("结果摘要"),
                "复核状态": audit.get("复核状态"),
                "清单编号": archive_code,
                "清单状态": (archive or {}).get("状态", "未生成清单"),
                "回写状态": (archive or {}).get("回写状态", "—"),
                "回写结果": (archive or {}).get("回写结果", "—"),
                "快照编号": (archive or {}).get("快照编号"),
                "模板版本": (archive or {}).get("模板版本"),
                "报表编号": (archive or {}).get("报表编号"),
            })
        return {"items": items, "total": len(items)}
