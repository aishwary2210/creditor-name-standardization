"""Streamlit app for approving merge suggestions and fixing mappings by hand.

    streamlit run review_app.py

Approved merges go to CREDITOR_MANUAL_OVERRIDES, which main.py and
incremental.py apply before any fuzzy matching.
"""

import pandas as pd
import streamlit as st

import db
from normalize import normalize_company

st.set_page_config(page_title="Creditor review", layout="wide")
st.title("Creditor merge review")


@st.cache_resource
def get_connection():
    return db.connect()


conn = get_connection()


def read(sql, params=None):
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [c[0] for c in cursor.description]
        return pd.DataFrame(cursor.fetchall(), columns=columns)


def save_override(name, canonical):
    db.query(conn, f"""
        CREATE TABLE IF NOT EXISTS {db.table(db.OVERRIDES)} (
            ORIGINAL_NAME VARCHAR,
            CANONICAL_NAME VARCHAR,
            APPROVED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
            APPROVED_BY VARCHAR DEFAULT CURRENT_USER()
        )
    """)
    db.query(conn, f"""
        MERGE INTO {db.table(db.OVERRIDES)} t
        USING (SELECT %s AS ORIGINAL_NAME, %s AS CANONICAL_NAME) s
        ON t.ORIGINAL_NAME = s.ORIGINAL_NAME
        WHEN MATCHED THEN UPDATE SET CANONICAL_NAME = s.CANONICAL_NAME,
                                     APPROVED_AT = CURRENT_TIMESTAMP(),
                                     APPROVED_BY = CURRENT_USER()
        WHEN NOT MATCHED THEN INSERT (ORIGINAL_NAME, CANONICAL_NAME)
                              VALUES (s.ORIGINAL_NAME, s.CANONICAL_NAME)
    """, (name, canonical))


def set_status(name, target, status):
    db.query(conn, f"""
        UPDATE {db.table(db.SUGGESTIONS)} SET STATUS = %s
        WHERE CURRENT_NAME = %s AND SUGGESTED_CANONICAL = %s AND STATUS = 'pending'
    """, (status, name, target))


suggestions_tab, manual_tab, overrides_tab = st.tabs(["Suggestions", "Manual fix", "Approved"])

with suggestions_tab:
    pending = pd.DataFrame()  # empty until suggest_merges.py has run
    if db.table_exists(conn, db.SUGGESTIONS):
        pending = read(f"""
            SELECT CURRENT_NAME, SUGGESTED_CANONICAL, REASON, SOURCE_ROWS
            FROM {db.table(db.SUGGESTIONS)}
            WHERE STATUS = 'pending'
            ORDER BY SOURCE_ROWS DESC
        """)
    if pending.empty:
        st.success("Nothing left to review.")
    else:
        reason = st.selectbox("Reason", ["All"] + sorted(pending["REASON"].unique()))
        if reason != "All":
            pending = pending[pending["REASON"] == reason]
        limit = st.slider("Rows to show", 10, 200, 50)
        st.caption(f"{len(pending):,} pending, largest first")

        for i, row in pending.head(limit).iterrows():
            name, target = row["CURRENT_NAME"], row["SUGGESTED_CANONICAL"]
            left, right, approve, reject = st.columns([4, 4, 1, 1])
            left.write(f"**{name}**  \n{row['SOURCE_ROWS']:,} rows, {row['REASON']}")
            right.write(f"→ {target}")
            if approve.button("Approve", key=f"approve_{i}"):
                save_override(name, target)
                set_status(name, target, "approved")
                st.rerun()
            if reject.button("Reject", key=f"reject_{i}"):
                set_status(name, target, "rejected")
                st.rerun()

with manual_tab:
    name = normalize_company(st.text_input("Creditor name as it appears in the source"))
    if name:
        current = read(f"""
            SELECT CANONICAL_NAME, TIER FROM {db.table(db.ALIAS_MAP)} WHERE NORMALIZED_NAME = %s
        """, (name,))
        if current.empty:
            st.warning(f"{name} is not in the alias map yet.")
        else:
            st.info(f"{name} currently maps to **{current.iloc[0, 0]}** ({current.iloc[0, 1]})")

        search = st.text_input("Search for the correct creditor").strip().upper()
        if search:
            matches = read(f"""
                SELECT CANONICAL_NAME FROM {db.table(db.CANONICALS)}
                WHERE CANONICAL_NAME ILIKE %s
                ORDER BY SOURCE_ROWS DESC
                LIMIT 20
            """, (f"%{search}%",))
            if matches.empty:
                st.warning("No creditor matches that search.")
            else:
                target = st.selectbox("Map to", matches["CANONICAL_NAME"])
                if st.button("Save"):
                    save_override(name, target)
                    st.success(f"Saved {name} → {target}. It applies on the next pipeline run.")

with overrides_tab:
    approved = pd.DataFrame()
    if db.table_exists(conn, db.OVERRIDES):
        approved = read(f"""
            SELECT ORIGINAL_NAME, CANONICAL_NAME, APPROVED_BY, APPROVED_AT
            FROM {db.table(db.OVERRIDES)}
            ORDER BY APPROVED_AT DESC
        """)
    if approved.empty:
        st.info("No approved overrides yet.")
    else:
        st.dataframe(approved, use_container_width=True)
