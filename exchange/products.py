from .audit import write
from .lineage import Lineage


class Products:
    def __init__(self, connection):
        self.connection = connection
        self.lineage = Lineage(connection)

    def assemble(self, product_id, name, asset_ids, at, actor, identity=None, scope=None, carryovers=None):
        asset_ids = list(dict.fromkeys(asset_ids))
        if not asset_ids:
            raise ValueError("数据产品缺少输入")
        carryovers = carryovers or []
        if identity is None and (scope or carryovers):
            raise ValueError("未指定申请主体，不能声明评价范围或继承")
        placeholders = ",".join("?" for _ in asset_ids)
        found = self.connection.execute(
            "SELECT count(*) FROM assets WHERE id IN (" + placeholders + ") AND state='active'",
            asset_ids,
        ).fetchone()[0]
        if found != len(asset_ids):
            raise ValueError("输入记录不可用")
        declarations = []
        if identity is not None:
            if not scope or not scope.strip():
                raise ValueError("申请需注明评价范围")
            if self.lineage.get(identity) is None:
                raise KeyError(identity)
            if not self.lineage.is_live(identity, at):
                raise ValueError("申请主体在该时点不存在或已调整")
            declarations = self._check_inputs(identity, asset_ids, carryovers)
        revision = self.connection.execute("SELECT COALESCE(MAX(revision),0)+1 FROM products WHERE id=?", (product_id,)).fetchone()[0]
        with self.connection:
            self.connection.execute("INSERT INTO products(id,revision,name,created_at) VALUES(?,?,?,?)", (product_id, revision, name, at))
            self.connection.executemany(
                "INSERT INTO product_inputs(product_id,product_revision,asset_id) VALUES(?,?,?)",
                [(product_id, revision, asset_id) for asset_id in asset_ids],
            )
            if identity is not None:
                self.connection.execute(
                    "INSERT INTO product_subjects(product_id,product_revision,identity_id,scope) VALUES(?,?,?,?)",
                    (product_id, revision, identity, scope.strip()),
                )
                for decl in declarations:
                    self.connection.execute(
                        "INSERT INTO carryovers(id,product_id,product_revision,asset_id,from_identity,to_identity,basis_event,scope,declared_at,actor) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        ("%s:%s:%s" % (product_id, revision, decl["asset_id"]), product_id, revision,
                         decl["asset_id"], decl["from_identity"], identity, decl["basis_event"], decl["scope"], at, actor),
                    )
            write(self.connection, at, actor, "product.assemble", product_id, {
                "revision": revision, "inputs": asset_ids, "identity": identity, "scope": scope,
                "carryovers": [decl["asset_id"] for decl in declarations],
            })
        return revision

    def inputs(self, product_id, revision):
        rows = self.connection.execute(
            "SELECT a.*,s.owner,s.jurisdiction,s.classification,s.active AS source_active FROM product_inputs p "
            "JOIN assets a ON a.id=p.asset_id JOIN sources s ON s.id=a.source_id "
            "WHERE p.product_id=? AND p.product_revision=? ORDER BY a.id",
            (product_id, revision),
        ).fetchall()
        return [dict(row) for row in rows]

    def subject(self, product_id, revision):
        row = self.connection.execute(
            "SELECT * FROM product_subjects WHERE product_id=? AND product_revision=?",
            (product_id, revision),
        ).fetchone()
        return dict(row) if row else None

    def carryovers_of(self, product_id, revision):
        rows = self.connection.execute(
            "SELECT * FROM carryovers WHERE product_id=? AND product_revision=? ORDER BY asset_id",
            (product_id, revision),
        ).fetchall()
        return [dict(row) for row in rows]

    def flags(self, product_id=None, open_only=False):
        sql = "SELECT * FROM product_flags"
        clauses, params = [], []
        if product_id is not None:
            clauses.append("product_id=?")
            params.append(product_id)
        if open_only:
            clauses.append("resolved_at IS NULL")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id"
        return [dict(row) for row in self.connection.execute(sql, params).fetchall()]

    def resolve_flags(self, product_id, revision, at, actor, note):
        if not note or not note.strip():
            raise ValueError("处理说明缺失")
        open_flags = self.connection.execute(
            "SELECT id FROM product_flags WHERE product_id=? AND product_revision=? AND resolved_at IS NULL",
            (product_id, revision),
        ).fetchall()
        if not open_flags:
            raise ValueError("没有待处理的沿革提示")
        with self.connection:
            self.connection.execute(
                "UPDATE product_flags SET resolved_at=?,resolver=?,resolution=? "
                "WHERE product_id=? AND product_revision=? AND resolved_at IS NULL",
                (at, actor, note.strip(), product_id, revision),
            )
            self.connection.execute(
                "UPDATE products SET state='draft' WHERE id=? AND revision=? AND state='flagged'",
                (product_id, revision),
            )
            write(self.connection, at, actor, "product.resolve_flags", product_id,
                  {"revision": revision, "flags": len(open_flags), "note": note.strip()})

    def _check_inputs(self, identity, asset_ids, carryovers):
        by_asset = {}
        for item in carryovers:
            aid = item.get("asset_id")
            if aid not in asset_ids:
                raise ValueError("继承声明指向未引用的材料:" + str(aid))
            if aid in by_asset:
                raise ValueError("材料继承声明重复:" + aid)
            by_asset[aid] = item
        declarations = []
        missing = []
        for aid in asset_ids:
            holders = {row[0] for row in self.connection.execute(
                "SELECT identity_id FROM attributions WHERE asset_id=?", (aid,)
            )}
            if identity in holders:
                continue
            item = by_asset.get(aid)
            if item is None:
                missing.append(aid)
                continue
            frm = item.get("from_identity")
            if not frm or frm not in holders:
                raise ValueError("材料%s未归属到声明的前身主体" % aid)
            self._check_basis(item.get("basis_event"), frm, identity)
            inherit_scope = (item.get("scope") or "").strip()
            if not inherit_scope:
                raise ValueError("继承声明需注明评价范围")
            declarations.append({
                "asset_id": aid, "from_identity": frm,
                "basis_event": item["basis_event"], "scope": inherit_scope,
            })
        if missing:
            raise ValueError("材料未归属本次申请主体且未声明继承:" + ",".join(missing))
        return declarations

    def _check_basis(self, basis_event, from_identity, to_identity):
        if not basis_event:
            raise ValueError("继承声明缺少沿革依据事件")
        event = self.connection.execute("SELECT * FROM lineage_events WHERE id=?", (basis_event,)).fetchone()
        if event is None:
            raise ValueError("继承依据事件不存在:" + str(basis_event))
        if event["superseded_by"] is not None:
            raise ValueError("继承依据事件已被纠正取代:" + basis_event)
        members = self.lineage.members(basis_event)
        ancestors = set()
        for predecessor in members["predecessor"]:
            ancestors |= self.lineage.ancestors(predecessor)
        descendants = set()
        for successor in members["successor"]:
            descendants |= self.lineage.descendants(successor)
        if from_identity not in ancestors or to_identity not in descendants:
            raise ValueError("继承依据事件不在主体沿革链上:" + basis_event)
