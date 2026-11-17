from .attribution import Attributions
from .audit import history
from .catalog import Catalog
from .db import connect
from .delivery import Delivery
from .grants import Grants
from .lineage import Lineage
from .policy import Policies
from .products import Products
from .query import Query


class Exchange:
    def __init__(self, database):
        self.connection = connect(database)
        self.catalog = Catalog(self.connection)
        self.grants = Grants(self.connection)
        self.policies = Policies(self.connection)
        self.products = Products(self.connection)
        self.delivery = Delivery(self.connection)
        self.lineage = Lineage(self.connection)
        self.attributions = Attributions(self.connection)
        self.query = Query(self.connection)

    def history(self, subject):
        return history(self.connection, subject)

    def close(self):
        self.connection.close()
