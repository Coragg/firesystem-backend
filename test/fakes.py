from types import SimpleNamespace


class FakeQuery:
    """Chainable stand-in for a Supabase query builder; execute() returns canned data.

    data may be a callable. It receives the rpc params, or for table queries the list of
    (method, args, kwargs) calls chained so far, so a test can vary or fail per call.
    """

    def __init__(self, data, error: Exception | None = None, params: dict | None = None, on_execute=None) -> None:
        self._data = data
        self._error = error
        self._params = params
        self._on_execute = on_execute
        self.ops: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def call(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self

        return call

    def execute(self) -> SimpleNamespace:
        if self._on_execute:
            self._on_execute(self.ops)
        if self._error:
            raise self._error
        context = self._params if self._params is not None else self.ops
        data = self._data(context) if callable(self._data) else self._data
        return SimpleNamespace(data=data)


class FakeClient:
    """Stand-in for supabase.Client: tables and rpcs map a name to rows (or a callable).

    calls records rpc use; executed records every table query as (table, ops).
    """

    def __init__(
        self,
        tables: dict | None = None,
        rpcs: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        self.tables = tables or {}
        self.rpcs = rpcs or {}
        self.error = error
        self.calls: list[tuple[str, dict]] = []
        self.executed: list[tuple[str, list]] = []

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(
            self.tables.get(name, []), self.error,
            on_execute=lambda ops: self.executed.append((name, list(ops))),
        )

    def rpc(self, name: str, params: dict) -> FakeQuery:
        self.calls.append((name, params))
        return FakeQuery(self.rpcs.get(name, []), self.error, params)
