"""采集统计报表接口：维护统计报表，覆盖生成报表、归档报表、删除报表等动作。

报表快照相关接口单独列出：生成时冻结统计维度、统计周期、指标集合、数据来源与
转换模板版本；模板后续变更只影响新报表，快照一律按冻结版本展示。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.report import ReportService

router = APIRouter(prefix="/api/report", tags=["采集统计报表"])

service = ReportService()

LIST_FIELDS = [
    "报表编号", "统计维度", "统计周期", "指标集合", "数据来源",
    "生成时间", "报表格式", "分发方式", "模板编号", "模板版本",
]
STATUSES = ["草稿", "生成中", "已生成", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按报表编号检索"),
    status: str | None = Query(default=None, description="草稿、生成中、已生成、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按报表编号与状态过滤采集统计报表列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


# 静态路径必须放在 /{entry_id} 之前，否则会被当成整数解析而 422。
@router.get("/snapshots", response_model=PageResult[dict])
def list_snapshots(
    keyword: str | None = Query(default=None, description="按快照编号或报表编号检索"),
    period: str | None = Query(default=None, description="按统计周期过滤"),
    template_version: str | None = Query(default=None, description="按冻结模板版本过滤"),
    template_code: str | None = Query(default=None, description="按转换模板编号过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """报表快照视图：按生成时冻结的模板版本展示来源与字段。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_snapshots(
        keyword=keyword, period=period, template_version=template_version,
        template_code=template_code, page=page, size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/snapshots/board", response_model=dict)
def snapshot_board() -> dict:
    """快照看板：按模板版本分组排列历史版本，并返回可切换的统计周期。"""
    return service.snapshot_board()


@router.get("/timeline", response_model=dict)
def generation_timeline(
    period: str | None = Query(default=None, description="按统计周期切换时间线"),
    keyword: str | None = Query(default=None, description="按报表编号或快照编号检索"),
) -> dict:
    """同一周期的生成结果时间线：成功、失败与历史回填都按时间顺序排列。"""
    items, total = service.generation_timeline(period=period, keyword=keyword)
    return {"items": items, "total": total, "period": period}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出采集统计报表清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "report", "total": total, "items": items}


@router.get("/snapshots/{snapshot_id}", response_model=dict)
def get_snapshot(snapshot_id: int) -> dict:
    """读取单份报表快照；附带当前模板版本与冲突说明，展示口径以冻结版本为准。"""
    snapshot = service.get_snapshot(snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"报表快照 {snapshot_id} 不存在")
    return snapshot


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条统计报表明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"统计报表 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条统计报表，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="统计报表已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条统计报表执行生成报表、归档报表、删除报表；不允许的动作会被拦下并说明原因。

    生成报表时 body 可带 `模板编号`（重新选择模板）与 `模拟失败`（回归失败分支）。
    """
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
