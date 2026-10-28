"""Conservative parser for bank messages explicitly pasted by the user."""
import re
from datetime import datetime

_CURRENCY_AMOUNT = re.compile(r"(?:₹|\bINR\b|\bRs\.?\s*)\s*([0-9][0-9,]*(?:\.\d{1,2})?)", re.IGNORECASE)
_DATE_PATTERNS = (
    (re.compile(r"\b(\d{4}-\d{1,2}-\d{1,2})\b"), ("%Y-%m-%d",)),
    (re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b"), ("%d-%m-%Y", "%d/%m/%Y", "%d-%m-%y", "%d/%m/%y")),
    (re.compile(r"\b(\d{1,2}[ -][A-Za-z]{3,9}[, -]+\d{4})\b"), ("%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%B-%Y", "%d %b, %Y", "%d %B, %Y")),
    (re.compile(r"\b([A-Za-z]{3,9} \d{1,2},? \d{4})\b"), ("%b %d %Y", "%B %d %Y", "%b %d, %Y", "%B %d, %Y")),
)
_DEBIT = re.compile(r"\b(debited|withdrawn|spent|paid|purchase|sent to|transferred to|payment to)\b|\bdebit(?:ed)?\s+(?:by|with|from|for|of|to)\b", re.IGNORECASE)
_CREDIT = re.compile(r"\b(credited|received|refund(?:ed)?|deposited)\b|\b(?:salary|cash|funds?|interest|dividend)\s+credit\b|\bcredit\s+(?:with|to|into|of)\b", re.IGNORECASE)
_TRANSACTION_INTENT = re.compile(r"\b(transaction|txn|upi|debit|debited|credit|credited|paid|payment|transfer|transferred|withdrawn|deposit(?:ed)?|received|purchase|spent|refund(?:ed)?)\b", re.IGNORECASE)
_NON_TRANSACTION = re.compile(r"\b(otp|one[ -]time password|verification code|do not share|never share|sale|limited time|offer|discount|lucky draw|click here|unsubscribe|pre-approved loan)\b", re.IGNORECASE)
_BALANCE_CONTEXT = re.compile(r"\b(avl|available|balance|bal|limit|minimum due|statement)\b", re.IGNORECASE)
_MERCHANT_STOP = re.compile(r"\b(via|on|ref(?:erence)?|utr|rrn|txn|transaction|upi|a/c|account|acct|card|available|avl|balance|bal|ifsc|for)\b", re.IGNORECASE)


def _parse_date(text):
    found = []
    for pattern, formats in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1).strip()
            for fmt in formats:
                try:
                    parsed = datetime.strptime(value, fmt).date().isoformat()
                    found.append(parsed)
                    break
                except ValueError:
                    continue
    unique = list(dict.fromkeys(found))
    if len(unique) == 1:
        return unique[0], None
    if len(unique) > 1:
        return None, "More than one date was found; choose the transaction date in the preview."
    return None, "No supported transaction date was found; enter the date in the preview."


def _parse_amount(text):
    candidates = []
    for match in _CURRENCY_AMOUNT.finditer(text):
        preceding = text[max(0, match.start() - 35):match.start()]
        # Exclude balances and limits from transaction amount candidates.
        if _BALANCE_CONTEXT.search(preceding):
            continue
        try:
            amount = float(match.group(1).replace(',', ''))
        except ValueError:
            continue
        if amount > 0:
            candidates.append(amount)
    unique = list(dict.fromkeys(candidates))
    if len(unique) == 1:
        return unique[0], None
    if len(unique) > 1:
        return None, "Multiple possible transaction amounts were found; enter the correct amount in the preview."
    return None, "No clear currency amount was found; enter the amount in the preview."


def _parse_direction(text):
    debits = list(_DEBIT.finditer(text))
    credits = list(_CREDIT.finditer(text))
    if debits and credits:
        return None, "Both debit and credit wording appears; choose the direction in the preview."
    if debits:
        return "EXPENSE", None
    if credits:
        return "INCOME", None
    return None, "Transaction direction is unclear; choose debit or credit in the preview."


def _parse_merchant(text, direction):
    patterns = [r"\b(?:to|at)\s+([^,;\n.]{2,100})"]
    if direction == 'INCOME':
        patterns.insert(0, r"\bfrom\s+([^,;\n.]{2,100})")
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        candidate = _MERCHANT_STOP.split(match.group(1), maxsplit=1)[0].strip(" \t:-")
        candidate = re.sub(r"\s+", " ", candidate)
        if len(candidate) >= 2 and not re.match(r"(?i)^(?:your|my|the|a/c|account|acct|card)\b", candidate):
            return candidate[:120]
    return None


def _parse_description(text, merchant):
    match=re.search(r"\b(?:transaction\s+details?|narration|remarks?|description|particulars)\s*[:\-]?\s*([^.;,\n]{2,120})",text,re.IGNORECASE)
    if match:
        value=re.sub(r"\s+"," ",match.group(1)).strip(" \t:-")
        if value and not re.match(r"(?i)^(?:ref(?:erence)?|utr|rrn|available|avl|balance)\b",value):
            return value[:500]
    # A clear payee is also a useful transaction description when the SMS has
    # no separate narration field.
    return merchant


def parse_bank_sms(message):
    """Extract only explicit transaction evidence; flag uncertain text for review."""
    raw = str(message or '').strip()
    if not raw:
        return {'status': 'REJECTED', 'amount': None, 'type': None, 'date': None, 'merchant_name': None, 'description': None, 'issues': ['Message is empty.']}
    if re.search(r"\b(otp|one[ -]time password|verification code|do not share|never share)\b", raw, re.IGNORECASE):
        return {'status': 'REJECTED', 'amount': None, 'type': None, 'date': None, 'merchant_name': None, 'description': None, 'issues': ['OTP or verification messages are not imported.']}
    has_intent = bool(_TRANSACTION_INTENT.search(raw))
    if not has_intent or (_NON_TRANSACTION.search(raw) and not (_DEBIT.search(raw) or _CREDIT.search(raw))):
        return {'status': 'REJECTED', 'amount': None, 'type': None, 'date': None, 'merchant_name': None, 'description': None, 'issues': ['This does not look like a completed bank transaction.']}

    amount, amount_issue = _parse_amount(raw)
    tx_type, direction_issue = _parse_direction(raw)
    tx_date, date_issue = _parse_date(raw)
    merchant = _parse_merchant(raw, tx_type)
    description = _parse_description(raw, merchant)
    issues = [issue for issue in (amount_issue, direction_issue, date_issue) if issue]
    if not merchant and not description:
        issues.append('Merchant or description was not clear; enter one in the preview.')
    return {
        'status': 'READY' if not issues else 'REVIEW',
        'amount': amount,
        'type': tx_type,
        'date': tx_date,
        'merchant_name': merchant,
        'description': description,
        'issues': issues,
    }


def split_sms_messages(text):
    """Split pasted SMS text on blank lines or explicit --- separators."""
    return [part.strip() for part in re.split(r"\n[ \t]*\n|^[ \t]*---+[ \t]*$", str(text or "").strip(), flags=re.MULTILINE) if part.strip()]
