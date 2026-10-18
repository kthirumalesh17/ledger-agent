import json, os, re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Load data
DATA_PATH = os.path.join(os.path.dirname(__file__), "orders.json")
with open(DATA_PATH) as f:
    RAW_ORDERS = json.load(f)

# Currency rates
USD_PER = {"USD": 1.0, "EUR": 1.12, "INR": 0.01209}

IST = ZoneInfo("Asia/Kolkata")

def parse_dt(s):
    s = s.strip()
    # Handle +05:30 offset
    try:
        return datetime.fromisoformat(s).astimezone(IST)
    except:
        return None

def to_usd(amount, currency):
    return amount * USD_PER.get(currency, 1.0)

# Deduplicate: keep latest updated_at per order id
def deduplicate(orders):
    latest = {}
    for o in orders:
        oid = o['id']
        if oid not in latest:
            latest[oid] = o
        else:
            existing_dt = parse_dt(latest[oid]['updated_at'])
            new_dt = parse_dt(o['updated_at'])
            if new_dt and existing_dt and new_dt > existing_dt:
                latest[oid] = o
    return list(latest.values())

ORDERS = deduplicate(RAW_ORDERS)

class QuestionRequest(BaseModel):
    question: str

def extract_month_year(question):
    months = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'june': 6, 'july': 7, 'august': 8, 'september': 9,
        'october': 10, 'november': 11, 'december': 12
    }
    q = question.lower()
    found_month = None
    found_year = None
    for name, num in months.items():
        if name in q:
            found_month = num
            break
    year_match = re.search(r'\b(202\d)\b', q)
    if year_match:
        found_year = int(year_match.group(1))
    return found_month, found_year

def answer_question(question):
    q = question.lower()
    
    # Extract filters
    month, year = extract_month_year(question)
    
    # Region
    region = None
    for r in ['north', 'south', 'east', 'west', 'central']:
        if r in q:
            region = r.capitalize()
            break
    
    # Product
    products_list = ['toaster', 'grinder', 'kettle', 'blender', 'mixer', 'rice cooker', 'juicer', 'air fryer']
    product = None
    for p in products_list:
        if p in q:
            product = p.title()
            break
    
    # Customer
    cust_match = re.search(r'c\d+', q)
    customer = cust_match.group(0).upper() if cust_match else None
    
    # Filter orders
    def matches(o):
        if 'refund' in q or 'refunded' in q:
            if o['status'] != 'refunded':
                return False
        elif 'revenue' in q or 'total' in q or 'paid' in q or 'sale' in q or 'sold' in q or 'earn' in q:
            if o['status'] != 'paid':
                return False
        
        if month or year:
            dt = parse_dt(o['created_at'])
            if dt:
                if month and dt.month != month:
                    return False
                if year and dt.year != year:
                    return False
            else:
                return False
        
        if region and o['region'] != region:
            return False
        if product and o['product'] != product:
            return False
        if customer and o['customer'].upper() != customer:
            return False
        
        return True
    
    filtered = [o for o in ORDERS if matches(o)]
    
    # What to compute
    if 'how many' in q and ('order' in q or 'transaction' in q):
        return len(filtered)
    
    if 'how many' in q and 'customer' in q:
        return len(set(o['customer'] for o in filtered))
    
    if 'how many' in q and 'product' in q:
        return len(set(o['product'] for o in filtered))
    
    if 'quantity' in q or 'units sold' in q or 'qty' in q or 'how many units' in q:
        return sum(o['qty'] for o in filtered)
    
    if 'average' in q and 'order' in q:
        if not filtered:
            return 0
        total = sum(to_usd(o['amount'], o.get('currency','USD')) for o in filtered)
        return round(total / len(filtered), 2)
    
    if 'refund' in q:
        total = sum(to_usd(o['amount'], o.get('currency','USD')) for o in filtered)
        return round(total, 2)
    
    # Default: revenue (sum of paid orders in USD)
    total = sum(to_usd(o['amount'], o.get('currency','USD')) for o in filtered)
    return round(total, 2)

@app.post("/")
@app.post("/api")
async def handle(req: QuestionRequest):
    try:
        ans = answer_question(req.question)
        return JSONResponse(content={"answer": ans}, headers={
            "Access-Control-Allow-Origin": "*"
        })
    except Exception as e:
        return JSONResponse(content={"answer": str(e)}, status_code=500)
