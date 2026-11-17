import json

from .lineage import Lineage


class Query:
    """对外查询：按查询时点解释主体身份、评价范围与材料归属。

    历史查询以当时主体解释；材料不随名称自动汇总到新主体，
    跨主体证据只能沿沿革链显式追查。
    """

    def __init__(self, connection):
        self.connection = connection
        self.lineage = Lineage(connection)

    def identity_at(self, name_or_id, as_of):
        """解析某时点下名称或编号指向的主体，并说明其由来。"""
        rows = self.connection.execute(
            "SELECT * FROM identities WHERE id=? OR name=?", (name_or_id, name_or_id)
        ).fetchall()
        candidates = [self.lineage.lifetime(row["id"]) for row in rows]
        for span in candidates:
            if span["start"] is not None and span["start"] <= as_of and (span["end"] is None or as_of < span["end"]):
                return {
                    "found": True, "as_of": as_of, "identity": span,
                    "formed_by": self._formed_by(span["id"], as_of),
                }
        return {"found": False, "as_of": as_of, "candidates": candidates}

    def materials(self, identity_id, as_of=None, scope=None):
        """归属到指定主体的材料，可按观察时点与评价范围过滤。"""
        span = self.lineage.lifetime(identity_id)
        sql = ("SELECT a.asset_id,a.identity_id,a.scope,a.observed_at,a.recorded_at,"
               "t.source_id,t.digest,t.received_at FROM attributions a "
               "JOIN assets t ON t.id=a.asset_id WHERE a.identity_id=?")
        params = [identity_id]
        if as_of is not None:
            sql += " AND a.observed_at<=?"
            params.append(as_of)
        if scope is not None:
            sql += " AND a.scope=?"
            params.append(scope)
        sql += " ORDER BY a.observed_at,a.asset_id"
        rows = self.connection.execute(sql, params).fetchall()
        return {"identity": span, "materials": [dict(row) for row in rows]}

    def lineage_of(self, identity_id, include_superseded=False):
        return self.lineage.events_for(identity_id, include_superseded)

    def decision_trace(self, product_id, revision):
        """决定的完整追踪：原引用保留，后续纠正以影响记录关联。"""
        product = self.connection.execute(
            "SELECT * FROM products WHERE id=? AND revision=?", (product_id, revision)
        ).fetchone()
        if product is None:
            raise KeyError(product_id)
        subject = self.connection.execute(
            "SELECT * FROM product_subjects WHERE product_id=? AND product_revision=?",
            (product_id, revision),
        ).fetchone()
        inputs = []
        for row in self.connection.execute(
            "SELECT asset_id FROM product_inputs WHERE product_id=? AND product_revision=? ORDER BY asset_id",
            (product_id, revision),
        ).fetchall():
            asset_id = row["asset_id"]
            attributions = [dict(r) for r in self.connection.execute(
                "SELECT identity_id,scope,observed_at FROM attributions WHERE asset_id=? ORDER BY identity_id,scope",
                (asset_id,),
            ).fetchall()]
            carryover = self.connection.execute(
                "SELECT from_identity,to_identity,basis_event,scope FROM carryovers "
                "WHERE product_id=? AND product_revision=? AND asset_id=?",
                (product_id, revision, asset_id),
            ).fetchone()
            inputs.append({
                "asset_id": asset_id, "attributions": attributions,
                "carryover": dict(carryover) if carryover else None,
            })
        deliveries = [dict(r) for r in self.connection.execute(
            "SELECT id,recipient,purpose,created_at FROM deliveries "
            "WHERE product_id=? AND product_revision=? ORDER BY created_at",
            (product_id, revision),
        ).fetchall()]
        flags = [{
            "id": r["id"], "correction_event": r["correction_event"],
            "reason": json.loads(r["reason"]), "created_at": r["created_at"],
            "resolved_at": r["resolved_at"], "resolver": r["resolver"], "resolution": r["resolution"],
        } for r in self.connection.execute(
            "SELECT * FROM product_flags WHERE product_id=? AND product_revision=? ORDER BY id",
            (product_id, revision),
        ).fetchall()]
        impacts = [{
            "id": r["id"], "correction_event": r["correction_event"],
            "reason": json.loads(r["reason"]), "created_at": r["created_at"],
            "correction_chain": self.lineage.chain(r["correction_event"]),
        } for r in self.connection.execute(
            "SELECT * FROM decision_impacts WHERE product_id=? AND product_revision=? ORDER BY id",
            (product_id, revision),
        ).fetchall()]
        return {
            "product": dict(product),
            "subject": dict(subject) if subject else None,
            "inputs": inputs,
            "deliveries": deliveries,
            "flags": flags,
            "impacts": impacts,
        }

    def _formed_by(self, identity_id, as_of):
        for event in self.lineage.events_for(identity_id):
            if (identity_id in event["successor"] and identity_id not in event["predecessor"]
                    and event["effective_at"] <= as_of):
                return {
                    "event": event["id"], "kind": event["kind"],
                    "effective_at": event["effective_at"], "source_ref": event["source_ref"],
                    "predecessors": event["predecessor"],
                }
        return None
