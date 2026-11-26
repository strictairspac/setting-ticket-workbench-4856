"""纯标准库测试基座：用最小桩件替代 sqlalchemy / fastapi。

生产代码只用到了 SQLAlchemy 构造器和少量查询形态，这里以内存表实现相同语义，
使服务层的状态机、行锁路径（单连接下等价）与幂等逻辑可在无第三方依赖时验证。
"""

import sys
import types
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
STUBS = Path(__file__).resolve().parent / "_stubs"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(STUBS))


class Cond:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class ColumnRef:
    def __init__(self, table, name):
        self.table = table
        self.name = name

    def __eq__(self, other):
        return Cond(self.name, other)

    def __hash__(self):
        return hash((self.table, self.name))


class Column:
    def __init__(self, name, *args, **kwargs):
        self.name = name


class _Columns:
    def __init__(self, table):
        self._table = table

    def __getattr__(self, name):
        return ColumnRef(self._table.name, name)


class Table:
    def __init__(self, name, metadata, *columns):
        self.name = name
        self.columns = columns
        self.c = _Columns(self)
        metadata.tables[name] = self


class MetaData:
    def __init__(self):
        self.tables = {}


class _Statement:
    def __init__(self, table):
        self.table = table
        self.conds = []
        self.value_map = {}

    def where(self, cond):
        self.conds.append(cond)
        return self

    def values(self, **kwargs):
        self.value_map = kwargs
        return self


class Select(_Statement):
    def __init__(self, table, projections=()):
        super().__init__(table)
        self.projections = projections

    def with_for_update(self, **_kwargs):
        return self

    def order_by(self, *_args):
        return self

    def limit(self, *_args):
        return self


class Insert(_Statement):
    pass


class Update(_Statement):
    pass


def select(*targets):
    first = targets[0]
    if isinstance(first, Table):
        return Select(first)
    # 标量投影，如 select(runs.c.status)
    return Select(ColumnRefTable(first), (first.name,))


class ColumnRefTable:
    def __init__(self, ref):
        self.name = ref.table


def insert(table):
    return Insert(table)


def update(table):
    return Update(table)


class FakeResult:
    def __init__(self, rows, projections=()):
        self._rows = rows
        self._projections = projections
        self.inserted_primary_key = [rows[0]["id"]] if rows and "id" in rows[0] else []

    def mappings(self):
        return self

    def first(self):
        return dict(self._rows[0]) if self._rows else None

    def __iter__(self):
        for row in self._rows:
            yield dict(row)

    def scalars(self):
        return self

    def scalar_one(self):
        return self._rows[0][self._projections[0]]

    def scalar(self):
        return self._rows[0][self._projections[0]] if self._rows else None


class FakeDB:
    def __init__(self):
        self.data = {}
        self.sequences = {}

    def create_table(self, name):
        self.data.setdefault(name, [])
        self.sequences.setdefault(name, 0)

    def _matches(self, row, conds):
        return all(row.get(cond.key) == cond.value for cond in conds)

    def execute(self, statement):
        if isinstance(statement, Insert):
            rows = self.data.setdefault(statement.table.name, [])
            row = dict(statement.value_map)
            has_id_column = any(col.name == "id" for col in statement.table.columns)
            if "id" not in row and has_id_column:
                self.sequences[statement.table.name] = self.sequences.get(statement.table.name, 0) + 1
                row["id"] = self.sequences[statement.table.name]
            rows.append(row)
            return FakeResult([row])
        if isinstance(statement, Update):
            changed = []
            for row in self.data[statement.table.name]:
                if self._matches(row, statement.conds):
                    row.update(statement.value_map)
                    changed.append(row)
            return FakeResult(changed)
        rows = [dict(row) for row in self.data[statement.table.name]
                if self._matches(row, statement.conds)]
        if statement.projections:
            rows = [{statement.projections[0]: row.get(statement.projections[0])} for row in rows]
        return FakeResult(rows, statement.projections)

    def begin(self):
        return self

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def install_stubs():
    sa = types.ModuleType("sqlalchemy")
    sa.MetaData = MetaData
    sa.Table = Table
    sa.Column = Column
    sa.Select = Select
    sa.Insert = Insert
    sa.Update = Update
    sa.select = select
    sa.insert = insert
    sa.update = update
    for name in ("JSON", "BigInteger", "Integer", "String"):
        setattr(sa, name, type(name, (), {"__init__": lambda self, *a, **k: None}))
    sa.ForeignKey = lambda *a, **k: None
    sys.modules["sqlalchemy"] = sa

    fastapi = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code, detail):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    fastapi.HTTPException = HTTPException
    sys.modules["fastapi"] = fastapi


def fresh_db():
    """按真实模型建表并返回可操作的内存数据库。"""
    install_stubs()
    from src.models import audit as audit_model, cases, repairs as repair_model, reviews, runs  # noqa: F401
    from src.models.base import metadata

    db = FakeDB()
    for name in metadata.tables:
        db.create_table(name)
    return db
