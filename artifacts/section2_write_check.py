"""Section 2: prove the dbig_reader accounts cannot write. Run it yourself.

Expected: every write is refused. It prints only the error class and the first line of each
refusal, with passwords scrubbed. If a line says "WRITE ACCEPTED", tell me at once.
  - PostgreSQL: the INSERT runs inside a transaction that is always rolled back.
  - ClickHouse: the INSERT selects zero rows, so it cannot add data even if allowed.
  - MongoDB: it tries to insert one probe document into a NEW collection `dbig_write_probe`.
    If that were accepted you would have to delete that collection.
"""
import base64
import json
import os
import re
import urllib.error
import urllib.request

import pg8000.dbapi
import pymongo

pg = json.loads(os.environ["PG_CONNECTION"])
ch = json.loads(os.environ["CLICKHOUSE_CONNECTION"])
uri = json.loads(os.environ["MONGODB_CONNECTION"])["uri"]
secrets = [pg["password"], ch["password"], re.search(r"//[^:]+:([^@]+)@", uri).group(1)]


def scrub(text):
    for s in secrets:
        text = text.replace(s, "***")
    return text.strip().split("\n")[0][:300]


lines = []

try:
    conn = pg8000.dbapi.connect(host=pg["host"], port=pg["port"], database=pg["database"],
                                user=pg["user"], password=pg["password"], timeout=10)
    cur = conn.cursor()
    try:
        cur.execute('INSERT INTO public."person" DEFAULT VALUES')
        lines.append("PostgreSQL: WRITE ACCEPTED (rolled back)")
    except Exception as e:
        lines.append(f"PostgreSQL: refused: {type(e).__name__}: {scrub(str(e))}")
    finally:
        conn.rollback()
        conn.close()
except Exception as e:
    lines.append(f"PostgreSQL: could not connect: {type(e).__name__}: {scrub(str(e))}")

sql = ('INSERT INTO testzoominfo_clone2."Person" SELECT * FROM testzoominfo_clone2."Person" '
       'LIMIT 0')
req = urllib.request.Request(f"http://{ch['host']}:{ch['port']}/", data=sql.encode(), method="POST")
req.add_header("Authorization", "Basic " + base64.b64encode(
    f"{ch['username']}:{ch['password']}".encode()).decode())
try:
    urllib.request.urlopen(req, timeout=20)
    lines.append("ClickHouse: WRITE ACCEPTED (0 rows)")
except urllib.error.HTTPError as e:
    lines.append(f"ClickHouse: refused: HTTP {e.code}: {scrub(e.read().decode())}")
except Exception as e:
    lines.append(f"ClickHouse: could not connect: {type(e).__name__}: {scrub(str(e))}")

try:
    db = pymongo.MongoClient(uri, serverSelectionTimeoutMS=8000)["enrichment_engine"]
    try:
        db["dbig_write_probe"].insert_one({"probe": 1})
        lines.append("MongoDB: WRITE ACCEPTED: delete collection dbig_write_probe")
    except Exception as e:
        lines.append(f"MongoDB: refused: {type(e).__name__}: {scrub(str(e))}")
except Exception as e:
    lines.append(f"MongoDB: could not connect: {type(e).__name__}: {scrub(str(e))}")

text = "\n".join(lines)
print(text)
with open("artifacts/section2-write-refusals.txt", "w", encoding="utf-8") as f:
    f.write(text + "\n")
