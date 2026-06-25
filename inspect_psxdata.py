# inspect_psxdata.py
# Stage 1: see exactly what psxdata.fundamentals() returns
import psxdata
import pandas as pd

pd.set_option("display.max_rows", 200)
pd.set_option("display.max_columns", 50)
pd.set_option("display.width", 200)

SYM = "LUCK"

print("="*70)
print(f"fundamentals('{SYM}')")
print("="*70)
f = psxdata.fundamentals(SYM)

print("\nTYPE:", type(f))

# It might be a dict of DataFrames, a single DataFrame, or something else.
if isinstance(f, dict):
    print("\nKEYS:", list(f.keys()))
    for k, v in f.items():
        print("\n" + "-"*70)
        print(f"SECTION: {k}   (type={type(v)})")
        print("-"*70)
        if isinstance(v, pd.DataFrame):
            print("COLUMNS:", list(v.columns))
            print("SHAPE:", v.shape)
            print(v.head(30).to_string())
        else:
            print(repr(v)[:2000])
elif isinstance(f, pd.DataFrame):
    print("\nCOLUMNS:", list(f.columns))
    print("SHAPE:", f.shape)
    print(f.head(50).to_string())
else:
    print("\nRAW REPR:")
    print(repr(f)[:3000])

# Also peek at a live quote — that's where current price / PE often live
print("\n" + "="*70)
print(f"quote('{SYM}')")
print("="*70)
try:
    q = psxdata.quote(SYM)
    print("TYPE:", type(q))
    print(q)
except Exception as e:
    print("quote failed:", e)
