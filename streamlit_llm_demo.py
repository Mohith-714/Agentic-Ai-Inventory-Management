import streamlit as st
from groq import Groq
import os
from dotenv import load_dotenv
import json
from datetime import datetime, timedelta

# Optional LangChain imports
try:
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_groq import ChatGroq
    LANGCHAIN_AVAILABLE = True
except Exception:
    LANGCHAIN_AVAILABLE = False

# DuckDuckGo Search (ddgs) optional import
try:
    from ddgs import DDGS  # preferred
    _DDG_AVAILABLE = True
    _DDG_SOURCE = "ddgs"
except Exception:
    try:
        from duckduckgo_search import DDGS  # legacy fallback
        _DDG_AVAILABLE = True
        _DDG_SOURCE = "duckduckgo_search"
    except Exception:
        _DDG_AVAILABLE = False
        _DDG_SOURCE = None

def load_inventory(path: str = "inventory.json") -> list:
    """Load inventory from JSON; return [] on error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "items" in data:
            return data.get("items", [])
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []

def summarize_inventory(items: list, limit: int = 50) -> str:
    """Return a compact, LLM-friendly summary of inventory."""
    lines = []
    for idx, item in enumerate(items[:limit], 1):
        if not isinstance(item, dict):
            continue
        name = item.get("product", "Unknown")
        stock = item.get("stock", "?")
        price = item.get("price", "?")
        loc = item.get("location", "?")
        lines.append(f"{idx}. {name} | stock={stock} | price={price} | location={loc}")
    if len(items) > limit:
        lines.append(f"... (+{len(items) - limit} more)")
    return "\n".join(lines) if lines else "No inventory loaded."

def load_purchase_history(path: str = "purchase_history.json") -> list:
    """Load purchase history; return [] on error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def summarize_history_by_product(history: list, lookback_days: int = 90) -> dict:
    """Compute per-product stats: total units, avg daily sales, last-30/7 units."""
    cutoff = datetime.now() - timedelta(days=lookback_days)
    last30 = datetime.now() - timedelta(days=30)
    last7 = datetime.now() - timedelta(days=7)
    per = {}
    for rec in history or []:
        if not isinstance(rec, dict):
            continue
        p = rec.get("product")
        q = float(rec.get("quantity", 0) or 0)
        try:
            d = datetime.fromisoformat(str(rec.get("date")))
        except Exception:
            continue
        if d < cutoff:
            continue
        if p not in per:
            per[p] = {"units": 0.0, "days": lookback_days, "last30": 0.0, "last7": 0.0}
        per[p]["units"] += q
        if d >= last30:
            per[p]["last30"] += q
        if d >= last7:
            per[p]["last7"] += q
    # finalize
    out = {}
    for p, data in per.items():
        avg_daily = data["units"] / max(1, data["days"])
        out[p] = {
            "total_units": round(data["units"], 2),
            "avg_daily_sales": round(avg_daily, 3),
            "last_30_units": round(data["last30"], 2),
            "last_7_units": round(data["last7"], 2),
        }
    return out

def heuristic_demand_level(stats: dict) -> str:
    """Simple fallback classification for demand level."""
    avg = float(stats.get("avg_daily_sales", 0) or 0)
    l30 = float(stats.get("last_30_units", 0) or 0)
    l7 = float(stats.get("last_7_units", 0) or 0)
    # growth proxy: last_7 vs (last_30/4)
    growth = l7 >= (l30 / 4.0) * 1.1 if l30 > 0 else l7 > 0
    if avg >= 1.0 or (avg >= 0.6 and growth):
        return "high"
    if avg >= 0.25:
        return "medium"
    return "low"


def ddgs_region_code(region_label: str) -> str:
    """Map human region labels to DDG region codes.
    Common examples: 'India' -> 'in-en', 'United States' -> 'us-en', 'United Kingdom' -> 'uk-en'.
    """
    label = (region_label or "").strip().lower()
    mapping = {
        "india": "in-en",
        "in": "in-en",
        "united states": "us-en",
        "usa": "us-en",
        "us": "us-en",
        "united kingdom": "uk-en",
        "uk": "uk-en",
        "germany": "de-de",
        "france": "fr-fr",
        "spain": "es-es",
        "italy": "it-it",
        "australia": "au-en",
    }
    return mapping.get(label, "in-en")


def ddgs_realtime_demand(product: str, region_label: str, max_results: int = 25) -> dict:
    """Fetch realtime demand signals via DDGS for a product in a region.
    Returns {results: [...], signals: {high_hits, low_hits, keywords}}
    """
    out = {"results": [], "signals": {"high_hits": 0, "low_hits": 0, "keywords": []}}
    if not _DDG_AVAILABLE or not product:
        return out
    region_code = ddgs_region_code(region_label)
    queries = [
        f"{product} demand {region_label}",
        f"{product} trending {region_label}",
        f"buy {product} in {region_label}",
        f"{product} sold out {region_label}",
        f"{product} shortage {region_label}",
        f"{product} clearance {region_label}",
        f"{product} discount {region_label}",
    ]
    high_kw = ["trending", "sold out", "shortage", "high demand", "popular", "hot", "peak"]
    low_kw = ["clearance", "discount", "overstock", "slow", "low demand"]
    seen = set()
    high_hits = 0
    low_hits = 0
    keywords_seen = set()
    try:
        with DDGS() as ddg:
            for q in queries:
                for r in ddg.text(q, region=region_code, safesearch="off"):
                    href = r.get("href")
                    if href and href in seen:
                        continue
                    seen.add(href)
                    title = r.get("title", "") or ""
                    body = r.get("body", "") or ""
                    s = f"{title}\n{body}".lower()
                    # signal counts
                    if any(k in s for k in high_kw):
                        high_hits += 1
                        for k in high_kw:
                            if k in s:
                                keywords_seen.add(k)
                    if any(k in s for k in low_kw):
                        low_hits += 1
                        for k in low_kw:
                            if k in s:
                                keywords_seen.add(k)
                    out["results"].append({
                        "query": q,
                        "title": r.get("title"),
                        "url": r.get("href"),
                        "snippet": r.get("body"),
                    })
                    if len(out["results"]) >= max_results:
                        break
                if len(out["results"]) >= max_results:
                    break
    except Exception:
        # silent fallback
        pass
    out["signals"]["high_hits"] = high_hits
    out["signals"]["low_hits"] = low_hits
    out["signals"]["keywords"] = sorted(list(keywords_seen))
    return out


def restock_heuristic(stock: int, demand_level: str, threshold: int = 15,
                      min_batch: int = 10, max_batch: int = 250) -> dict:
    """Heuristic restock/hold decision based on stock and demand."""
    dl = (demand_level or "medium").lower()
    # Demand multipliers for target stock
    target_base = 60
    demand_factor_map = {"high": 1.5, "medium": 1.0, "low": 0.7}
    target = target_base * demand_factor_map.get(dl, 1.0)
    decision = "RESTOCK" if (stock or 0) < threshold else "HOLD"
    qty = 0
    if decision == "RESTOCK":
        needed = target - (stock or 0)
        qty = int(max(min_batch, min(max_batch, needed))) if needed > 0 else min_batch
    return {"decision": decision, "suggested_qty": qty}

# Load environment variables
load_dotenv()

# Configure page
st.set_page_config(page_title="GROQ LLM Demo", layout="centered")
st.title("🚀 GROQ LLM Text Input")

# Get GROQ API key and model from .env
groq_api_key = os.getenv("GROQ_API_KEY")
groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

if groq_api_key:
    # Initialize Groq client
    client = Groq(api_key=groq_api_key)
    
    # Load inventory once
    inventory_items = load_inventory()
    inventory_summary = summarize_inventory(inventory_items)
    total_products = len(inventory_items)
    total_stock = sum((i.get("stock", 0) or 0) for i in inventory_items if isinstance(i, dict))

    # Load purchase history and summary
    purchase_history = load_purchase_history()
    history_summary = summarize_history_by_product(purchase_history, lookback_days=90)
    
    # Display model info
    st.sidebar.info(f"📌 Model: {groq_model}")
    with st.sidebar.expander("📦 Inventory Preview", expanded=False):
        st.caption(f"Items: {total_products} | Units: {total_stock}")
        st.text_area("Inventory (read-only)", inventory_summary, height=200, disabled=True)
    use_inventory = st.sidebar.toggle("Include inventory context", value=True, help="Send inventory summary with your prompt so the LLM can answer availability questions.")
    
    # LangChain availability
    if not LANGCHAIN_AVAILABLE:
        st.sidebar.warning("LangChain not installed. For demand analysis: pip install langchain-core langchain-groq")
    
    # Main input area
    st.subheader("Ask anything:")
    user_input = st.text_area("Enter your question or prompt:", height=100)
    
    # Submit button
    if st.button("Generate Response", type="primary"):
        if user_input.strip():
            with st.spinner("Generating response..."):
                try:
                    messages = []
                    if use_inventory:
                        messages.append({
                            "role": "system",
                            "content": (
                                "You are an inventory assistant. Use the provided inventory to answer questions. "
                                "If asked about availability, list items with stock and location. "
                                "Inventory data:\n" + inventory_summary
                            ),
                        })
                    messages.append({"role": "user", "content": user_input})

                    message = client.chat.completions.create(
                        messages=messages,
                        model=groq_model,
                    )
                    
                    st.subheader("Response:")
                    st.write(message.choices[0].message.content)
                except Exception as e:
                    st.error(f"Error: {str(e)}")
        else:
            st.warning("Please enter some text first!")

    st.divider()
    st.header("📈 Demand Analysis")
    st.caption("Analyzes purchase history to classify demand for each product.")
    col_a, col_b = st.columns([1, 1])
    with col_a:
        st.write(f"History records: {len(purchase_history)} | Products with history: {len(history_summary)}")
    with col_b:
        st.write("LangChain:", "✅ Available" if LANGCHAIN_AVAILABLE else "❌ Not Installed")

    analyze = st.button("Analyze Demand", type="primary")
    if analyze:
        if not purchase_history:
            st.warning("No purchase_history.json data found.")
        else:
            if LANGCHAIN_AVAILABLE:
                try:
                    # Build compact JSON summary for prompt
                    summary_json = json.dumps(history_summary, ensure_ascii=False)
                    template = ChatPromptTemplate.from_messages([
                        ("system", "You are a retail analytics expert. Classify demand for each product as high, medium, or low based on the provided metrics. Be precise and consistent."),
                        ("human", (
                            "Given the following per-product metrics over ~90 days, label demand for each product as one of: high, medium, low.\n"
                            "Return STRICT JSON with an array under the key 'demand': each item {{product, demand_level, rationale}}.\n\n"
                            "Metrics per product (JSON):\n{metrics}"
                        )),
                    ])
                    llm = ChatGroq(model=groq_model, temperature=0.1)
                    chain = template | llm
                    result = chain.invoke({"metrics": summary_json})
                    content = getattr(result, "content", str(result))
                    # Try to parse JSON
                    parsed = None
                    try:
                        parsed = json.loads(content)
                    except Exception:
                        # attempt to extract JSON-like block
                        import re
                        m = re.search(r"\{[\s\S]*\}$", content.strip())
                        if m:
                            try:
                                parsed = json.loads(m.group(0))
                            except Exception:
                                parsed = None
                    if parsed and isinstance(parsed, dict) and "demand" in parsed:
                        rows = parsed["demand"]
                        if isinstance(rows, list) and rows:
                            st.success("Demand classification (LangChain)")
                            st.dataframe(rows, width='stretch')
                        else:
                            st.info("No demand rows returned. Showing heuristic fallback.")
                            parsed = None
                    if not parsed:
                        # Fallback to heuristic
                        out = []
                        for p, stats in history_summary.items():
                            out.append({
                                "product": p,
                                "demand_level": heuristic_demand_level(stats),
                                "rationale": f"avg_daily={stats.get('avg_daily_sales')}, last30={stats.get('last_30_units')}, last7={stats.get('last_7_units')}"
                            })
                        st.warning("Showing heuristic demand classification (fallback)")
                        st.dataframe(out, width='stretch')
                except Exception as e:
                    st.error(f"LangChain error: {e}")
            else:
                # Deterministic fallback if LangChain not installed
                out = []
                for p, stats in history_summary.items():
                    out.append({
                        "product": p,
                        "demand_level": heuristic_demand_level(stats),
                        "rationale": f"avg_daily={stats.get('avg_daily_sales')}, last30={stats.get('last_30_units')}, last7={stats.get('last_7_units')}"
                    })
                st.info("LangChain not available; using heuristic fallback.")
                st.dataframe(out, width='stretch')

    # Realtime DDGS-based demand signals by region
    st.divider()
    st.header("🌍 Realtime Demand (DDGS)")
    st.caption("Fetch live web signals for each product in a specific region and classify demand.")
    region_label = st.text_input("Region (e.g., 'India', 'United States')", value="India")
    max_products = st.slider("Products to analyze (from inventory)", min_value=1, max_value=max(1, total_products), value=min(10, total_products))
    run_ddgs = st.button("Fetch Realtime Demand", type="primary")
    if run_ddgs:
        if not _DDG_AVAILABLE:
            st.warning("DDGS is not installed. Install 'ddgs' or 'duckduckgo_search' to enable realtime signals.")
        else:
            products_to_check = [i.get("product") for i in inventory_items if isinstance(i, dict)][:max_products]
            ddgs_rows = []
            ddgs_metrics = {}
            with st.spinner(f"Fetching realtime signals for {len(products_to_check)} products in {region_label}..."):
                for p in products_to_check:
                    bundle = ddgs_realtime_demand(p, region_label, max_results=20)
                    sig = bundle.get("signals", {})
                    high_hits = sig.get("high_hits", 0)
                    low_hits = sig.get("low_hits", 0)
                    keywords = sig.get("keywords", [])
                    # Heuristic demand from signals
                    demand_level = (
                        "high" if high_hits >= 3 and high_hits > low_hits else
                        "medium" if high_hits >= 1 else
                        "low"
                    )
                    ddgs_rows.append({
                        "product": p,
                        "region": region_label,
                        "signals_high": high_hits,
                        "signals_low": low_hits,
                        "keywords": ", ".join(keywords),
                        "demand_level": demand_level,
                    })
                    ddgs_metrics[p] = {
                        "signals_high": high_hits,
                        "signals_low": low_hits,
                        "keywords": keywords,
                    }
            # Display heuristic table
            st.success("Realtime signals collected")
            st.dataframe(ddgs_rows, width='stretch')

            # Optional LangChain classification using DDGS metrics
            if LANGCHAIN_AVAILABLE:
                try:
                    st.caption("Classifying demand via LangChain (using DDGS metrics)")
                    metrics_json = json.dumps(ddgs_metrics, ensure_ascii=False)
                    template = ChatPromptTemplate.from_messages([
                        ("system", "You are a retail analytics expert. Given web signals per product (high/low hits and keywords), classify demand level (high/medium/low). Be precise and consistent."),
                        ("human", (
                            "Region: {region}\n\nSignals per product (JSON):\n{metrics}\n\n"
                            "Return STRICT JSON with key 'demand': array of items {{product, demand_level, rationale}}."
                        )),
                    ])
                    llm = ChatGroq(model=groq_model, temperature=0.1)
                    chain = template | llm
                    result = chain.invoke({"region": region_label, "metrics": metrics_json})
                    content = getattr(result, "content", str(result))
                    parsed = None
                    try:
                        parsed = json.loads(content)
                    except Exception:
                        import re
                        m = re.search(r"\{[\s\S]*\}$", content.strip())
                        if m:
                            try:
                                parsed = json.loads(m.group(0))
                            except Exception:
                                parsed = None
                    if parsed and isinstance(parsed, dict) and "demand" in parsed:
                        rows = parsed["demand"]
                        if isinstance(rows, list) and rows:
                            st.success("DDGS-based demand classification (LangChain)")
                            st.dataframe(rows, width='stretch')
                        else:
                            st.info("No classification rows returned.")
                    else:
                        st.info("Could not parse LangChain output. Showing heuristic results above.")
                except Exception as e:
                    st.error(f"LangChain classification error: {e}")
            else:
                st.caption("LangChain not available; using heuristic results above.")

    # LLM-driven Restock Decisions
    st.divider()
    st.header("🧠 Restock Decisions (LLM)")
    st.caption("Decide RESTOCK or HOLD per product using demand (DDGS or history).")
    source = st.radio("Demand Source", options=["DDGS", "History"], index=0, horizontal=True)
    threshold = st.slider("Restock threshold (units)", min_value=1, max_value=50, value=15)
    min_batch = st.slider("Min restock batch", min_value=1, max_value=100, value=10)
    max_batch = st.slider("Max restock batch", min_value=50, max_value=500, value=250)
    products_for_decision = st.slider("Products to consider", min_value=1, max_value=max(1, total_products), value=min(10, total_products))
    region_for_ddgs = st.text_input("Region for DDGS (if DDGS selected)", value="India")
    run_restock = st.button("Compute Restock Decisions", type="primary")

    if run_restock:
        # Build demand map {product: demand_level}
        demand_map = {}
        items_list = []
        selected_products = [i for i in inventory_items[:products_for_decision] if isinstance(i, dict)]
        if source == "DDGS" and _DDG_AVAILABLE:
            with st.spinner(f"Collecting DDGS signals for {len(selected_products)} products in {region_for_ddgs}…"):
                for item in selected_products:
                    p = item.get("product")
                    bundle = ddgs_realtime_demand(p, region_for_ddgs, max_results=20)
                    sig = bundle.get("signals", {})
                    high_hits = sig.get("high_hits", 0)
                    low_hits = sig.get("low_hits", 0)
                    demand_level = (
                        "high" if high_hits >= 3 and high_hits > low_hits else
                        "medium" if high_hits >= 1 else
                        "low"
                    )
                    demand_map[p] = {"demand_level": demand_level, "signals": sig}
        elif source == "DDGS" and not _DDG_AVAILABLE:
            st.warning("DDGS not available. Switch to 'History' or install DDGS.")
        else:
            # History-based demand
            for item in selected_products:
                p = item.get("product")
                stats = history_summary.get(p, {"avg_daily_sales": 0, "last_30_units": 0, "last_7_units": 0})
                demand_level = heuristic_demand_level(stats)
                demand_map[p] = {"demand_level": demand_level, "history": stats}

        # Prepare items payload for LLM: product, stock, price, demand_level
        for item in selected_products:
            pl = item.get("product")
            dl = demand_map.get(pl, {}).get("demand_level", "medium")
            items_list.append({
                "product": pl,
                "stock": item.get("stock", 0),
                "price": item.get("price", 0),
                "location": item.get("location", "N/A"),
                "demand_level": dl,
            })

        # Ask LLM for RESTOCK/HOLD and quantity
        decisions_rows = []
        try:
            if LANGCHAIN_AVAILABLE:
                template = ChatPromptTemplate.from_messages([
                    ("system", "You are a supply planning assistant. Given inventory items with stock and demand levels, decide RESTOCK or HOLD and suggest quantity if RESTOCK. Be concise and consistent."),
                    ("human", (
                        "Threshold: {threshold}; MinBatch: {min_batch}; MaxBatch: {max_batch}.\n"
                        "Items (JSON):\n{items}\n\n"
                        "Return STRICT JSON with key 'plan': array of items {{product, decision, suggested_qty, rationale}}."
                    )),
                ])
                llm = ChatGroq(model=groq_model, temperature=0.1)
                chain = template | llm
                result = chain.invoke({
                    "threshold": threshold,
                    "min_batch": min_batch,
                    "max_batch": max_batch,
                    "items": json.dumps(items_list, ensure_ascii=False),
                })
                content = getattr(result, "content", str(result))
                parsed = None
                try:
                    parsed = json.loads(content)
                except Exception:
                    import re
                    m = re.search(r"\{[\s\S]*\}$", content.strip())
                    if m:
                        try:
                            parsed = json.loads(m.group(0))
                        except Exception:
                            parsed = None
                if parsed and isinstance(parsed, dict) and "plan" in parsed:
                    decisions_rows = parsed["plan"]
                else:
                    st.info("Could not parse LLM output. Falling back to heuristic decisions.")
            else:
                st.caption("LangChain not available; using heuristic decisions.")
        except Exception as e:
            st.error(f"LLM error: {e}")

        if not decisions_rows:
            # Heuristic fallback
            for it in items_list:
                h = restock_heuristic(it.get("stock", 0), it.get("demand_level"), threshold, min_batch, max_batch)
                decisions_rows.append({
                    "product": it.get("product"),
                    "decision": h["decision"],
                    "suggested_qty": h["suggested_qty"],
                    "rationale": f"stock={it.get('stock')} demand={it.get('demand_level')} threshold={threshold}"
                })

        st.success("Restock decisions ready")
        st.dataframe(decisions_rows, width='stretch')

    # ===== AUTO DEMAND AGENT (no manual input) =====
    st.divider()
    st.header("🤖 Auto Demand Agent")
    st.caption("Automatically infers demand per product (DDGS + history) and decides RESTOCK/HOLD without manual input.")
    auto_products = min(10, total_products)
    auto_threshold = 15
    auto_min_batch = 10
    auto_max_batch = 250
    auto_region_default = "India"

    # Auto compute demand per product
    auto_demand_rows = []
    auto_demand_map = {}
    selected_auto = [i for i in inventory_items[:auto_products] if isinstance(i, dict)]
    with st.spinner(f"Auto-detecting demand for {len(selected_auto)} products…"):
        for item in selected_auto:
            prod = item.get("product")
            loc = item.get("location") or auto_region_default
            # DDGS signals
            sig_bundle = ddgs_realtime_demand(prod, loc, max_results=20) if _DDG_AVAILABLE else {"signals": {"high_hits": 0, "low_hits": 0, "keywords": []}}
            sig = sig_bundle.get("signals", {})
            high_hits = sig.get("high_hits", 0)
            low_hits = sig.get("low_hits", 0)
            hist = history_summary.get(prod, {"avg_daily_sales": 0, "last_30_units": 0, "last_7_units": 0})
            # Heuristic demand blend
            demand_level = (
                "high" if (high_hits >= 3 and high_hits > low_hits) or (hist.get("avg_daily_sales", 0) >= 1.0) else
                "medium" if (high_hits >= 1) or (hist.get("avg_daily_sales", 0) >= 0.25) else
                "low"
            )
            auto_demand_map[prod] = {"demand_level": demand_level, "signals": sig, "history": hist, "location": loc}
            auto_demand_rows.append({
                "product": prod,
                "location": loc,
                "signals_high": high_hits,
                "signals_low": low_hits,
                "avg_daily_sales": hist.get("avg_daily_sales", 0),
                "demand_level": demand_level,
            })

    st.success("Auto demand inferred")
    st.dataframe(auto_demand_rows, width='stretch')

    # Auto restock decisions via LLM
    auto_items_payload = []
    for item in selected_auto:
        prod = item.get("product")
        dl = auto_demand_map.get(prod, {}).get("demand_level", "medium")
        auto_items_payload.append({
            "product": prod,
            "stock": item.get("stock", 0),
            "price": item.get("price", 0),
            "location": item.get("location", auto_region_default),
            "demand_level": dl,
        })

    auto_decisions = []
    try:
        if LANGCHAIN_AVAILABLE:
            template = ChatPromptTemplate.from_messages([
                ("system", "You are a supply planning assistant. Given inventory items with stock and demand levels, decide RESTOCK or HOLD and suggest quantity if RESTOCK."),
                ("human", (
                    "Threshold: {threshold}; MinBatch: {min_batch}; MaxBatch: {max_batch}.\n"
                    "Items (JSON):\n{items}\n\n"
                    "Return STRICT JSON with key 'plan': array of items {{product, decision, suggested_qty, rationale}}."
                )),
            ])
            llm = ChatGroq(model=groq_model, temperature=0.1)
            chain = template | llm
            result = chain.invoke({
                "threshold": auto_threshold,
                "min_batch": auto_min_batch,
                "max_batch": auto_max_batch,
                "items": json.dumps(auto_items_payload, ensure_ascii=False),
            })
            content = getattr(result, "content", str(result))
            parsed = None
            try:
                parsed = json.loads(content)
            except Exception:
                import re
                m = re.search(r"\{[\s\S]*\}$", content.strip())
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                    except Exception:
                        parsed = None
            if parsed and isinstance(parsed, dict) and "plan" in parsed:
                auto_decisions = parsed["plan"]
        else:
            st.caption("LangChain not available; using heuristic decisions.")
    except Exception as e:
        st.error(f"Auto LLM decision error: {e}")

    if not auto_decisions:
        for it in auto_items_payload:
            h = restock_heuristic(it.get("stock", 0), it.get("demand_level"), auto_threshold, auto_min_batch, auto_max_batch)
            auto_decisions.append({
                "product": it.get("product"),
                "decision": h["decision"],
                "suggested_qty": h["suggested_qty"],
                "rationale": f"stock={it.get('stock')} demand={it.get('demand_level')} threshold={auto_threshold}"
            })

    st.success("Auto restock decisions ready")
    st.dataframe(auto_decisions, width='stretch')
else:
    st.error("❌ GROQ_API_KEY not found in .env file. Please configure it.")
