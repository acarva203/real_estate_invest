import streamlit as st
import pandas as pd
import plotly.express as px
import os

# Import your existing functions directly — no changes needed
from census_data_pull import (
    fetch_zcta_data, compute_derived_metrics,
    compute_trends, compute_opportunity_score, assign_tiers,
    YEARS, DEV_ZIP_PREFIXES, API_KEY
)

st.set_page_config(
    page_title="Real Estate Opportunity Dashboard",
    page_icon="🏠",
    layout="wide"
)

# ── 1. CACHED DATA LOADING ────────────────────────────────────────────────────
# @st.cache_data tells Streamlit: run this once, cache the result.
# The Census API won't be called again unless you clear the cache.

@st.cache_data(show_spinner="Fetching Census data...")
def load_data(years, zip_prefixes, api_key):
    """Pull + score data. Cached so Census API is only hit once per session."""
    import time
    dfs = []
    for year in years:
        df = fetch_zcta_data(year, zip_prefixes=zip_prefixes)
        df = compute_derived_metrics(df)
        dfs.append(df)
        time.sleep(0.5)
    df_long = pd.concat(dfs, ignore_index=True)
    df_trends = compute_trends(df_long)
    df_scored = compute_opportunity_score(df_trends)
    df_scored = assign_tiers(df_scored)
    return df_long, df_scored


# ── 2. SIDEBAR CONTROLS ───────────────────────────────────────────────────────

st.sidebar.title("🏠 Controls")

# Let users pick which state prefix to analyze
prefix_options = {
    "California": ["9"],
    "Texas": ["75","76","77","78","79"],
    "Florida": ["32","33","34"],
    "New York": ["10","11","12","13","14"],
    "National (slow)": None,
}
state_choice = st.sidebar.selectbox("State / Region", list(prefix_options.keys()))
selected_prefixes = prefix_options[state_choice]

# Tier filter
tier_filter = st.sidebar.multiselect(
    "Show tiers",
    ["BUILD NOW", "WATCH LIST", "EMERGING", "MONITOR"],
    default=["BUILD NOW", "WATCH LIST", "EMERGING"]
)

# Score threshold slider
min_score = st.sidebar.slider("Min opportunity score", 0, 100, 40)

# Load data button — prevents auto-firing on every interaction
if st.sidebar.button("Load / Refresh Data", type="primary"):
    st.cache_data.clear()

# ── 3. LOAD DATA ──────────────────────────────────────────────────────────────

df_long, df_scored = load_data(
    tuple(YEARS),           # must be hashable for cache key
    tuple(selected_prefixes) if selected_prefixes else None,
    API_KEY
)

# Apply sidebar filters
df_view = df_scored[
    (df_scored["investment_tier"].isin(tier_filter)) &
    (df_scored["opportunity_score"] >= min_score)
].copy()


# ── 4. HEADER + KPI STRIP ─────────────────────────────────────────────────────

st.title("Real Estate Opportunity Dashboard")
st.caption(f"ACS 5-year estimates · {min(YEARS)}–{max(YEARS)} · {state_choice}")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Zip codes analyzed", f"{len(df_scored):,}")
col2.metric("BUILD NOW zips", (df_scored["investment_tier"] == "BUILD NOW").sum())
col3.metric("WATCH LIST zips", (df_scored["investment_tier"] == "WATCH LIST").sum())

# Find the best available score column dynamically
density_col = next(
    (c for c in sorted(df_scored.columns, reverse=True)
     if c.startswith("pct_hh_income_100k_plus_2")), None
)
if density_col:
    top_zip = df_scored.iloc[0]
    col4.metric("Top zip score", f"{top_zip['opportunity_score']:.1f}",
                delta=f"ZIP {top_zip['zcta']}")

st.divider()


# ── 5. CHOROPLETH MAP ─────────────────────────────────────────────────────────

st.subheader("Opportunity Score by ZIP Code")

fig_map = px.choropleth(
    df_view,
    geojson="https://raw.githubusercontent.com/OpenDataDE/State-zip-code-GeoJSON/master/all_us_zipcodes.geojson",
    locations="zcta",
    featureidkey="properties.ZCTA5CE10",
    color="opportunity_score",
    color_continuous_scale="RdYlGn",
    range_color=(0, 100),
    hover_data=["investment_tier", density_col] if density_col else ["investment_tier"],
    labels={"opportunity_score": "Score"},
)
fig_map.update_geos(fitbounds="locations", visible=False)
fig_map.update_layout(margin={"r":0,"t":0,"l":0,"b":0}, height=450)
st.plotly_chart(fig_map, use_container_width=True)

st.caption("⚠️ GeoJSON for 33k ZCTAs is large (~50MB). For the hackathon, "
           "swap the URL above for a state-level file to load faster.")

st.divider()


# ── 6. TOP ZIP CODES TABLE ────────────────────────────────────────────────────

st.subheader("Investment Tier Breakdown")

tab1, tab2, tab3 = st.tabs(["🟢 BUILD NOW", "🟡 WATCH LIST", "🔵 EMERGING"])

display_cols = ["zcta", "opportunity_score", "investment_tier", "rationale"]
if density_col:
    display_cols.insert(2, density_col)
growth_col = next(
    (c for c in sorted(df_scored.columns, reverse=True)
     if c.startswith("pct_hh_income_100k_plus_change_")), None
)
if growth_col:
    display_cols.insert(3, growth_col)

available_cols = [c for c in display_cols if c in df_view.columns]

for tab, tier in zip([tab1, tab2, tab3], ["BUILD NOW", "WATCH LIST", "EMERGING"]):
    with tab:
        subset = df_view[df_view["investment_tier"] == tier][available_cols].head(25)
        if subset.empty:
            st.info(f"No {tier} zip codes match your current filters.")
        else:
            st.dataframe(
                subset,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "opportunity_score": st.column_config.ProgressColumn(
                        "Score", min_value=0, max_value=100
                    ),
                    "rationale": st.column_config.TextColumn("Rationale", width="large"),
                }
            )

st.divider()


# ── 7. SCATTER: DENSITY vs GROWTH ────────────────────────────────────────────

if density_col and growth_col:
    st.subheader("Buyer Density vs. Demographic Growth")
    st.caption("Ideal targets are upper-right (high density, high growth). "
               "Bubble size = opportunity score.")

    fig_scatter = px.scatter(
        df_view.dropna(subset=[density_col, growth_col]),
        x=density_col,
        y=growth_col,
        size="opportunity_score",
        color="investment_tier",
        color_discrete_map={
            "BUILD NOW": "#16a34a",
            "WATCH LIST": "#ca8a04",
            "EMERGING": "#2563eb",
            "MONITOR": "#9ca3af",
        },
        hover_name="zcta",
        hover_data=["rationale"],
        labels={
            density_col: "% Households Earning $100k+ (latest year)",
            growth_col: "Change in % since earliest year (pp)",
        },
        height=500,
    )
    # Add quadrant lines at medians
    fig_scatter.add_vline(x=df_view[density_col].median(), line_dash="dot",
                          line_color="gray", opacity=0.5)
    fig_scatter.add_hline(y=0, line_dash="dot", line_color="gray", opacity=0.5)
    st.plotly_chart(fig_scatter, use_container_width=True)

    st.divider()


# ── 8. TIME-SERIES: SPOTLIGHT A ZIP ──────────────────────────────────────────

st.subheader("Demographic Trend — ZIP Code Spotlight")

top_zips = df_scored.head(30)["zcta"].tolist()
selected_zip = st.selectbox("Select a ZIP to inspect", top_zips)

zip_trend = df_long[df_long["zcta"] == selected_zip].sort_values("year")

if not zip_trend.empty and "pct_hh_income_100k_plus" in zip_trend.columns:
    fig_ts = px.line(
        zip_trend,
        x="year", y="pct_hh_income_100k_plus",
        markers=True,
        labels={"pct_hh_income_100k_plus": "% Households $100k+", "year": "ACS Year"},
        title=f"ZIP {selected_zip} — High-Income Household Share Over Time",
    )
    fig_ts.update_traces(line_color="#16a34a", line_width=3)
    st.plotly_chart(fig_ts, use_container_width=True)

    # Show the rationale card for this zip
    row = df_scored[df_scored["zcta"] == selected_zip]
    if not row.empty:
        tier = row.iloc[0]["investment_tier"]
        rationale = row.iloc[0]["rationale"]
        color = {"BUILD NOW": "green", "WATCH LIST": "orange",
                 "EMERGING": "blue", "MONITOR": "gray"}.get(tier, "gray")
        st.markdown(f"**Tier:** :{color}[{tier}]")
        st.info(rationale)


# ── 9. DOWNLOAD ───────────────────────────────────────────────────────────────

st.divider()
st.download_button(
    label="Download scored data (CSV)",
    data=df_scored.to_csv(index=False),
    file_name="zcta_opportunity_scores.csv",
    mime="text/csv",
)
