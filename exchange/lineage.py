import json

from .audit import write


class Lineage:
    """景区主体身份沿革：登记主体，记录合并、拆分及其纠正。

    每个沿革事件必须注明生效日期与来源依据（公告、批文等），
    纠正以新事件取代旧事件，旧事件保留用于追踪。
    """

    def __init__(self, connection):
        self.connection = connection

    def register(self, identity_id, name, at, actor):
        if not name or not name.strip():
            raise ValueError("主体名称缺失")
        with self.connection:
            self.connection.execute(
                "INSERT INTO identities(id,name,created_at) VALUES(?,?,?)",
                (identity_id, name.strip(), at),
            )
            write(self.connection, at, actor, "identity.register", identity_id, {"name": name})

    def merge(self, event_id, predecessors, successors, effective_at, source_ref, at, actor, note=None):
        return self._record(event_id, "merge", predecessors, successors, effective_at, source_ref, at, actor, note)

    def split(self, event_id, predecessors, successors, effective_at, source_ref, at, actor, note=None):
        return self._record(event_id, "split", predecessors, successors, effective_at, source_ref, at, actor, note)

    def correct(self, event_id, correction_id, source_ref, at, actor,
                predecessors=None, successors=None, effective_at=None, note=None):
        """以新事件纠正已登记的沿革事件，并评估对材料归属与决定的影响。"""
        original = self._event(event_id)
        if original is None:
            raise KeyError(event_id)
        if original["superseded_by"]:
            raise ValueError("该事件已被纠正，应针对最新纠正再次提出")
        members = self.members(event_id)
        if predecessors is None:
            predecessors = members["predecessor"]
        if successors is None:
            successors = []
            for sid in members["successor"]:
                if sid in members["predecessor"]:
                    successors.append(sid)
                else:
                    successors.append((sid, self._identity(sid)["name"]))
        self._record(
            correction_id, original["kind"], predecessors, successors,
            effective_at if effective_at is not None else original["effective_at"],
            source_ref, at, actor, note, supersedes=event_id,
        )
        return self._impacts(event_id, correction_id, at, actor)

    def get(self, identity_id):
        row = self._identity(identity_id)
        return dict(row) if row else None

    def lifetime(self, identity_id):
        """主体存续区间 [start, end)：按当前有效（未被纠正取代）的沿革计算。"""
        row = self._identity(identity_id)
        if row is None:
            raise KeyError(identity_id)
        start = row["created_at"] if row["origin_event"] is None else None
        end = None
        for event in self._events():
            members = self.members(event["id"])
            preds, succs = members["predecessor"], members["successor"]
            if identity_id in succs and identity_id not in preds:
                start = event["effective_at"]
            if identity_id in preds and identity_id not in succs:
                end = event["effective_at"]
                break
        return {"id": identity_id, "name": row["name"], "start": start, "end": end}

    def is_live(self, identity_id, at):
        try:
            span = self.lifetime(identity_id)
        except KeyError:
            return False
        return span["start"] is not None and span["start"] <= at and (span["end"] is None or at < span["end"])

    def ancestors(self, identity_id):
        return self._walk(identity_id, "predecessor", "successor")

    def descendants(self, identity_id):
        return self._walk(identity_id, "successor", "predecessor")

    def members(self, event_id):
        rows = self.connection.execute(
            "SELECT identity_id,role FROM lineage_members WHERE event_id=? ORDER BY identity_id",
            (event_id,),
        ).fetchall()
        out = {"predecessor": [], "successor": []}
        for row in rows:
            out[row["role"]].append(row["identity_id"])
        return out

    def events_for(self, identity_id, include_superseded=False):
        sql = ("SELECT e.* FROM lineage_events e JOIN lineage_members m ON m.event_id=e.id "
               "WHERE m.identity_id=?")
        if not include_superseded:
            sql += " AND e.superseded_by IS NULL"
        sql += " ORDER BY e.effective_at,e.recorded_at,e.id"
        return [self._event_view(dict(row)) for row in self.connection.execute(sql, (identity_id,)).fetchall()]

    def chain(self, event_id):
        """纠正链：从给定事件出发，依次列出取代它的后续纠正事件。"""
        chain = []
        current = self._event(event_id)
        while current is not None:
            chain.append(current["id"])
            current = self._event(current["superseded_by"]) if current["superseded_by"] else None
        return chain

    def _record(self, event_id, kind, predecessors, successors, effective_at, source_ref, at, actor, note, supersedes=None):
        predecessors = list(dict.fromkeys(predecessors or []))
        successors = [self._successor(item) for item in (successors or [])]
        if not source_ref or not source_ref.strip():
            raise ValueError("沿革事件必须注明来源依据")
        if not effective_at:
            raise ValueError("沿革事件必须注明生效日期")
        if not predecessors or not successors:
            raise ValueError("沿革事件缺少前身或后续主体")
        if kind == "merge" and len(predecessors) < 2:
            raise ValueError("合并至少包含两个前身主体")
        if kind == "split" and len(predecessors) != 1:
            raise ValueError("拆分只能有一个前身主体")
        if kind == "split" and len(successors) < 2:
            raise ValueError("拆分至少产生两个后续主体")
        pred_set = set(predecessors)
        seen = set()
        for sid, sname in successors:
            if sid in seen:
                raise ValueError("后续主体重复")
            seen.add(sid)
            if sid in pred_set and sname is not None:
                raise ValueError("延续主体不应重复命名")
            if sid not in pred_set and sname is None:
                raise ValueError("既有主体作为后续时必须同时列为前身")
        if self._event(event_id) is not None:
            raise ValueError("事件编号已存在")
        for pid in predecessors:
            if self._identity(pid) is None:
                raise ValueError("前身主体未登记:" + pid)
        with self.connection:
            if supersedes is not None:
                old = self._event(supersedes)
                if old is None:
                    raise KeyError(supersedes)
                if old["superseded_by"]:
                    raise ValueError("该事件已被纠正")
                self.connection.execute(
                    "UPDATE lineage_events SET superseded_by=? WHERE id=?", (event_id, supersedes)
                )
            for sid, sname in successors:
                if sname is None:
                    continue
                existing = self._identity(sid)
                if existing is None:
                    self.connection.execute(
                        "INSERT INTO identities(id,name,created_at,origin_event) VALUES(?,?,?,?)",
                        (sid, sname.strip(), effective_at, event_id),
                    )
                elif not (supersedes and existing["origin_event"] == supersedes):
                    raise ValueError("后续主体已存在:" + sid)
            self.connection.execute(
                "INSERT INTO lineage_events(id,kind,effective_at,source_ref,recorded_at,actor,supersedes,note) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (event_id, kind, effective_at, source_ref.strip(), at, actor, supersedes, note),
            )
            self.connection.executemany(
                "INSERT INTO lineage_members(event_id,identity_id,role) VALUES(?,?,?)",
                [(event_id, sid, role)
                 for role, ids in (("predecessor", predecessors), ("successor", [s for s, _ in successors]))
                 for sid in ids],
            )
            self._check_graph()
            write(self.connection, at, actor, "lineage." + kind, event_id, {
                "effective_at": effective_at, "source_ref": source_ref,
                "predecessors": predecessors, "successors": [s for s, _ in successors],
                "supersedes": supersedes, "note": note,
            })

    def _impacts(self, event_id, correction_id, at, actor):
        original = self._event(event_id)
        corrected = self._event(correction_id)
        old_members = self.members(event_id)
        new_members = self.members(correction_id)
        identity_change = (
            original["effective_at"] != corrected["effective_at"]
            or set(old_members["predecessor"]) != set(new_members["predecessor"])
            or set(old_members["successor"]) != set(new_members["successor"])
        )
        affected = (set(old_members["predecessor"]) | set(old_members["successor"])
                    | set(new_members["predecessor"]) | set(new_members["successor"]))
        report = {
            "correction": correction_id, "supersedes": event_id,
            "identity_change": identity_change,
            "affected_identities": sorted(affected),
            "misattributions": [], "flagged": [], "published": [],
        }
        if not identity_change:
            return report
        misattributed = {}
        for identity_id in sorted(affected):
            rows = self.connection.execute(
                "SELECT * FROM attributions WHERE identity_id=?", (identity_id,)
            ).fetchall()
            for row in rows:
                if not self.is_live(identity_id, row["observed_at"]):
                    key = (row["asset_id"], identity_id, row["scope"])
                    misattributed[key] = {
                        "asset_id": row["asset_id"], "identity_id": identity_id,
                        "scope": row["scope"], "observed_at": row["observed_at"],
                    }
        report["misattributions"] = sorted(misattributed.values(), key=lambda m: (m["asset_id"], m["identity_id"]))
        reasons = {}

        def add(pid, rev, reason):
            slot = reasons.setdefault((pid, rev), [])
            if reason not in slot:
                slot.append(reason)

        placeholders = ",".join("?" for _ in affected)
        for row in self.connection.execute(
            "SELECT * FROM product_subjects WHERE identity_id IN (" + placeholders + ")", sorted(affected)
        ).fetchall():
            add(row["product_id"], row["product_revision"], {"type": "subject", "identity": row["identity_id"]})
        for mis in report["misattributions"]:
            for row in self.connection.execute(
                "SELECT product_id,product_revision FROM product_inputs WHERE asset_id=?", (mis["asset_id"],)
            ).fetchall():
                add(row["product_id"], row["product_revision"], {"type": "attribution", **mis})
        for row in self.connection.execute("SELECT * FROM carryovers WHERE basis_event=?", (event_id,)).fetchall():
            add(row["product_id"], row["product_revision"],
                {"type": "carryover", "asset_id": row["asset_id"], "basis_event": event_id})
        with self.connection:
            for (pid, rev), product_reasons in sorted(reasons.items()):
                published = self.connection.execute(
                    "SELECT 1 FROM deliveries WHERE product_id=? AND product_revision=? LIMIT 1", (pid, rev)
                ).fetchone()
                state = self.connection.execute(
                    "SELECT state FROM products WHERE id=? AND revision=?", (pid, rev)
                ).fetchone()["state"]
                payload = json.dumps(product_reasons, ensure_ascii=False, sort_keys=True)
                if published:
                    self.connection.execute(
                        "INSERT INTO decision_impacts(product_id,product_revision,correction_event,reason,created_at) "
                        "VALUES(?,?,?,?,?)",
                        (pid, rev, correction_id, payload, at),
                    )
                    report["published"].append({"product_id": pid, "revision": rev, "reasons": product_reasons})
                elif state in {"draft", "flagged"}:
                    self.connection.execute(
                        "INSERT INTO product_flags(product_id,product_revision,correction_event,reason,created_at) "
                        "VALUES(?,?,?,?,?)",
                        (pid, rev, correction_id, payload, at),
                    )
                    if state == "draft":
                        self.connection.execute(
                            "UPDATE products SET state='flagged' WHERE id=? AND revision=?", (pid, rev)
                        )
                    report["flagged"].append({"product_id": pid, "revision": rev, "reasons": product_reasons})
            write(self.connection, at, actor, "lineage.correct", correction_id, {
                "supersedes": event_id,
                "affected_identities": report["affected_identities"],
                "misattributions": len(report["misattributions"]),
                "flagged": [(f["product_id"], f["revision"]) for f in report["flagged"]],
                "published": [(p["product_id"], p["revision"]) for p in report["published"]],
            })
        return report

    def _check_graph(self):
        """回放全部有效沿革事件，校验每个事件在其生效日期上成立。"""
        known = {}
        for row in self.connection.execute("SELECT * FROM identities WHERE origin_event IS NULL").fetchall():
            known[row["id"]] = {"start": row["created_at"], "end": None}
        for event in self._events():
            members = self.members(event["id"])
            preds, succs = members["predecessor"], members["successor"]
            eff = event["effective_at"]
            for pid in preds:
                state = known.get(pid)
                if state is None or state["end"] is not None or state["start"] > eff:
                    raise ValueError("主体%s在%s不存在或已调整" % (pid, eff))
            for sid in succs:
                if sid in preds:
                    continue
                if sid in known:
                    raise ValueError("后续主体%s在%s前已存在" % (sid, eff))
                known[sid] = {"start": eff, "end": None}
            for pid in preds:
                if pid not in succs:
                    known[pid]["end"] = eff

    def _walk(self, identity_id, target, given):
        seen = {identity_id}
        frontier = [identity_id]
        while frontier:
            current = frontier.pop()
            for event in self._events():
                members = self.members(event["id"])
                if current in members[given]:
                    for nxt in members[target]:
                        if nxt not in seen:
                            seen.add(nxt)
                            frontier.append(nxt)
        return seen

    def _events(self):
        return self.connection.execute(
            "SELECT * FROM lineage_events WHERE superseded_by IS NULL ORDER BY effective_at,recorded_at,id"
        ).fetchall()

    def _event(self, event_id):
        return self.connection.execute("SELECT * FROM lineage_events WHERE id=?", (event_id,)).fetchone()

    def _identity(self, identity_id):
        return self.connection.execute("SELECT * FROM identities WHERE id=?", (identity_id,)).fetchone()

    def _event_view(self, event):
        view = dict(event)
        view.update(self.members(event["id"]))
        return view

    @staticmethod
    def _successor(item):
        if isinstance(item, str):
            return (item, None)
        sid, sname = item
        if not sname or not str(sname).strip():
            raise ValueError("新主体缺少名称")
        return (sid, str(sname))
