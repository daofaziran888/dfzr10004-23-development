"""采集统计报表业务规则：状态流转、字段校验与筛选口径都收在这里。

统计报表的历史内容不再依赖当前转换模板配置：每次成功生成都会落一份
`report_snapshot`，冻结统计维度、统计周期、指标集合、数据来源与转换模板版本。
模板后续变更只影响新生成的快照，旧快照按生成时冻结的内容展示。

快照生成采用“先组装、后提交”的方式：组装完成前不写任何可见记录，失败时只留一条
不含报表内容的生成日志，因此不会出现半成品。同一报表编号 + 统计周期只保留一个
成功快照，重复生成会原子替换旧快照，并在审计清单里把被替换条目标记为已取代。
"""
from __future__ import annotations

import re
import threading
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "report"
SNAPSHOT_MODULE = "report_snapshot"
GENERATION_LOG_MODULE = "report_generation_log"
AUDIT_ARCHIVE_MODULE = "audit_archive"
TRANSFORM_MODULE = "transform"
AUDIT_MODULE = "audit"

REQUIRED_FIELDS = ["报表编号", "统计维度", "统计周期"]
STATUS_ORDER = ["草稿", "生成中", "已生成", "已归档"]
ACTION_RULES = {"生成报表": "已生成", "归档报表": "已归档", "删除报表": "已归档"}
NEGATIVE_ACTIONS = []
GENERATABLE_STATUSES = {"草稿", "生成中", "已生成"}
SNAPSHOT_SUCCESS = "成功"
SNAPSHOT_BACKFILL = "历史回填"

# 这些模板字段会整体快照进报表快照；模板改字段映射后旧报表仍按这份冻结内容展示。
TEMPLATE_FROZEN_FIELDS = ["输入格式", "输出格式", "映射规则", "编码设置", "分隔符", "换行策略"]


# 同步接口在线程池里执行，并发生成同一周期时用锁把“组装 + 提交”串起来，
# 保证同一报表编号 + 统计周期最终只剩一个成功快照。
_GENERATION_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(module: str) -> int:
    rows = store.rows(module)
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _next_code(module: str, field: str, prefix: str) -> str:
    pattern = re.compile(rf"{re.escape(prefix)}-(\d+)")
    highest = 0
    for row in store.rows(module):
        matched = pattern.fullmatch(str(row.get(field, "")))
        if matched:
            highest = max(highest, int(matched.group(1)))
    return f"{prefix}-{highest + 1:04d}"


def _bump_version(current: str) -> str:
    matched = re.fullmatch(r"v(\d+)", str(current or "").strip())
    if matched:
        return f"v{int(matched.group(1)) + 1}"
    return f"{current}.1" if current else "v1"


class ReportService:
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
            rows = [row for row in rows if keyword in str(row.get("报表编号", ""))]
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
        entry["模板编号"] = str(values.get("模板编号") or "").strip()
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
            return None, f"统计报表 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集统计报表可执行范围"
        if action == "生成报表":
            values = values or {}
            snapshot, message = self.generate_snapshot(
                entry_id,
                template_code=values.get("模板编号") or values.get("template_code"),
                simulate_failure=bool(values.get("模拟失败") or values.get("simulate_failure")),
                operator=str(values.get("执行人") or "值班管理员"),
                plan_code=values.get("采集计划"),
            )
            return (entry if snapshot is not None else None), message
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action == "归档报表" and entry.get("status") != "已生成":
            return None, (
                f"统计报表 {entry.get('报表编号')} 当前为{entry.get('status')}，"
                "只有已生成的报表可以归档"
            )
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"统计报表已{action}"

    # ------------------------------------------------------------------
    # 模板与快照辅助
    # ------------------------------------------------------------------
    def _find_template(self, template_code: str) -> dict[str, Any] | None:
        for row in store.rows(TRANSFORM_MODULE):
            if str(row.get("模板编号", "")) == template_code:
                return row
        return None

    def _template_version(self, template: dict[str, Any] | None) -> str:
        return str((template or {}).get("模板版本") or "v1")

    def _frozen_template_fields(self, template: dict[str, Any] | None) -> dict[str, Any]:
        template = template or {}
        return {field: template.get(field) for field in TEMPLATE_FROZEN_FIELDS}

    def _snapshot_key(self, report_code: str, period: str) -> tuple[str, str]:
        return str(report_code), str(period)

    def _existing_snapshot_ids(self, report_code: str, period: str) -> list[int]:
        key = self._snapshot_key(report_code, period)
        return [
            int(row.get("id", 0))
            for row in store.rows(SNAPSHOT_MODULE)
            if self._snapshot_key(str(row.get("报表编号", "")), str(row.get("统计周期", ""))) == key
        ]

    def _append_generation_log(
        self,
        *,
        report: dict[str, Any],
        template_code: str,
        template_version: str,
        started_at: str,
        result: str,
        failure_reason: str = "",
        snapshot_code: str = "",
        replaced_snapshot_code: str = "",
    ) -> dict[str, Any]:
        log = {
            "id": _next_id(GENERATION_LOG_MODULE),
            "报表id": report.get("id"),
            "报表编号": report.get("报表编号"),
            "统计周期": report.get("统计周期"),
            "模板编号": template_code,
            "模板版本": template_version,
            "开始时间": started_at,
            "结束时间": _now(),
            "结果": result,
            "失败原因": failure_reason,
            "快照编号": snapshot_code,
            "被取代快照": replaced_snapshot_code,
        }
        store.rows(GENERATION_LOG_MODULE).append(log)
        return log

    def _mark_replaced_archives(self, report_code: str, period: str, replacement_code: str) -> None:
        for archive in store.rows(AUDIT_ARCHIVE_MODULE):
            same_key = (
                str(archive.get("报表编号", "")) == str(report_code)
                and str(archive.get("统计周期", "")) == str(period)
            )
            if same_key and archive.get("状态") == "待归档":
                archive["状态"] = "已取代"
                archive["回写状态"] = "已回写（被新快照取代）"
                archive["回写结果"] = f"被新快照 {replacement_code} 取代"
                archive["取代快照编号"] = replacement_code

    # ------------------------------------------------------------------
    # 快照生成
    # ------------------------------------------------------------------
    def generate_snapshot(
        self,
        entry_id: int,
        *,
        template_code: Any = None,
        simulate_failure: bool = False,
        operator: str = "值班管理员",
        plan_code: Any = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """为一条统计报表生成快照。

        成功时返回快照明细；任何一步失败都返回 (None, 可读原因)，且不会留下
        可见快照或待归档清单。`simulate_failure` 只用于回归测试“中途失败”分支。
        """
        with _GENERATION_LOCK:
            return self._generate_snapshot_locked(
                entry_id,
                template_code=template_code,
                simulate_failure=simulate_failure,
                operator=operator,
                plan_code=plan_code,
            )

    def _generate_snapshot_locked(
        self,
        entry_id: int,
        *,
        template_code: Any = None,
        simulate_failure: bool = False,
        operator: str = "值班管理员",
        plan_code: Any = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"统计报表 {entry_id} 不存在或已归档"
        if entry.get("status") not in GENERATABLE_STATUSES:
            return None, f"统计报表 {entry.get('报表编号')} 当前为{entry.get('status')}，不能重新生成"

        report_code = str(entry.get("报表编号") or "")
        period = str(entry.get("统计周期") or "")
        selected_code = str(template_code or entry.get("模板编号") or "").strip()
        started_at = _now()

        if not selected_code:
            message = f"统计报表 {report_code} 尚未选择转换模板，请先选择模板再生成"
            self._append_generation_log(
                report=entry, template_code="", template_version="", started_at=started_at,
                result="失败", failure_reason=message,
            )
            return None, message

        template = self._find_template(selected_code)
        if template is None:
            message = f"转换模板 {selected_code} 不存在，请重新选择模板后生成"
            self._append_generation_log(
                report=entry, template_code=selected_code, template_version="", started_at=started_at,
                result="失败", failure_reason=message,
            )
            return None, message

        template_status = str(template.get("status") or "")
        template_version = self._template_version(template)
        if template_status == "已停用":
            message = (
                f"引用的转换模板 {selected_code} 已停用，统计报表仍可查看历史快照，"
                "再次生成请重新选择可用模板"
            )
            self._append_generation_log(
                report=entry, template_code=selected_code, template_version=template_version,
                started_at=started_at, result="失败", failure_reason=message,
            )
            return None, message
        if template_status != "已启用":
            message = (
                f"转换模板 {selected_code} 当前为{template_status}，请先启用模板后再生成统计报表"
            )
            self._append_generation_log(
                report=entry, template_code=selected_code, template_version=template_version,
                started_at=started_at, result="失败", failure_reason=message,
            )
            return None, message

        # 冻结字段是快照的输入格式依据；不完整时在组装阶段就失败，不落任何可见记录。
        missing_template_fields = [
            field for field in ["映射规则", "输出格式"]
            if not str(template.get(field) or "").strip()
        ]
        if missing_template_fields:
            message = (
                f"转换模板 {selected_code} 缺少冻结所需字段："
                f"{'、'.join(missing_template_fields)}，请补全模板后再生成"
            )
            self._append_generation_log(
                report=entry, template_code=selected_code, template_version=template_version,
                started_at=started_at, result="失败", failure_reason=message,
            )
            return None, message

        # ---- 组装阶段：只操作局部变量，不写任何可见表 ----
        snapshot_id = _next_id(SNAPSHOT_MODULE)
        snapshot_code = f"SNAP-{snapshot_id:04d}"
        replaced_ids = self._existing_snapshot_ids(report_code, period)
        replaced_codes = [
            str(row.get("快照编号"))
            for row in store.rows(SNAPSHOT_MODULE)
            if int(row.get("id", 0)) in replaced_ids
        ]
        snapshot: dict[str, Any] = {
            "id": snapshot_id,
            "快照编号": snapshot_code,
            "报表id": entry.get("id"),
            "报表编号": report_code,
            "统计维度": entry.get("统计维度"),
            "统计周期": period,
            "指标集合": entry.get("指标集合"),
            "数据来源": entry.get("数据来源"),
            "模板编号": selected_code,
            "模板版本": template_version,
            "冻结模板字段": self._frozen_template_fields(template),
            "生成时间": started_at,
            "快照状态": SNAPSHOT_SUCCESS,
            "冻结来源": "生成时冻结",
            "回填标识": False,
            "版本推定": False,
            "冲突提示": "",
            "执行人": operator,
        }
        archive_id = _next_id(AUDIT_ARCHIVE_MODULE)
        archive_code = f"ARCH-{archive_id:04d}"
        audit_code = _next_code(AUDIT_MODULE, "记录编号", "AUDI")
        audit_record: dict[str, Any] = {
            "id": _next_id(AUDIT_MODULE),
            "status": "已记录",
            "pending": True,
            "abnormal": False,
            "记录编号": audit_code,
            "操作类型": "统计报表生成",
            "采集计划": str(plan_code or entry.get("数据来源") or "全平台"),
            "执行人": operator,
            "执行时间": started_at,
            "结果摘要": f"生成统计报表 {report_code}（{period}）快照 {snapshot_code}",
            "变更内容": f"转换模板 {selected_code} 冻结为 {template_version}",
            "复核状态": "待复核",
            "归档清单编号": archive_code,
        }
        archive: dict[str, Any] = {
            "id": archive_id,
            "清单编号": archive_code,
            "报表id": entry.get("id"),
            "报表编号": report_code,
            "快照编号": snapshot_code,
            "统计周期": period,
            "统计维度": entry.get("统计维度"),
            "指标集合": entry.get("指标集合"),
            "模板编号": selected_code,
            "模板版本": template_version,
            "生成时间": started_at,
            "状态": "待归档",
            "回写状态": "已回写",
            "回写结果": f"快照 {snapshot_code} 已生成并登记待归档",
            "回写时间": started_at,
            "复核状态": "待复核",
        }

        if simulate_failure:
            message = "报表快照生成失败：模拟中途失败，已回滚本次生成"
            self._append_generation_log(
                report=entry, template_code=selected_code, template_version=template_version,
                started_at=started_at, result="失败", failure_reason=message,
            )
            return None, message

        # ---- 提交阶段：所有记录一次写入，保证同一周期的成功快照唯一 ----
        if replaced_ids:
            store.drop(
                SNAPSHOT_MODULE,
                lambda row: int(row.get("id", 0)) in replaced_ids,
            )
            self._mark_replaced_archives(report_code, period, snapshot_code)
        store.rows(SNAPSHOT_MODULE).append(snapshot)
        entry["模板编号"] = selected_code
        entry["模板版本"] = template_version
        entry["status"] = "已生成"
        entry["生成时间"] = started_at
        entry["pending"] = False
        entry["abnormal"] = False
        store.rows(AUDIT_ARCHIVE_MODULE).append(archive)
        store.rows(AUDIT_MODULE).append(audit_record)
        self._append_generation_log(
            report=entry, template_code=selected_code, template_version=template_version,
            started_at=started_at, result="成功",
            snapshot_code=snapshot_code,
            replaced_snapshot_code="、".join(replaced_codes),
        )
        return snapshot, (
            f"统计报表 {report_code} 已生成快照 {snapshot_code}"
            f"（模板 {selected_code} {template_version}）"
        )

    # ------------------------------------------------------------------
    # 历史报表回填
    # ------------------------------------------------------------------
    def _ensure_backfilled(self) -> None:
        """给没有快照的历史已生成报表补一份可见快照。

        历史报表没有原始生成快照，只能按回填时的模板内容推定冻结，并在快照上明确
        标注 `历史回填 / 版本推定`。回填只影响查看，不写回审计清单，也不作为
        再次生成的“当前版本”。
        """
        with _GENERATION_LOCK:
            self._ensure_backfilled_locked()

    def _ensure_backfilled_locked(self) -> None:
        for report in store.rows(MODULE):
            if report.get("status") not in {"已生成", "已归档"}:
                continue
            report_code = str(report.get("报表编号") or "")
            period = str(report.get("统计周期") or "")
            if not report_code or not period:
                continue
            if any(
                int(row.get("报表id", 0)) == int(report.get("id", 0))
                for row in store.rows(SNAPSHOT_MODULE)
            ):
                continue
            if self._existing_snapshot_ids(report_code, period):
                continue
            template_code = str(report.get("模板编号") or "").strip()
            template = self._find_template(template_code)
            current_version = self._template_version(template)
            declared_version = str(report.get("模板版本") or "").strip()
            frozen_version = declared_version or current_version
            version_inferred = bool(
                template and (not declared_version or declared_version != current_version)
            )
            if not template:
                conflict_note = "历史报表引用的模板已不存在，冻结内容仅保留报表侧字段"
            elif version_inferred:
                conflict_note = (
                    f"历史报表无原始快照：报表记录版本 {frozen_version} 与当前模板版本 "
                    f"{current_version} 不一致，冻结模板字段按当前内容推定，仅用于查看"
                )
            else:
                conflict_note = "历史报表无原始快照，冻结模板字段按回填时模板内容推定，仅用于查看"
            generated_at = str(report.get("生成时间") or "").strip()
            if not generated_at or generated_at == "—":
                generated_at = _now()

            snapshot_id = _next_id(SNAPSHOT_MODULE)
            snapshot_code = f"SNAP-{snapshot_id:04d}"
            snapshot = {
                "id": snapshot_id,
                "快照编号": snapshot_code,
                "报表id": report.get("id"),
                "报表编号": report_code,
                "统计维度": report.get("统计维度"),
                "统计周期": period,
                "指标集合": report.get("指标集合"),
                "数据来源": report.get("数据来源"),
                "模板编号": template_code,
                "模板版本": frozen_version,
                "冻结模板字段": self._frozen_template_fields(template),
                "生成时间": generated_at,
                "快照状态": SNAPSHOT_BACKFILL,
                "冻结来源": "历史回填",
                "回填标识": True,
                "版本推定": version_inferred,
                "冲突提示": conflict_note,
                "执行人": "系统回填",
            }
            store.rows(SNAPSHOT_MODULE).append(snapshot)
            self._append_generation_log(
                report=report, template_code=template_code, template_version=frozen_version,
                started_at=generated_at, result=SNAPSHOT_BACKFILL, snapshot_code=snapshot_code,
            )

    # ------------------------------------------------------------------
    # 快照查询与看板
    # ------------------------------------------------------------------
    def list_snapshots(
        self,
        *,
        keyword: str | None = None,
        period: str | None = None,
        template_version: str | None = None,
        template_code: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        self._ensure_backfilled()
        rows = sorted(store.rows(SNAPSHOT_MODULE), key=lambda row: int(row.get("id", 0)))
        if keyword:
            needle = str(keyword)
            rows = [
                row for row in rows
                if needle in str(row.get("快照编号", "")) or needle in str(row.get("报表编号", ""))
            ]
        if period:
            rows = [row for row in rows if str(row.get("统计周期", "")) == str(period)]
        if template_version:
            rows = [row for row in rows if str(row.get("模板版本", "")) == str(template_version)]
        if template_code:
            rows = [row for row in rows if str(row.get("模板编号", "")) == str(template_code)]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_snapshot(self, snapshot_id: int) -> dict[str, Any] | None:
        self._ensure_backfilled()
        snapshot = store.find(SNAPSHOT_MODULE, snapshot_id)
        if snapshot is None:
            return None
        template = self._find_template(str(snapshot.get("模板编号", "")))
        current_version = self._template_version(template) if template else "（模板已不存在）"
        current_fields = self._frozen_template_fields(template)
        frozen_fields = snapshot.get("冻结模板字段") or {}
        version_changed = bool(template) and current_version != str(snapshot.get("模板版本", ""))
        content_changed = any(
            current_fields.get(field) != frozen_fields.get(field)
            for field in TEMPLATE_FROZEN_FIELDS
        )
        if not template:
            conflict_note = "当前模板已不存在，展示内容以快照冻结字段为准"
        elif version_changed or content_changed:
            conflict_note = (
                f"当前模板版本 {current_version} 与快照冻结版本 "
                f"{snapshot.get('模板版本')} 不一致；查看与导出以生成时冻结版本为准"
            )
        else:
            conflict_note = "当前模板与快照冻结版本一致"
        return {
            **snapshot,
            "当前模板版本": current_version,
            "模板已变更": version_changed or content_changed,
            "版本冲突说明": conflict_note,
            "冲突处理": "以快照生成时冻结版本为准",
            "当前模板字段": current_fields,
        }

    def snapshot_board(self) -> dict[str, Any]:
        """快照看板：按模板版本分组排列历史版本，并给出可按周期查看的时间线入口。"""
        self._ensure_backfilled()
        rows = sorted(store.rows(SNAPSHOT_MODULE), key=lambda row: int(row.get("id", 0)))
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            key = (str(row.get("模板编号", "")), str(row.get("模板版本", "")))
            group = groups.setdefault(key, {
                "模板编号": key[0],
                "模板版本": key[1],
                "冻结字段": row.get("冻结模板字段") or {},
                "快照数量": 0,
                "快照": [],
            })
            group["快照数量"] += 1
            group["快照"].append({
                "id": row.get("id"),
                "快照编号": row.get("快照编号"),
                "报表编号": row.get("报表编号"),
                "统计维度": row.get("统计维度"),
                "统计周期": row.get("统计周期"),
                "指标集合": row.get("指标集合"),
                "数据来源": row.get("数据来源"),
                "生成时间": row.get("生成时间"),
                "快照状态": row.get("快照状态"),
                "冻结来源": row.get("冻结来源"),
                "回填标识": row.get("回填标识"),
                "版本推定": row.get("版本推定"),
                "冲突提示": row.get("冲突提示"),
            })
        periods = sorted({str(row.get("统计周期", "")) for row in rows if row.get("统计周期")})
        return {
            "版本分组": list(groups.values()),
            "历史版本": [
                {
                    "id": row.get("id"),
                    "快照编号": row.get("快照编号"),
                    "报表编号": row.get("报表编号"),
                    "模板编号": row.get("模板编号"),
                    "模板版本": row.get("模板版本"),
                    "统计周期": row.get("统计周期"),
                    "生成时间": row.get("生成时间"),
                    "快照状态": row.get("快照状态"),
                }
                for row in sorted(
                    rows, key=lambda item: str(item.get("生成时间", "")), reverse=True
                )
            ],
            "统计周期": periods,
            "快照总数": len(rows),
        }

    def generation_timeline(
        self,
        *,
        period: str | None = None,
        keyword: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        self._ensure_backfilled()
        rows = sorted(
            store.rows(GENERATION_LOG_MODULE),
            key=lambda row: (str(row.get("开始时间", "")), int(row.get("id", 0))),
        )
        if period:
            rows = [row for row in rows if str(row.get("统计周期", "")) == str(period)]
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("报表编号", "")) or keyword in str(row.get("快照编号", ""))
            ]
        return rows, len(rows)


# 供联动模块复用：升级模板版本时使用同一套版本号规则。
def next_template_version(current: str) -> str:
    return _bump_version(current)
