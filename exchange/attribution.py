from .audit import write
from .lineage import Lineage


class Attributions:
    """材料归属：把原件记录绑定到观察时点有效的景区主体与评价范围。

    归属按材料观察时点解释，不随主体后续合并拆分自动迁移。
    """

    def __init__(self, connection):
        self.connection = connection
        self.lineage = Lineage(connection)

    def bind(self, asset_id, identity_id, scope, observed_at, at, actor):
        asset = self.connection.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if asset is None:
            raise KeyError(asset_id)
        if asset["state"] != "active":
            raise ValueError("记录不可用")
        if self.lineage.get(identity_id) is None:
            raise KeyError(identity_id)
        if not scope or not scope.strip():
            raise ValueError("评价范围缺失")
        if not observed_at:
            raise ValueError("材料观察时点缺失")
        if not self.lineage.is_live(identity_id, observed_at):
            raise ValueError("主体在材料观察时点不存在或已调整")
        existing = self.connection.execute(
            "SELECT 1 FROM attributions WHERE asset_id=? AND identity_id=? AND scope=?",
            (asset_id, identity_id, scope.strip()),
        ).fetchone()
        if existing:
            raise ValueError("归属已存在")
        with self.connection:
            self.connection.execute(
                "INSERT INTO attributions(asset_id,identity_id,scope,observed_at,recorded_at) VALUES(?,?,?,?,?)",
                (asset_id, identity_id, scope.strip(), observed_at, at),
            )
            write(self.connection, at, actor, "attribution.bind", asset_id,
                  {"identity": identity_id, "scope": scope.strip(), "observed_at": observed_at})

    def of_asset(self, asset_id):
        rows = self.connection.execute(
            "SELECT * FROM attributions WHERE asset_id=? ORDER BY identity_id,scope", (asset_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def of_identity(self, identity_id, as_of=None, scope=None):
        sql = "SELECT * FROM attributions WHERE identity_id=?"
        params = [identity_id]
        if as_of is not None:
            sql += " AND observed_at<=?"
            params.append(as_of)
        if scope is not None:
            sql += " AND scope=?"
            params.append(scope)
        sql += " ORDER BY observed_at,asset_id"
        rows = self.connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
