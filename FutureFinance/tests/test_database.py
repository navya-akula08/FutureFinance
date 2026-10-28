from database import db


class FakeCursor:
    def __init__(self, fail_categories=False):
        self.calls=[]
        self.fail_categories=fail_categories
        self.closed=False
        self.rowcount=1

    def execute(self, sql, params):
        self.calls.append((sql,params))

    def fetchone(self):
        return (42,)

    def executemany(self, sql, rows):
        self.calls.append((sql,rows))
        if self.fail_categories:
            raise RuntimeError('category insert failed')

    def close(self):
        self.closed=True


class FakeConnection:
    def __init__(self, fail_categories=False):
        self.fake_cursor=FakeCursor(fail_categories)
        self.committed=False
        self.rolled_back=False
        self.closed=False

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.committed=True

    def rollback(self):
        self.rolled_back=True

    def close(self):
        self.closed=True


def test_user_and_default_categories_commit_together(monkeypatch):
    connection=FakeConnection()
    monkeypatch.setattr(db,'connect',lambda:connection)
    user_id=db.create_user_with_categories('user@example.test','hashed','User',[('Food','EXPENSE','Y')])
    assert user_id==42
    assert connection.committed and not connection.rolled_back and connection.closed
    assert connection.fake_cursor.closed
    assert connection.fake_cursor.calls[0][1]['p_email']=='user@example.test'
    assert connection.fake_cursor.calls[2][1][0]['p_user_id']==42


def test_user_creation_rolls_back_if_starter_category_insert_fails(monkeypatch):
    connection=FakeConnection(fail_categories=True)
    monkeypatch.setattr(db,'connect',lambda:connection)
    import pytest
    with pytest.raises(RuntimeError,match='category insert failed'):
        db.create_user_with_categories('user@example.test','hashed','User',[('Food','EXPENSE','Y')])
    assert not connection.committed and connection.rolled_back and connection.closed
    assert connection.fake_cursor.closed


def test_query_materializes_lobs_before_releasing_connection(monkeypatch):
    class FakeLob:
        def __init__(self, cursor, connection):
            self.cursor=cursor
            self.connection=connection
        def read(self):
            assert not self.cursor.closed and not self.connection.closed
            return '{"evidence":"loaded"}'

    class LobCursor(FakeCursor):
        def __init__(self, connection):
            super().__init__()
            self.connection=connection
            self.description=[('SUPPORTING_DATA',)]
        def fetchall(self):
            return [(FakeLob(self,self.connection),)]

    class LobConnection(FakeConnection):
        def __init__(self):
            super().__init__()
            self.fake_cursor=LobCursor(self)

    connection=LobConnection()
    monkeypatch.setattr(db,'connect',lambda:connection)
    monkeypatch.setattr(db.oracledb,'LOB',FakeLob)
    result=db.query('SELECT supporting_data FROM recommendations',one=True)
    assert result=={'supporting_data':'{"evidence":"loaded"}'}
    assert connection.fake_cursor.closed and connection.closed


def test_execute_helpers_close_cursors_and_connections(monkeypatch):
    connections=[FakeConnection(),FakeConnection()]
    remaining=list(connections)
    monkeypatch.setattr(db,'connect',lambda:remaining.pop(0))
    db.execute('UPDATE t SET value=:v',{'v':1})
    db.execute_many('INSERT INTO t(value) VALUES(:v)',[{'v':1}])
    assert not remaining
    assert all(conn.closed and conn.fake_cursor.closed for conn in connections)
