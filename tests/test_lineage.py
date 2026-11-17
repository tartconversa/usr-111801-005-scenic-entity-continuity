import tempfile
import unittest
from pathlib import Path

from exchange import Exchange


class LineageFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Exchange(Path(self.temp.name) / "exchange.db")
        svc = self.service
        svc.lineage.register("jia", "甲景区", "2026-01-01", "资料室")
        svc.lineage.register("yi", "乙景区", "2026-01-01", "资料室")
        svc.catalog.register("inspect", "暗访组", "甲地", "review",
                             [("finding", "服务观察", "text", False)], "2026-01-02")
        svc.catalog.ingest("item-jia", "inspect", {"finding": "指引可见"}, "2026-02-10")
        svc.catalog.ingest("item-yi", "inspect", {"finding": "泊位不足"}, "2026-02-11")
        svc.attributions.bind("item-jia", "jia", "A级申报范围", "2026-02-10", "2026-02-12", "资料室")
        svc.attributions.bind("item-yi", "yi", "A级申报范围", "2026-02-11", "2026-02-12", "资料室")
        svc.grants.issue("g-1", "inspect", "复核组", "质量复核", "2026-12-31", "2026-01-03")
        svc.policies.set("甲地", "review", "质量复核", "allow", "2026-01-03", "资料室")

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def merge(self, effective="2026-06-01"):
        self.service.lineage.merge("ev-merge", ["jia", "yi"], [("jiayi", "甲乙景区")],
                                   effective, "文旅公告2026-15号", "2026-06-02", "资料室")

    def assemble_dossier(self, assets, carryovers, at="2026-06-05"):
        return self.service.products.assemble(
            "dossier", "甲乙景区复核资料", assets, at, "资料室",
            identity="jiayi", scope="A级申报范围", carryovers=carryovers)

    def test_event_requires_source_ref(self):
        with self.assertRaises(ValueError):
            self.service.lineage.merge("ev-x", ["jia", "yi"], [("jiayi", "甲乙景区")],
                                       "2026-06-01", "", "2026-06-02", "资料室")
        with self.assertRaises(ValueError):
            self.service.lineage.split("ev-y", ["jia"], ["jia", ("new", "新景区")],
                                       "2026-06-01", None, "2026-06-02", "资料室")

    def test_event_shape_validation(self):
        with self.assertRaises(ValueError):
            self.service.lineage.merge("ev-x", ["jia"], [("jiayi", "甲乙景区")],
                                       "2026-06-01", "公告1号", "2026-06-02", "资料室")
        with self.assertRaises(ValueError):
            self.service.lineage.split("ev-y", ["jia", "yi"], [("a", "甲"), ("b", "乙")],
                                       "2026-06-01", "公告2号", "2026-06-02", "资料室")
        with self.assertRaises(ValueError):
            self.service.lineage.merge("ev-z", ["jia", "ghost"], [("jiayi", "甲乙景区")],
                                       "2026-06-01", "公告3号", "2026-06-02", "资料室")

    def test_attribution_follows_lineage(self):
        self.merge()
        self.service.catalog.ingest("item-late", "inspect", {"finding": "七月观察"}, "2026-07-01")
        with self.assertRaises(ValueError):
            self.service.attributions.bind("item-late", "jia", "A级申报范围", "2026-07-01", "2026-07-02", "资料室")
        with self.assertRaises(ValueError):
            self.service.attributions.bind("item-late", "jiayi", "A级申报范围", "2026-03-01", "2026-07-02", "资料室")
        self.service.attributions.bind("item-late", "jiayi", "A级申报范围", "2026-07-01", "2026-07-02", "资料室")
        with self.assertRaises(ValueError):
            self.service.attributions.bind("item-late", "jiayi", "A级申报范围", "2026-07-01", "2026-07-03", "资料室")

    def test_query_uses_then_subject(self):
        self.merge()
        before = self.service.query.identity_at("甲景区", "2026-03-01")
        self.assertTrue(before["found"])
        self.assertEqual(before["identity"]["id"], "jia")
        after = self.service.query.identity_at("甲景区", "2026-07-01")
        self.assertFalse(after["found"])
        self.assertEqual(after["candidates"][0]["end"], "2026-06-01")
        merged = self.service.query.identity_at("甲乙景区", "2026-07-01")
        self.assertTrue(merged["found"])
        self.assertEqual(merged["formed_by"]["source_ref"], "文旅公告2026-15号")
        self.assertEqual(set(merged["formed_by"]["predecessors"]), {"jia", "yi"})
        self.assertEqual(self.service.query.materials("jiayi")["materials"], [])
        jia_materials = self.service.query.materials("jia")["materials"]
        self.assertEqual([m["asset_id"] for m in jia_materials], ["item-jia"])
        self.assertEqual(jia_materials[0]["scope"], "A级申报范围")

    def test_new_application_requires_explicit_carryover(self):
        self.merge()
        with self.assertRaises(ValueError):
            self.assemble_dossier(["item-jia"], [])
        self.service.lineage.register("wai", "外围景区", "2026-01-01", "资料室")
        self.service.lineage.register("bin", "滨湖景区", "2026-01-01", "资料室")
        self.service.lineage.merge("ev-other", ["wai", "bin"], [("waibin", "外滨景区")],
                                   "2026-06-01", "文旅公告2026-16号", "2026-06-02", "资料室")
        with self.assertRaises(ValueError):
            self.assemble_dossier(["item-jia"], [
                {"asset_id": "item-jia", "from_identity": "jia", "basis_event": "ev-other", "scope": "A级申报范围"}])
        with self.assertRaises(ValueError):
            self.assemble_dossier(["item-jia"], [
                {"asset_id": "item-jia", "from_identity": "yi", "basis_event": "ev-merge", "scope": "A级申报范围"}])
        revision = self.assemble_dossier(["item-jia", "item-yi"], [
            {"asset_id": "item-jia", "from_identity": "jia", "basis_event": "ev-merge", "scope": "A级申报范围"},
            {"asset_id": "item-yi", "from_identity": "yi", "basis_event": "ev-merge", "scope": "A级申报范围"}])
        subject = self.service.products.subject("dossier", revision)
        self.assertEqual(subject["identity_id"], "jiayi")
        declared = self.service.products.carryovers_of("dossier", revision)
        self.assertEqual(len(declared), 2)
        self.assertEqual({row["basis_event"] for row in declared}, {"ev-merge"})

    def test_own_materials_need_no_carryover(self):
        self.merge()
        self.service.catalog.ingest("item-new", "inspect", {"finding": "合并后观察"}, "2026-07-01")
        self.service.attributions.bind("item-new", "jiayi", "A级申报范围", "2026-07-01", "2026-07-02", "资料室")
        revision = self.assemble_dossier(["item-new"], [])
        self.assertEqual(self.service.products.carryovers_of("dossier", revision), [])

    def test_correction_flags_draft_and_blocks_delivery(self):
        self.merge()
        self.service.catalog.ingest("item-may", "inspect", {"finding": "五月观察"}, "2026-05-10")
        self.service.attributions.bind("item-may", "jia", "A级申报范围", "2026-05-10", "2026-05-11", "资料室")
        revision = self.assemble_dossier(["item-may"], [
            {"asset_id": "item-may", "from_identity": "jia", "basis_event": "ev-merge", "scope": "A级申报范围"}])
        report = self.service.lineage.correct("ev-merge", "ev-merge-fix", "文旅公告2026-15号(更正)",
                                              "2026-11-01", "资料室", effective_at="2026-05-01")
        self.assertTrue(report["identity_change"])
        self.assertEqual([m["asset_id"] for m in report["misattributions"]], ["item-may"])
        self.assertEqual([(f["product_id"], f["revision"]) for f in report["flagged"]], [("dossier", revision)])
        self.assertEqual(report["published"], [])
        with self.assertRaises(ValueError):
            self.service.delivery.send("d-1", "dossier", revision, "复核组", "质量复核", "2026-11-02")
        self.service.products.resolve_flags("dossier", revision, "2026-11-03", "资料室", "复核后确认沿用")
        receipt = self.service.delivery.send("d-1", "dossier", revision, "复核组", "质量复核", "2026-11-04")
        self.assertEqual(receipt["product_revision"], revision)
        self.assertEqual(self.service.products.flags("dossier", open_only=True), [])

    def test_correction_keeps_published_decision_traceable(self):
        self.merge()
        self.service.catalog.ingest("item-may", "inspect", {"finding": "五月观察"}, "2026-05-10")
        self.service.attributions.bind("item-may", "jia", "A级申报范围", "2026-05-10", "2026-05-11", "资料室")
        revision = self.assemble_dossier(["item-may"], [
            {"asset_id": "item-may", "from_identity": "jia", "basis_event": "ev-merge", "scope": "A级申报范围"}])
        self.service.delivery.send("d-1", "dossier", revision, "复核组", "质量复核", "2026-06-06")
        report = self.service.lineage.correct("ev-merge", "ev-merge-fix", "文旅公告2026-15号(更正)",
                                              "2026-11-01", "资料室", effective_at="2026-05-01")
        self.assertEqual(report["flagged"], [])
        self.assertEqual([(p["product_id"], p["revision"]) for p in report["published"]], [("dossier", revision)])
        trace = self.service.query.decision_trace("dossier", revision)
        self.assertEqual(trace["inputs"][0]["attributions"][0]["identity_id"], "jia")
        self.assertEqual(trace["inputs"][0]["carryover"]["basis_event"], "ev-merge")
        self.assertEqual(len(trace["deliveries"]), 1)
        self.assertEqual(trace["flags"], [])
        self.assertEqual(len(trace["impacts"]), 1)
        self.assertEqual(trace["impacts"][0]["correction_event"], "ev-merge-fix")
        self.assertEqual(trace["impacts"][0]["correction_chain"], ["ev-merge-fix"])

    def test_correction_without_identity_change_is_quiet(self):
        self.merge()
        revision = self.assemble_dossier(["item-jia"], [
            {"asset_id": "item-jia", "from_identity": "jia", "basis_event": "ev-merge", "scope": "A级申报范围"}])
        report = self.service.lineage.correct("ev-merge", "ev-merge-fix", "文旅公告2026-15号(更正)",
                                              "2026-11-01", "资料室")
        self.assertFalse(report["identity_change"])
        self.assertEqual(report["flagged"], [])
        self.assertEqual(report["published"], [])
        self.assertEqual(self.service.products.flags("dossier"), [])
        receipt = self.service.delivery.send("d-1", "dossier", revision, "复核组", "质量复核", "2026-11-02")
        self.assertEqual(receipt["product_revision"], revision)

    def test_stale_basis_rejected_after_correction(self):
        self.merge()
        self.service.lineage.correct("ev-merge", "ev-merge-fix", "文旅公告2026-15号(更正)",
                                     "2026-11-01", "资料室", effective_at="2026-05-15")
        with self.assertRaises(ValueError):
            self.service.lineage.correct("ev-merge", "ev-merge-fix2", "公告", "2026-11-02", "资料室")
        with self.assertRaises(ValueError):
            self.assemble_dossier(["item-jia"], [
                {"asset_id": "item-jia", "from_identity": "jia", "basis_event": "ev-merge", "scope": "A级申报范围"}],
                at="2026-11-03")
        revision = self.assemble_dossier(["item-jia"], [
            {"asset_id": "item-jia", "from_identity": "jia", "basis_event": "ev-merge-fix", "scope": "A级申报范围"}],
            at="2026-11-03")
        self.assertEqual(self.service.products.subject("dossier", revision)["identity_id"], "jiayi")

    def test_split_creates_independent_scenic(self):
        self.service.lineage.register("region", "青云旅游区", "2026-01-01", "资料室")
        self.service.catalog.ingest("item-region", "inspect", {"finding": "全区观察"}, "2026-03-01")
        self.service.attributions.bind("item-region", "region", "A级申报范围", "2026-03-01", "2026-03-02", "资料室")
        self.service.lineage.split("ev-split", ["region"], ["region", ("lake", "青云湖景区")],
                                   "2026-08-01", "文旅公告2026-30号", "2026-08-02", "资料室")
        self.assertTrue(self.service.lineage.is_live("region", "2026-09-01"))
        self.assertTrue(self.service.lineage.is_live("lake", "2026-08-01"))
        self.assertFalse(self.service.lineage.is_live("lake", "2026-07-31"))
        self.service.catalog.ingest("item-lake", "inspect", {"finding": "湖面观察"}, "2026-07-15")
        with self.assertRaises(ValueError):
            self.service.attributions.bind("item-lake", "lake", "A级申报范围", "2026-07-15", "2026-07-16", "资料室")
        self.service.attributions.bind("item-lake", "lake", "A级申报范围", "2026-08-15", "2026-08-16", "资料室")
        region_view = self.service.query.identity_at("青云旅游区", "2026-09-01")
        self.assertTrue(region_view["found"])
        lake_view = self.service.query.identity_at("青云湖景区", "2026-09-01")
        self.assertEqual(lake_view["formed_by"]["event"], "ev-split")

    def test_split_off_application_inherits_from_region(self):
        self.service.lineage.register("region", "青云旅游区", "2026-01-01", "资料室")
        self.service.catalog.ingest("item-region", "inspect", {"finding": "全区观察"}, "2026-03-01")
        self.service.attributions.bind("item-region", "region", "A级申报范围", "2026-03-01", "2026-03-02", "资料室")
        self.service.lineage.split("ev-split", ["region"], ["region", ("lake", "青云湖景区")],
                                   "2026-08-01", "文旅公告2026-30号", "2026-08-02", "资料室")
        with self.assertRaises(ValueError):
            self.service.products.assemble("lake-dossier", "青云湖景区复核资料", ["item-region"],
                                           "2026-09-01", "资料室", identity="lake", scope="A级申报范围")
        revision = self.service.products.assemble(
            "lake-dossier", "青云湖景区复核资料", ["item-region"], "2026-09-01", "资料室",
            identity="lake", scope="A级申报范围",
            carryovers=[{"asset_id": "item-region", "from_identity": "region",
                         "basis_event": "ev-split", "scope": "A级申报范围"}])
        declared = self.service.products.carryovers_of("lake-dossier", revision)
        self.assertEqual(declared[0]["from_identity"], "region")
        self.assertEqual(declared[0]["to_identity"], "lake")


if __name__ == "__main__":
    unittest.main()
