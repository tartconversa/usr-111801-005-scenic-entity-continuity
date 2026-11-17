# 景区资料协作台账：景区合并拆分后的身份连续性

服务登记资料来源、字段、原件、使用许可、数据产品与交付凭据。示例主体和记录均为虚构，不连接真实文旅部门。

需要 Python 3.10 或更新版本，不依赖第三方包。

运行项目测试：python -m unittest discover -s tests -v。
检查源码：python -m compileall -q exchange。
运行本地演练：python -m exchange --database demo.db。

exchange/catalog.py 登记来源字段和原件，grants.py 与 policy.py 管理使用边界，products.py 编排资料，delivery.py 出具回执，audit.py 记录操作。

## 景区身份沿革与材料归属

一片旅游区域可能把几个景点合并申报 A 级，也可能因经营调整拆出独立景区。目录不按当前名称汇总材料，而是显式记录主体身份沿革：

- exchange/lineage.py 登记景区主体，记录合并、拆分事件；每个事件必须注明生效日期与来源依据（公告、批文编号）。主体存续区间按有效事件回放校验，同一主体不能被两个事件重复调整。
- exchange/attribution.py 把原件记录绑定到观察时点有效的主体与评价范围；主体在该时点不存在或已调整的，归属被拒绝。
- exchange/query.py 对外查询：按查询时点解析名称指向的当时主体并说明其由来，材料按当时主体和评价范围解释，不随名称自动汇总到新主体。
- 新申请（products.assemble 指定 identity 与 scope）不自动继承旧主体证据：每份外来材料必须逐项声明继承，注明前身主体、依据的沿革事件与评价范围，依据事件必须位于主体沿革链上且未被纠正取代。
- 沿革纠正（lineage.correct）以新事件取代旧事件：引用受影响身份的待发布决定被置为 flagged 并阻断交付，处理（resolve_flags）后方可交付；已公开的决定保留原引用，纠正以 decision_impacts 记录关联，可经 query.decision_trace 追踪完整纠正链。
