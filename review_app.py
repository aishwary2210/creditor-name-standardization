"""
Creditor Merge Review App (Streamlit)
======================================
A visual UI for reviewing auto-suggested merge candidates and manually
assigning creditor names.

Features:
  - View suggestions sorted by impact (source row count)
  - Approve / Reject per row
  - Manual search: type any name, find its canonical, assign it
  - Approved items write to CREDITOR_MANUAL_OVERRIDES table

Run locally:
  streamlit run review_app.py
"""

import streamlit as st
import snowflake.connector
import pandas as pd
from config import SNOWFLAKE_CONFIG, TARGET_SCHEMA

st.set_page_config(page_title="Creditor Review", layout="wide")
st.title("Creditor Standardization — Merge Review")


# =============================================================================
# CONNECTION
# =============================================================================

@st.cache_resource
def get_connection():
    return snowflake.connector.connect(**SNOWFLAKE_CONFIG)

conn = get_connection()


# =============================================================================
# LOAD DATA
# =============================================================================

@st.cache_data(ttl=60)
def load_suggestions():
    query = f"""
        SELECT CURRENT_NAME, SUGGESTED_CANONICAL, REASON, DETAIL,
               SOURCE_ROW_COUNT, STATUS
        FROM {TARGET_SCHEMA}.CREDITOR_MERGE_SUGGESTIONS
        WHERE STATUS = 'pending'
        ORDER BY SOURCE_ROW_COUNT DESC
    """
    return pd.read_sql(query, conn)

@st.cache_data(ttl=60)
def load_canonicals():
    query = f"""
        SELECT CANONICAL_NAME, SOURCE_ROW_COUNT, CONFIDENCE_TIER
        FROM {TARGET_SCHEMA}.CANONICAL_CREDITORS
        ORDER BY SOURCE_ROW_COUNT DESC
    """
    return pd.read_sql(query, conn)

@st.cache_data(ttl=60)
def load_overrides():
    try:
        query = f"""
            SELECT ORIGINAL_NAME, CANONICAL_NAME, APPROVED_AT
            FROM {TARGET_SCHEMA}.CREDITOR_MANUAL_OVERRIDES
            ORDER BY APPROVED_AT DESC
        """
        return pd.read_sql(query, conn)
    except Exception:
        return pd.DataFrame(columns=['ORIGINAL_NAME', 'CANONICAL_NAME', 'APPROVED_AT'])


def save_override(original_name, canonical_name):
    """Write an approved override to Snowflake."""
    cursor = conn.cursor()
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {TARGET_SCHEMA}.CREDITOR_MANUAL_OVERRIDES (
            ORIGINAL_NAME VARCHAR,
            CANONICAL_NAME VARCHAR,
            APPROVED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
            APPROVED_BY VARCHAR DEFAULT CURRENT_USER()
        )
    """)
    cursor.execute(f"""
        MERGE INTO {TARGET_SCHEMA}.CREDITOR_MANUAL_OVERRIDES t
        USING (SELECT %s AS ORIGINAL_NAME, %s AS CANONICAL_NAME) s
        ON t.ORIGINAL_NAME = s.ORIGINAL_NAME
        WHEN MATCHED THEN UPDATE SET CANONICAL_NAME = s.CANONICAL_NAME,
                                     APPROVED_AT = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (ORIGINAL_NAME, CANONICAL_NAME)
                              VALUES (s.ORIGINAL_NAME, s.CANONICAL_NAME)
    """, (original_name, canonical_name))
    cursor.close()


def update_suggestion_status(current_name, status):
    """Mark a suggestion as approved or rejected."""
    cursor = conn.cursor()
    cursor.execute(f"""
        UPDATE {TARGET_SCHEMA}.CREDITOR_MERGE_SUGGESTIONS
        SET STATUS = %s
        WHERE CURRENT_NAME = %s
    """, (status, current_name))
    cursor.close()


# =============================================================================
# UI TABS
# =============================================================================

tab1, tab2, tab3 = st.tabs(["Auto-Suggestions", "Manual Assign", "Approved Overrides"])


# ─── TAB 1: AUTO-SUGGESTIONS ────────────────────────────────────────────────
with tab1:
    st.subheader("Auto-Suggested Merges")
    st.caption("These were found by the auto-suggest script. Review and approve/reject.")

    suggestions = load_suggestions()

    if suggestions.empty:
        st.success("No pending suggestions. All reviewed!")
    else:
        st.metric("Pending Suggestions", len(suggestions))

        # Filter by reason
        reasons = ['All'] + sorted(suggestions['REASON'].unique().tolist())
        selected_reason = st.selectbox("Filter by reason:", reasons)

        if selected_reason != 'All':
            filtered = suggestions[suggestions['REASON'] == selected_reason]
        else:
            filtered = suggestions

        # Show top N
        page_size = st.slider("Show top N:", 10, 200, 50)
        display = filtered.head(page_size)

        for idx, row in display.iterrows():
            col1, col2, col3, col4 = st.columns([3, 3, 1, 1])
            with col1:
                st.text(f"{row['CURRENT_NAME']}")
            with col2:
                st.text(f"→ {row['SUGGESTED_CANONICAL']}")
            with col3:
                if st.button("Approve", key=f"approve_{idx}"):
                    save_override(row['CURRENT_NAME'], row['SUGGESTED_CANONICAL'])
                    update_suggestion_status(row['CURRENT_NAME'], 'approved')
                    st.cache_data.clear()
                    st.rerun()
            with col4:
                if st.button("Reject", key=f"reject_{idx}"):
                    update_suggestion_status(row['CURRENT_NAME'], 'rejected')
                    st.cache_data.clear()
                    st.rerun()

            st.caption(f"  Reason: {row['REASON']} | Rows: {row['SOURCE_ROW_COUNT']:,} | {row['DETAIL']}")
            st.divider()


# ─── TAB 2: MANUAL ASSIGN ───────────────────────────────────────────────────
with tab2:
    st.subheader("Manual Assignment")
    st.caption("Search for any name and assign it to a canonical creditor.")

    canonicals_df = load_canonicals()
    canonical_list = canonicals_df['CANONICAL_NAME'].tolist()

    # Search for a name to reassign
    search_name = st.text_input("Enter the name you want to reassign:")

    if search_name:
        # Show current mapping
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT CANONICAL_NAME, CONFIDENCE_TIER, MATCH_SOURCE
            FROM {TARGET_SCHEMA}.CREDITOR_ALIAS_MAP
            WHERE NORMALIZED_COMPANY = %s
        """, (search_name.upper(),))
        current = cursor.fetchone()
        cursor.close()

        if current:
            st.info(f"Currently mapped to: **{current[0]}** (tier: {current[1]}, source: {current[2]})")
        else:
            st.warning("Not found in alias map.")

        # Search for target canonical
        target_search = st.text_input("Search for the correct canonical:")
        if target_search:
            matches = [c for c in canonical_list if target_search.upper() in c]
            if matches:
                selected = st.selectbox("Select canonical:", matches[:20])
                if st.button("Assign Override"):
                    save_override(search_name.upper(), selected)
                    st.success(f"Saved: {search_name.upper()} → {selected}")
                    st.cache_data.clear()
            else:
                st.warning("No matching canonicals found.")


# ─── TAB 3: APPROVED OVERRIDES ──────────────────────────────────────────────
with tab3:
    st.subheader("Approved Overrides")
    st.caption("These will be applied as exact matches on the next pipeline run.")

    overrides = load_overrides()
    if overrides.empty:
        st.info("No overrides yet. Approve suggestions or use manual assign.")
    else:
        st.metric("Total Overrides", len(overrides))
        st.dataframe(overrides, use_container_width=True)
