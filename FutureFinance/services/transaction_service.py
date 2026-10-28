import hashlib
import re
from database.db import query, execute, execute_many

def decode_csv_bytes(data):
    """Decode common spreadsheet CSV encodings without replacement characters."""
    if data.startswith((b'\xff\xfe', b'\xfe\xff', b'\x00\x00\xfe\xff', b'\xff\xfe\x00\x00')):
        raise ValueError('This CSV uses UTF-16/UTF-32 encoding. Save it as UTF-8 or CSV (Comma delimited) in Excel and try again.')
    for encoding in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            text=data.decode(encoding,errors='strict')
        except UnicodeDecodeError:
            continue
        # NUL and non-whitespace C0 control bytes generally indicate a binary or
        # unsupported export; do not let them become invisible field content.
        if any(ord(char)<32 and char not in '\t\r\n' for char in text):
            raise ValueError('The file contains binary or unsupported control characters. Export it again as UTF-8 or a Windows CSV file.')
        if encoding=='latin-1' and any('\x80' <= char <= '\x9f' for char in text):
            raise ValueError('The CSV encoding is ambiguous or unsupported. Save the file as UTF-8 or Windows-1252 and try again.')
        return text
    raise ValueError('The CSV encoding is unsupported. Save the file as UTF-8, UTF-8 with BOM, Windows-1252, or Latin-1 and try again.')

def list_transactions(user_id, start=None, end=None, category_id=None, tx_type=None, search=None):
    sql = "SELECT t.transaction_id,t.transaction_date,t.amount,t.transaction_type,t.description,t.payment_method,t.merchant_name,c.name category FROM transactions t JOIN categories c ON c.category_id=t.category_id WHERE t.user_id=:p_user_id"
    p = {"p_user_id": user_id}
    for key, value, clause in [("p_start",start," AND t.transaction_date>=TO_DATE(:p_start,'YYYY-MM-DD')"),("p_end",end," AND t.transaction_date<TO_DATE(:p_end,'YYYY-MM-DD')+1"),("p_category",category_id," AND t.category_id=:p_category"),("p_tx_type",tx_type," AND t.transaction_type=:p_tx_type"),("p_search",search," AND (LOWER(t.description) LIKE '%'||LOWER(:p_search)||'%' OR LOWER(t.merchant_name) LIKE '%'||LOWER(:p_search)||'%')")]:
        if value not in (None, ""):
            sql += clause; p[key] = value
    return query(sql+" ORDER BY t.transaction_date DESC,t.transaction_id DESC", p)

def add_transaction(user_id, form):
    """Insert one validated transaction for the signed-in user."""
    execute(
        """
        INSERT INTO transactions (
            user_id, transaction_date, amount, transaction_type, category_id,
            description, payment_method, merchant_name
        )
        VALUES (
            :p_user_id, TO_DATE(:dt, 'YYYY-MM-DD'), :amount, :typ, :cat,
            :descr, :payment, :merchant
        )
        """,
        {
            "p_user_id": user_id,
            "dt": form["date"],
            "amount": form["amount"],
            "typ": form["type"],
            "cat": form["category_id"],
            "descr": form.get("description"),
            "payment": form.get("payment_method"),
            "merchant": form.get("merchant_name"),
        },
    )

def add_imported_transaction(user_id, row, category_id, digest):
    execute(
        """
        INSERT INTO transactions (
            user_id, transaction_date, amount, transaction_type,
            category_id, description, payment_method, merchant_name, import_hash
        )
        VALUES (
            :u, TO_DATE(:d, 'YYYY-MM-DD'), :a, :t,
            :c, :descr, :pay, :merchant, :hash
        )
        """,
        {
            "u": user_id,
            "d": row["date"],
            "a": row["amount"],
            "t": row["type"].upper(),
            "c": category_id,
            "descr": row.get("description"),
            "pay": row.get("payment_method"),
            "merchant": row.get("merchant_name"),
            "hash": digest,
        },
    )

def import_hash(row):
    raw = "|".join(str(row.get(k, "")).strip().lower() for k in ("date","amount","type","category","description","merchant_name"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def sms_import_hash(message):
    """Stable source-message digest so corrected/repeated SMS imports dedupe."""
    normalized=re.sub(r"\s+"," ",str(message or "")).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def suggest_category(row, categories):
    """Return a user's own category and a plain-language reason, if a rule matches."""
    text = " ".join(str(row.get(key, "") or "") for key in ("merchant_name", "description")).lower()
    rules = (
        (("salary", "payroll", "paycheck", "stipend"), ("salary", "income")),
        (("grocery", "grocer", "restaurant", "cafe", "food", "swiggy", "zomato"), ("food", "grocery", "dining", "restaurant")),
        (("uber", "ola", "metro", "fuel", "petrol", "transport", "bus"), ("transport", "travel")),
        (("rent", "landlord"), ("housing", "rent")),
        (("electric", "water bill", "internet", "utility", "power bill"), ("utilities", "utility")),
        (("pharmacy", "medical", "clinic", "hospital", "health"), ("health", "medical")),
        (("amazon", "shopping", "clothing", "retail"), ("shopping", "retail")),
        (("netflix", "spotify", "movie", "cinema", "entertainment"), ("entertainment", "subscription")),
    )
    tx_type = str(row.get("type", "EXPENSE")).upper()
    for terms, category_terms in rules:
        if any(term in text for term in terms):
            for category in categories:
                if category["category_type"] in ("BOTH", tx_type) and any(term in category["name"].lower() for term in category_terms):
                    return category, f"Matched merchant/description keywords to your {category['name']} category."
    eligible = [c for c in categories if c["category_type"] in ("BOTH", tx_type)]
    preferred = "Other" if tx_type == "EXPENSE" else "Salary"
    fallback = next((c for c in eligible if c["name"].lower() == preferred.lower()), None)
    if fallback:
        return fallback, f"No keyword rule matched; suggested your default {fallback['name']} category. Please review."
    if eligible:
        return eligible[0], f"No keyword rule matched; suggested your first eligible category, {eligible[0]['name']}. Please review."
    return None, "No compatible category exists for this transaction type."


def import_transactions_batch(user_id, rows):
    """Atomically insert rows and skip duplicates already stored or repeated in this batch."""
    if not rows:
        return 0, 0
    digests = list(dict.fromkeys(row["digest"] for row in rows))
    seen = find_existing_import_hashes(user_id, digests)
    inserts = []
    skipped = 0
    for row in rows:
        if row["digest"] in seen:
            skipped += 1
            continue
        seen.add(row["digest"])
        inserts.append({
            "p_user_id": user_id, "p_date": row["date"], "p_amount": row["amount"],
            "p_type": row["type"], "p_category_id": row["category_id"],
            "p_description": row.get("description"), "p_payment_method": row.get("payment_method"),
            "p_merchant_name": row.get("merchant_name"), "p_import_hash": row["digest"],
        })
    if inserts:
        execute_many(
            "INSERT INTO transactions (user_id,transaction_date,amount,transaction_type,category_id,description,payment_method,merchant_name,import_hash) "
            "VALUES (:p_user_id,TO_DATE(:p_date,'YYYY-MM-DD'),:p_amount,:p_type,:p_category_id,:p_description,:p_payment_method,:p_merchant_name,:p_import_hash)",
            inserts,
        )
    return len(inserts), skipped


def find_existing_import_hashes(user_id, digests):
    """Find existing imported rows in chunks under Oracle's 1,000-item IN limit."""
    existing_hashes=set()
    digests=list(dict.fromkeys(digests))
    for offset in range(0,len(digests),900):
        chunk=digests[offset:offset+900]
        binds={"p_user_id":user_id}; placeholders=[]
        for index,digest in enumerate(chunk):
            name=f"p_hash_{index}"; placeholders.append(f":{name}"); binds[name]=digest
        rows=query("SELECT import_hash FROM transactions WHERE user_id=:p_user_id AND import_hash IN ("+",".join(placeholders)+")",binds)
        existing_hashes.update(row["import_hash"] for row in rows)
    return existing_hashes
