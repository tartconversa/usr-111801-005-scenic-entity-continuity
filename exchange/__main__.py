import argparse
import json
from .service import Exchange

def main():
    parser = argparse.ArgumentParser(description="景区资料本地协作演练")
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    service = Exchange(args.database)
    try:
        # 登记景区主体与资料来源
        service.lineage.register("jia", "甲景区", "2026-01-01", "资料室")
        service.lineage.register("yi", "乙景区", "2026-01-01", "资料室")
        service.catalog.register("inspect", "暗访组", "甲地", "review",
                                 [("finding", "服务观察", "text", False)], "2026-01-02")
        service.catalog.ingest("item-jia", "inspect", {"finding": "指引可见"}, "2026-02-10")
        service.catalog.ingest("item-yi", "inspect", {"finding": "泊位不足"}, "2026-02-11")
        # 材料按观察时点归属到当时主体与评价范围
        service.attributions.bind("item-jia", "jia", "A级申报范围", "2026-02-10", "2026-02-12", "资料室")
        service.attributions.bind("item-yi", "yi", "A级申报范围", "2026-02-11", "2026-02-12", "资料室")
        # 合并：甲+乙 → 甲乙景区，生效范围以公告为据
        service.lineage.merge("ev-merge", ["jia", "yi"], [("jiayi", "甲乙景区")],
                              "2026-06-01", "文旅公告2026-15号", "2026-06-02", "资料室")
        # 历史查询仍按当时主体解释
        before = service.query.identity_at("甲景区", "2026-03-01")
        # 新申请：甲乙景区引用前身的旧材料必须逐项声明继承
        service.grants.issue("g-1", "inspect", "复核组", "质量复核", "2026-12-31", "2026-06-03")
        service.policies.set("甲地", "review", "质量复核", "allow", "2026-06-03", "资料室")
        revision = service.products.assemble(
            "dossier", "甲乙景区复核资料", ["item-jia", "item-yi"], "2026-06-05", "资料室",
            identity="jiayi", scope="A级申报范围",
            carryovers=[
                {"asset_id": "item-jia", "from_identity": "jia", "basis_event": "ev-merge", "scope": "A级申报范围"},
                {"asset_id": "item-yi", "from_identity": "yi", "basis_event": "ev-merge", "scope": "A级申报范围"},
            ])
        receipt = service.delivery.send("d-1", "dossier", revision, "复核组", "质量复核", "2026-06-06")
        # 沿革纠正：生效日提前；已公开决定保留原引用，纠正以影响记录追踪
        report = service.lineage.correct("ev-merge", "ev-merge-fix", "文旅公告2026-15号(更正)",
                                         "2026-11-17", "资料室", effective_at="2026-05-01")
        trace = service.query.decision_trace("dossier", revision)
        print(json.dumps({"before": before, "receipt": receipt,
                          "correction": report, "impacts": trace["impacts"]},
                         ensure_ascii=False, sort_keys=True, indent=2))
    finally:
        service.close()

if __name__ == "__main__":
    main()
