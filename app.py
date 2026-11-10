import streamlit as st
from dotenv import load_dotenv
import threading
import queue
import re
import os

load_dotenv()

# Inject Streamlit Cloud secrets into environment (for deployment)
if hasattr(st, "secrets"):
    for key, value in st.secrets.items():
        if isinstance(value, str):
            os.environ.setdefault(key, value)

# ── Patch crewai prompt-cache breakpoints (Groq rejects cache_breakpoint) ────
# Must run before any crewai imports happen in crew.py / agent files
try:
    import crewai.llms.cache as _crewai_cache
    def _noop_mark_cache_breakpoint(message):
        """Strip cache_breakpoint key — Groq doesn't support it."""
        msg = dict(message)
        msg.pop("cache_breakpoint", None)
        return msg
    _crewai_cache.mark_cache_breakpoint = _noop_mark_cache_breakpoint
except Exception:
    pass

# ── Patch ToolsHandler so cache=True (bool) doesn't crash on .read()/.get() ──
try:
    from crewai.agents.tools_handler import ToolsHandler
    _orig_tools_handler_init = ToolsHandler.__init__
    def _patched_tools_handler_init(self, *args, **kwargs):
        _orig_tools_handler_init(self, *args, **kwargs)
        if not hasattr(self.cache, "read"):
            self.cache = None
    ToolsHandler.__init__ = _patched_tools_handler_init
except Exception:
    pass



# Inject Streamlit Cloud secrets into environment (for deployment)
if hasattr(st, "secrets"):
    for key, value in st.secrets.items():
        if isinstance(value, str):
            os.environ.setdefault(key, value)

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Stock Analyst",
    page_icon="📈",
    layout="centered",
)

# ── Styles ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main { background-color: #0e1117; }
    .stTextInput > div > div > input {
        background-color: #1e2130;
        color: #ffffff;
        border: 1px solid #3d4663;
        border-radius: 8px;
        font-size: 18px;
        padding: 10px;
    }
    .metric-card {
        background: linear-gradient(135deg, #1e2130, #252d3d);
        border: 1px solid #3d4663;
        border-radius: 12px;
        padding: 20px;
        text-align: center;
        margin: 8px 0;
    }
    .metric-label {
        color: #8892b0;
        font-size: 13px;
        font-weight: 600;
        letter-spacing: 1px;
        text-transform: uppercase;
        margin-bottom: 6px;
    }
    .metric-value {
        color: #ccd6f6;
        font-size: 28px;
        font-weight: 700;
    }
    .metric-change-positive { color: #64ffda; font-size: 16px; }
    .metric-change-negative { color: #ff6b6b; font-size: 16px; }
    .recommendation-buy {
        background: linear-gradient(135deg, #0d3321, #1a5c3a);
        border: 2px solid #64ffda;
        border-radius: 16px;
        padding: 24px;
        text-align: center;
    }
    .recommendation-sell {
        background: linear-gradient(135deg, #3d0d0d, #5c1a1a);
        border: 2px solid #ff6b6b;
        border-radius: 16px;
        padding: 24px;
        text-align: center;
    }
    .recommendation-hold {
        background: linear-gradient(135deg, #2d2d0d, #4a4a1a);
        border: 2px solid #ffd700;
        border-radius: 16px;
        padding: 24px;
        text-align: center;
    }
    .rec-label {
        color: #8892b0;
        font-size: 13px;
        font-weight: 600;
        letter-spacing: 2px;
        text-transform: uppercase;
    }
    .rec-value {
        font-size: 48px;
        font-weight: 900;
        margin: 8px 0;
    }
    .rec-buy  { color: #64ffda; }
    .rec-sell { color: #ff6b6b; }
    .rec-hold { color: #ffd700; }
    .log-box {
        background-color: #131722;
        border: 1px solid #2d3550;
        border-radius: 8px;
        padding: 16px;
        font-family: 'Courier New', monospace;
        font-size: 12px;
        color: #8892b0;
        max-height: 300px;
        overflow-y: auto;
        white-space: pre-wrap;
    }
    .section-title {
        color: #ccd6f6;
        font-size: 14px;
        font-weight: 700;
        letter-spacing: 1.5px;
        text-transform: uppercase;
        margin: 20px 0 10px 0;
        border-bottom: 1px solid #2d3550;
        padding-bottom: 6px;
    }
    .analysis-box {
        background-color: #131722;
        border-left: 3px solid #3d7bd4;
        border-radius: 0 8px 8px 0;
        padding: 16px;
        color: #ccd6f6;
        font-size: 14px;
        line-height: 1.7;
        margin: 8px 0;
    }
</style>
""", unsafe_allow_html=True)

# ── Header ───────────────────────────────────────────────────────────────────
st.markdown("## 📈 AI Stock Analyst")
st.markdown("<p style='color:#8892b0; margin-top:-10px;'>Powered by Hritam · Multi-Agent Analysis</p>", unsafe_allow_html=True)
st.divider()

# ── Input ─────────────────────────────────────────────────────────────────────
col1, col2 = st.columns([3, 1])
with col1:
    ticker = st.text_input(
        "Stock Ticker",
        placeholder="e.g. AAPL, TSLA, MSFT, NVDA",
        label_visibility="collapsed",
    ).strip().upper()
with col2:
    run_btn = st.button("🔍 Analyse", use_container_width=True, type="primary")


# ── Helper: parse outputs ─────────────────────────────────────────────────────
def parse_price_info(text: str):
    """Extract price and change from analyst output."""
    price = re.search(r'\*{0,2}(?:Current Stock Price|Price)[:\s*]+\$?([\d,.]+)', text, re.IGNORECASE)
    change = re.search(r'(?:Daily Price Change|Change)[:\s*‑\-]+\$?([\d,.]+)\s*USD?\s*\(([-\d.]+)%\)', text, re.IGNORECASE)
    return (
        price.group(1) if price else None,
        change.group(1) if change else None,
        change.group(2) if change else None,
    )


def extract_recommendation(text: str) -> str | None:
    """Find BUY / SELL / HOLD in the trader output."""
    m = re.search(r'\b(BUY|SELL|HOLD)\b', text, re.IGNORECASE)
    return m.group(1).upper() if m else None


def run_crew(ticker: str, log_queue: queue.Queue, result_queue: queue.Queue):
    """Run the crew in a background thread and stream logs."""
    import io, sys
    from crew import stock_crew

    # Capture stdout so we can stream it
    old_stdout = sys.stdout
    sys.stdout = buffer = io.StringIO()

    try:
        result = stock_crew.kickoff(inputs={"stock": ticker})
        output = buffer.getvalue()
        result_queue.put(("success", str(result), output))
    except Exception as e:
        output = buffer.getvalue()
        result_queue.put(("error", str(e), output))
    finally:
        sys.stdout = old_stdout
        log_queue.put(None)  # sentinel


# ── Run ───────────────────────────────────────────────────────────────────────
if run_btn and ticker:
    st.divider()

    analyst_placeholder = st.empty()
    trader_placeholder  = st.empty()
    log_placeholder     = st.empty()

    with st.spinner(f"Analysing **{ticker}** — this takes ~20–40 seconds…"):
        log_q    = queue.Queue()
        result_q = queue.Queue()

        t = threading.Thread(target=run_crew, args=(ticker, log_q, result_q), daemon=True)
        t.start()
        t.join()

    status, result_text, raw_log = result_q.get()

    if status == "error":
        st.error(f"❌ Crew failed: {result_text}")
        with st.expander("Raw log"):
            st.code(raw_log)
        st.stop()

    # ── Split output into analyst vs trader sections ──────────────────────────
    # CrewAI verbose output contains "Agent: Financial Market Analyst" markers
    analyst_output = ""
    trader_output  = result_text  # final output is always from trader

    # Try to find analyst section from raw log
    analyst_match = re.search(
        r'Final Answer:\s*\n(.*?)(?=╭|Agent: Strategic|$)',
        raw_log, re.DOTALL
    )
    if analyst_match:
        analyst_output = analyst_match.group(1).strip()

    # ── Price metrics card ────────────────────────────────────────────────────
    price, change_abs, change_pct = parse_price_info(analyst_output or result_text)

    if price:
        is_positive = change_pct and not change_pct.startswith('-')
        change_color = "metric-change-positive" if is_positive else "metric-change-negative"
        change_arrow = "▲" if is_positive else "▼"
        change_display = f"{change_arrow} ${change_abs} ({change_pct}%)" if change_abs else ""

        c1, c2, c3 = st.columns(3)
        c1.markdown(f"""
        <div class='metric-card'>
            <div class='metric-label'>Ticker</div>
            <div class='metric-value'>{ticker}</div>
        </div>""", unsafe_allow_html=True)
        c2.markdown(f"""
        <div class='metric-card'>
            <div class='metric-label'>Current Price</div>
            <div class='metric-value'>${price}</div>
        </div>""", unsafe_allow_html=True)
        c3.markdown(f"""
        <div class='metric-card'>
            <div class='metric-label'>Daily Change</div>
            <div class='metric-value'>
                <span class='{change_color}'>{change_display or "N/A"}</span>
            </div>
        </div>""", unsafe_allow_html=True)

    # ── Recommendation card ───────────────────────────────────────────────────
    rec = extract_recommendation(result_text)
    if rec:
        rec_lower = rec.lower()
        emoji = {"buy": "🚀", "sell": "🔻", "hold": "⏸️"}.get(rec_lower, "")
        st.markdown(f"""
        <div class='recommendation-{rec_lower}' style='margin: 20px 0;'>
            <div class='rec-label'>AI Recommendation</div>
            <div class='rec-value rec-{rec_lower}'>{emoji} {rec}</div>
            <div style='color:#8892b0; font-size:13px;'>Based on live market data analysis</div>
        </div>""", unsafe_allow_html=True)

    # ── Analyst summary ───────────────────────────────────────────────────────
    if analyst_output:
        st.markdown("<div class='section-title'>📊 Market Analysis</div>", unsafe_allow_html=True)
        # Clean up markdown artifacts
        clean = re.sub(r'\*\*(.*?)\*\*', r'**\1**', analyst_output)
        st.markdown(f"<div class='analysis-box'>{clean}</div>", unsafe_allow_html=True)

    # ── Full trader reasoning ─────────────────────────────────────────────────
    st.markdown("<div class='section-title'>🧠 Trading Rationale</div>", unsafe_allow_html=True)
    # Remove top-level recommendation header since we already show it as a card
    clean_trader = re.sub(r'\*\*Recommendation:.*?\*\*\s*\n?---\s*\n?', '', result_text, flags=re.IGNORECASE).strip()
    st.markdown(clean_trader)

    # ── Raw log expander ──────────────────────────────────────────────────────
    with st.expander("🔎 Raw agent logs"):
        st.code(raw_log, language="text")

elif run_btn and not ticker:
    st.warning("Please enter a stock ticker symbol.")

else:
    # Landing state
    st.markdown("""
    <div style='text-align:center; padding: 60px 20px; color: #8892b0;'>
        <div style='font-size: 64px; margin-bottom: 16px;'>🤖</div>
        <div style='font-size: 18px; color: #ccd6f6; margin-bottom: 8px;'>Two AI agents working together</div>
        <div style='font-size: 14px;'>
            A <b style='color:#64ffda'>Financial Analyst</b> fetches live market data, 
            then a <b style='color:#ffd700'>Strategic Trader</b> delivers a Buy / Sell / Hold verdict.
        </div>
    </div>
    """, unsafe_allow_html=True)
