"""Oracle connection and transaction helpers. Oracle is the only supported store."""
import oracledb
from flask import current_app

def connect():
    cfg = current_app.config
    if not cfg.get("ORACLE_USER") or not cfg.get("ORACLE_PASSWORD"):
        raise RuntimeError("Oracle is not configured. Set ORACLE_USER and ORACLE_PASSWORD in .env.")
    kwargs = {"user": cfg["ORACLE_USER"], "password": cfg["ORACLE_PASSWORD"], "dsn": cfg["ORACLE_DSN"]}
    wallet = cfg.get("ORACLE_WALLET_LOCATION")
    if wallet:
        kwargs["config_dir"] = wallet
        kwargs["wallet_location"] = wallet
    try:
        return oracledb.connect(**kwargs)
    except oracledb.Error as exc:
        raise RuntimeError(f"Could not connect to Oracle at {cfg['ORACLE_DSN']}: {exc}") from exc

def _materialize(value):
    """Read Oracle LOBs while their owning connection is still open."""
    return value.read() if isinstance(value, oracledb.LOB) else value

def _close(conn, cursor):
    """Close a cursor before its connection, even if cursor cleanup raises."""
    try:
        if cursor is not None:
            cursor.close()
    finally:
        conn.close()

def _rollback(conn):
    """Attempt rollback without replacing the database error that triggered it."""
    try:
        conn.rollback()
    except oracledb.Error:
        pass

def query(sql, params=None, *, one=False):
    conn = connect()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(sql, params or {})
        names = [d[0].lower() for d in cur.description] if cur.description else []
        rows = [tuple(_materialize(value) for value in row) for row in cur.fetchall()] if cur.description else []
        result = [dict(zip(names, row)) for row in rows]
        return result[0] if one and result else (None if one else result)
    finally:
        _close(conn, cur)

def execute(sql, params=None):
    conn = connect()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(sql, params or {})
        conn.commit()
        return cur.rowcount
    except Exception:
        _rollback(conn)
        raise
    finally:
        _close(conn, cur)

def execute_many(sql, rows):
    conn = connect()
    cur = None
    try:
        cur = conn.cursor()
        cur.executemany(sql, rows)
        conn.commit()
        return cur.rowcount
    except Exception:
        _rollback(conn)
        raise
    finally:
        _close(conn, cur)

def create_user_with_categories(email, password_hash, display_name, categories):
    """Create an account and its starter categories atomically."""
    conn = connect()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users(email,password_hash,display_name) VALUES(:p_email,:p_hash,:p_name)",
            {"p_email":email,"p_hash":password_hash,"p_name":display_name},
        )
        cur.execute("SELECT user_id FROM users WHERE email=:p_email", {"p_email":email})
        user_id = cur.fetchone()[0]
        cur.executemany(
            "INSERT INTO categories(user_id,name,category_type,is_essential) VALUES(:p_user_id,:p_category_name,:p_category_type,:p_essential)",
            [{"p_user_id":user_id,"p_category_name":name,"p_category_type":kind,"p_essential":essential}
             for name,kind,essential in categories],
        )
        conn.commit()
        return user_id
    except Exception:
        _rollback(conn)
        raise
    finally:
        _close(conn, cur)
