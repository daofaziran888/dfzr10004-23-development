"""报表快照回归：用 REPO-0002 与 TRAN-0002 覆盖快照冻结、原子失败、同周期唯一、
模板停用与版本冲突、以及审计/模板联动。

直接调用服务层，避免依赖 httpx（当前环境只装了 FastAPI 运行时）。
每个用例前重置内存仓库，种子数据里的 REPO-0002 是草稿、TRAN-0002 是待校验。
"""
from __future__ import annotations

import unittest

from app.store import store
from app.services.audit import AuditService
from app.services.report import ReportService
from app.services.transform import TransformService


class ReportSnapshotRegression(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        self.report = ReportService()
        self.transform = TransformService()
        self.audit = AuditService()
        self.report_id = self._report_id("REPO-0002")
        self.template_id = self._template_id("TRAN-0002")
        # TRAN-0002 种子里是待校验，先启用，模拟“模板可用时生成报表”的真实前置。
        self.transform.run_action(self.template_id, "启用模板")

    # helpers ---------------------------------------------------------
    def _report_id(self, code: str) -> int:
        row = next(r for r in store.rows("report") if r["报表编号"] == code)
        return int(row["id"])

    def _template_id(self, code: str) -> int:
        row = next(r for r in store.rows("transform") if r["模板编号"] == code)
        return int(row["id"])

    def _snapshots(self) -> list[dict]:
        return [dict(r) for r in store.rows("report_snapshot")]

    def _archives(self) -> list[dict]:
        return [dict(r) for r in store.rows("audit_archive")]

    # case 1 ----------------------------------------------------------
    def test_snapshot_freezes_inputs_and_version(self) -> None:
        entry, message = self.report.run_action(self.report_id, "生成报表")
        self.assertIsNotNone(entry, message)
        snap = self._snapshots()[-1]
        self.assertEqual(snap["统计维度"], "按采集计划")
        self.assertEqual(snap["统计周期"], "2026-09-08~2026-09-14")
        self.assertIn("异常重试次数", snap["指标集合"])
        self.assertIn("采集审计日志", snap["数据来源"])
        self.assertEqual(snap["模板编号"], "TRAN-0002")
        self.assertEqual(snap["模板版本"], "v2")
        self.assertIn("映射规则", snap["冻结模板字段"])
        self.assertTrue(snap["生成时间"])
        self.assertEqual(snap["快照状态"], "成功")

    def test_template_change_does_not_drift_existing_snapshot(self) -> None:
        self.report.run_action(self.report_id, "生成报表")
        snap_id = self._snapshots()[-1]["id"]
        frozen = dict(self._snapshots()[-1]["冻结模板字段"])

        entry, message = self.transform.run_action(
            self.template_id, "更新映射规则",
            {"映射规则": "source_id→record_id, title→headline"},
        )
        self.assertIsNotNone(entry, message)
        self.assertEqual(entry["模板版本"], "v3")

        detail = self.report.get_snapshot(snap_id)
        self.assertEqual(detail["模板版本"], "v2")
        self.assertEqual(detail["冻结模板字段"], frozen)
        self.assertTrue(detail["模板已变更"])
        self.assertIn("以快照生成时冻结版本为准", detail["冲突处理"])
        self.assertEqual(detail["当前模板版本"], "v3")

        # 再次生成才会采用新版本；同周期仍只有一个成功快照（被替换）。
        entry, message = self.report.run_action(self.report_id, "生成报表")
        self.assertIsNotNone(entry, message)
        same_period = [
            s for s in self._snapshots()
            if s["报表编号"] == "REPO-0002" and s["统计周期"] == "2026-09-08~2026-09-14"
        ]
        self.assertEqual(len(same_period), 1)
        self.assertEqual(same_period[0]["模板版本"], "v3")

    # case 3 ----------------------------------------------------------
    def test_midway_failure_leaves_no_visible_half_product(self) -> None:
        before = (len(self._snapshots()), len(self._archives()), len(store.rows("report_generation_log")))
        entry, message = self.report.run_action(
            self.report_id, "生成报表", {"模拟失败": True}
        )
        self.assertIsNone(entry)
        self.assertIn("模拟中途失败", message)
        after = (len(self._snapshots()), len(self._archives()), len(store.rows("report_generation_log")))
        self.assertEqual(after[0], before[0], "失败不应留下快照")
        self.assertEqual(after[1], before[1], "失败不应留下待归档清单")
        self.assertEqual(after[2], before[2] + 1, "只保留不含报表内容的失败日志")
        self.assertEqual(store.find("report", self.report_id)["status"], "草稿")
        # 模板引用口径也不应看到半成品
        refs = self.transform.references_overview()
        target = next(item for item in refs["items"] if item["模板编号"] == "TRAN-0002")
        self.assertEqual(target["引用报表数"], 1)
        self.assertEqual(target["引用报表"][-1]["快照数量"], 0)

    # case 4 ----------------------------------------------------------
    def test_same_period_keeps_single_success_snapshot(self) -> None:
        first, _ = self.report.run_action(self.report_id, "生成报表")
        self.assertIsNotNone(first)
        first_code = self._snapshots()[-1]["快照编号"]
        second, message = self.report.run_action(self.report_id, "生成报表")
        self.assertIsNotNone(second, message)
        snaps = [
            s for s in self._snapshots()
            if s["报表编号"] == "REPO-0002" and s["统计周期"] == "2026-09-08~2026-09-14"
        ]
        self.assertEqual(len(snaps), 1)
        self.assertNotEqual(snaps[0]["快照编号"], first_code)
        # 旧待归档清单被标记为已取代，新清单保持待归档
        archives = self._archives()
        self.assertEqual([a["状态"] for a in archives], ["已取代", "待归档"])
        self.assertEqual(archives[0]["取代快照编号"], snaps[0]["快照编号"])
        logs = [
            log for log in store.rows("report_generation_log")
            if log["报表编号"] == "REPO-0002" and log["统计周期"] == "2026-09-08~2026-09-14"
        ]
        self.assertEqual([log["结果"] for log in logs], ["成功", "成功"])
        self.assertEqual(logs[-1]["被取代快照"], first_code)

    # case 5 ----------------------------------------------------------
    def test_disabled_template_keeps_history_but_blocks_regeneration(self) -> None:
        entry, message = self.report.run_action(self.report_id, "生成报表")
        self.assertIsNotNone(entry, message)
        snap_id = self._snapshots()[-1]["id"]

        template_entry, _ = self.transform.run_action(self.template_id, "停用模板")
        self.assertEqual(template_entry["status"], "已停用")
        # 历史快照仍可查看
        detail = self.report.get_snapshot(snap_id)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["快照状态"], "成功")

        entry, message = self.report.run_action(self.report_id, "生成报表")
        self.assertIsNone(entry)
        self.assertIn("已停用", message)
        self.assertIn("重新选择", message)
        # 换成启用中的 TRAN-0003 可以继续生成
        entry, message = self.report.run_action(
            self.report_id, "生成报表", {"模板编号": "TRAN-0003"}
        )
        self.assertIsNotNone(entry, message)
        self.assertEqual(self.report.get_entry(self.report_id)["模板编号"], "TRAN-0003")

    # case 6 ----------------------------------------------------------
    def test_template_reference_listing_shows_report_usage(self) -> None:
        self.report.run_action(self.report_id, "生成报表")
        refs = self.transform.references_overview()
        target = next(item for item in refs["items"] if item["模板编号"] == "TRAN-0002")
        self.assertEqual(target["引用报表数"], 1)
        self.assertEqual(target["引用报表"][0]["报表编号"], "REPO-0002")
        self.assertEqual(target["引用报表"][0]["快照版本"], ["v2"])

    def test_audit_archive_and_backwrite_are_linked(self) -> None:
        before = len([r for r in store.rows("audit") if r["操作类型"] == "统计报表生成"])
        self.report.run_action(self.report_id, "生成报表", {"采集计划": "TASK-0002"})
        clause = self.audit.archive_clause()
        self.assertEqual(clause["total"], 1)
        self.assertEqual(clause["items"][0]["报表编号"], "REPO-0002")
        self.assertEqual(clause["items"][0]["回写状态"], "已回写")

        records = [r for r in store.rows("audit") if r["操作类型"] == "统计报表生成"]
        self.assertEqual(len(records), before + 1)
        new_record = next(r for r in records if r.get("归档清单编号"))
        self.assertEqual(new_record["采集计划"], "TASK-0002")
        backwrite = self.audit.audit_backwrite(new_record["id"])
        self.assertEqual(backwrite["total"], 1)

    # case 7 ----------------------------------------------------------
    def test_history_backfill_marks_inferred_version(self) -> None:
        # REPO-0003 种子里已生成，声明模板版本 v1，但当时没有快照。
        # setUp 启用模板时会触发一次回填，这里验证回填结果且幂等。
        self.report.snapshot_board()
        snaps = self._snapshots()
        backfilled = next(s for s in snaps if s["报表编号"] == "REPO-0003")
        self.assertEqual(backfilled["冻结来源"], "历史回填")
        self.assertTrue(backfilled["回填标识"])
        self.assertEqual(backfilled["模板版本"], "v1")
        # 回填不重复
        before = len(snaps)
        self.report.snapshot_board()
        self.assertEqual(len(self._snapshots()), before)

    def test_board_groups_by_template_version_and_period_timeline(self) -> None:
        self.report.run_action(self.report_id, "生成报表")
        board = self.report.snapshot_board()
        group = next(
            g for g in board["版本分组"]
            if g["模板编号"] == "TRAN-0002" and g["模板版本"] == "v2"
        )
        self.assertTrue(group["冻结字段"]["映射规则"])
        timeline, _ = self.report.generation_timeline(period="2026-09-08~2026-09-14")
        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]["结果"], "成功")

    def test_report_without_template_cannot_generate(self) -> None:
        report = next(r for r in store.rows("report") if r["报表编号"] == "REPO-0002")
        report["模板编号"] = ""
        entry, message = self.report.run_action(self.report_id, "生成报表")
        self.assertIsNone(entry)
        self.assertIn("请先选择模板", message)
        self.assertEqual(len(self._archives()), 0)

    def test_archive_requires_generated_status(self) -> None:
        # REPO-0001 是草稿，没有快照，不允许直接归档。
        draft_id = self._report_id("REPO-0001")
        entry, message = self.report.run_action(draft_id, "归档报表")
        self.assertIsNone(entry)
        self.assertIn("只有已生成", message)


if __name__ == "__main__":
    unittest.main()
