import json, os, re, sqlite3
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Currency rates
USD_PER = {"USD": 1.0, "EUR": 1.12, "INR": 0.01209}

# Load JSONL data and build SQLite DB
DATA_PATH = os.path.join(os.path.dirname(__file__), "orders.jsonl")

db = sqlite3.connect(":memory:", check_same_thread=False)

def init_db():
    rows = []
    with open(DATA_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    # Deduplicate - keep latest updated_at per id
    latest = {}
    for r in rows:
        oid = r['id']
        if oid not in latest or r['updated_at'] > latest[oid]['updated_at']:
            latest[oid] = r
    rows = list(latest.values())

    db.execute("""CREATE TABLE orders (
        id TEXT, customer TEXT, region TEXT, product TEXT,
        qty INTEGER, unit_price REAL, amount REAL, currency TEXT,
        status TEXT, created_at TEXT, updated_at TEXT,
        amount_usd REAL, created_month TEXT, created_year INTEGER
    )""")

    for r in rows:
        rate = USD_PER.get(r.get('currency', 'USD'), 1.0)
        amount_usd = r['amount'] * rate
        # Parse date in IST
        ca = r['created_at']
        # Convert to IST: handle +05:30 or Z
        from datetime import datetime, timezone, timedelta
        IST = timezone(timedelta(hours=5, minutes=30))
        try:
            if ca.endswith('Z'):
                dt = datetime.fromisoformat(ca.replace('Z', '+00:00')).astimezone(IST)
            else:
                dt = datetime.fromisoformat(ca).astimezone(IST)
            created_month = dt.strftime('%Y-%m')
            created_year = dt.year
        except:
            created_month = ca[:7]
            created_year = int(ca[:4])

        db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            r['id'], r['customer'], r['region'], r['product'],
            r['qty'], r['unit_price'], r['amount'], r.get('currency','USD'),
            r['status'], r['created_at'], r['updated_at'],
            amount_usd, created_month, created_year
        ))
    db.commit()
    print(f"Loaded {len(rows)} deduplicated orders into SQLite")

init_db()

MONTH_MAP = {
    'january':'01','february':'02','march':'03','april':'04',
    'may':'05','june':'06','july':'07','august':'08',
    'september':'09','october':'10','november':'11','december':'12',
    'jan':'01','feb':'02','mar':'03','apr':'04',
    'jun':'06','jul':'07','aug':'08','sep':'09','oct':'10','nov':'11','dec':'12'
}
PRODUCTS = ['Air Fryer','Rice Cooker','Juicer','Toaster','Mixer','Kettle','Blender','Grinder']
REGIONS = ['North','South','East','West','Central']

def parse_to_sql(question):
    q = question.lower()
    conditions = []

    # Status
    if re.search(r'\brefund(ed|s)?\b', q):
        conditions.append("status = 'refunded'")
    elif re.search(r'\b(void|voided|cancel)\b', q):
        conditions.append("status = 'void'")
    else:
        conditions.append("status = 'paid'")

    # Region
    for reg in REGIONS:
        if re.search(rf'\b{reg.lower()}\b', q):
            conditions.append(f"region = '{reg}'")
            break

    # Product
    for prod in PRODUCTS:
        if re.search(rf'\b{re.escape(prod.lower())}s?\b', q):
            conditions.append(f"product = '{prod}'")
            break

    # Customer
    cm = re.search(r'\b(c\d{4})\b', q)
    if cm:
        conditions.append(f"customer = '{cm.group(1).upper()}'")

    # Date
    ym = re.search(r'\b(202\d)\b', q)
    year_str = ym.group(1) if ym else None

    month_num = None
    for m_name, m_val in MONTH_MAP.items():
        if re.search(rf'\b{m_name}\b', q):
            month_num = m_val
            break

    if month_num and year_str:
        conditions.append(f"created_month = '{year_str}-{month_num}'")
    elif month_num:
        conditions.append(f"substr(created_month, 6, 2) = '{month_num}'")
    elif year_str:
        conditions.append(f"created_year = {year_str}")

    where = ' AND '.join(conditions) if conditions else '1=1'

    # Intent
    if re.search(r'\b(which|top|best).*(product)\b', q) or re.search(r'\bproduct.*(most|top|highest)\b', q):
        metric = 'SUM(qty)' if re.search(r'\b(qty|quantity|units)\b', q) else 'SUM(amount_usd)'
        return f"SELECT product FROM orders WHERE {where} GROUP BY product ORDER BY {metric} DESC LIMIT 1", 'string'

    if re.search(r'\b(which|top|best).*(customer)\b', q) or re.search(r'\bcustomer.*(most|top|highest)\b', q):
        return f"SELECT customer FROM orders WHERE {where} GROUP BY customer ORDER BY SUM(amount_usd) DESC LIMIT 1", 'string'

    if re.search(r'\b(which|top|best).*(region)\b', q) or re.search(r'\bregion.*(most|top|highest)\b', q):
        return f"SELECT region FROM orders WHERE {where} GROUP BY region ORDER BY SUM(amount_usd) DESC LIMIT 1", 'string'

    if re.search(r'\b(distinct|unique|different|how many).*(customer)\b', q):
        return f"SELECT COUNT(DISTINCT customer) FROM orders WHERE {where}", 'int'

    if re.search(r'\baverage\b', q):
        return f"SELECT ROUND(COALESCE(AVG(amount_usd),0),2) FROM orders WHERE {where}", 'money'

    if re.search(r'\b(how many orders|number of orders|count.*orders)\b', q):
        return f"SELECT COUNT(*) FROM orders WHERE {where}", 'int'

    if re.search(r'\b(how many units|total quantity|total units|units sold)\b', q):
        return f"SELECT COALESCE(SUM(qty),0) FROM orders WHERE {where}", 'int'

    # Default: revenue
    return f"SELECT ROUND(COALESCE(SUM(amount_usd),0),2) FROM orders WHERE {where}", 'money'

class Req(BaseModel):
    question: str

def get_answer(question: str):
    sql, typ = parse_to_sql(question)
    print(f"Q: {question}\nSQL: {sql}")
    cur = db.execute(sql)
    row = cur.fetchone()
    val = row[0] if row and row[0] is not None else 0
    if typ == 'string':
        result = str(val)
    elif typ == 'int':
        result = int(val)
    else:
        result = round(float(val), 2)
    print(f"A: {result}")
    return result

@app.post("/")
@app.post("/api")
async def answer(request: Request):
    from fastapi import Request as Req2
    body = await request.json()

    # MCP JSON-RPC format
    if "jsonrpc" in body:
        method = body.get("method", "")
        req_id = body.get("id", 1)

        if method == "initialize":
            return {
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "ledger-agent", "version": "1.0.0"}
                }
            }

        if method == "tools/list":
            return {
                "jsonrpc": "2.0", "id": req_id,
                "result": {"tools": [
                    {
                        "name": "answer_question",
                        "description": "Answer questions about Acme Appliances order ledger revenue, refunds, products, customers",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"question": {"type": "string", "description": "Natural language question about the order ledger"}},
                            "required": ["question"]
                        }
                    },
                    {
                        "name": "query_ledger",
                        "description": "Query the Acme Appliances order ledger",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"question": {"type": "string"}},
                            "required": ["question"]
                        }
                    }
                ]}
            }

        if method == "tools/call":
            params = body.get("params", {})
            args = params.get("arguments", params.get("input", params.get("kwargs", {})))
            question = args.get("question", args.get("query", args.get("text", "")))
            result = get_answer(question)
            return {
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": str(result)}],
                    "isError": False
                }
            }

        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}}

    # Plain REST format
    question = body.get("question", "")
    result = get_answer(question)
    return {"answer": result}
