from __future__ import annotations

import hmac
import html
import io
import os
import time
from datetime import date

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Art Class Manager", page_icon="🎨", layout="wide")

# ---------------- Security ----------------
def _secret(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        return str(st.secrets.get(name, default)).strip()
    except Exception:
        return default

APP_PASSWORD = _secret("APP_PASSWORD")


def login_gate() -> None:
    if not APP_PASSWORD:
        st.error("App access is not configured yet. Add APP_PASSWORD to Streamlit Secrets before deployment.")
        st.stop()
    if st.session_state.get("authenticated"):
        return
    st.markdown("# 🎨 Art Class Manager")
    st.caption("Private access")
    if time.time() < st.session_state.get("locked_until", 0):
        st.error("Too many wrong attempts. Please wait a minute and try again.")
        st.stop()
    with st.form("login_form"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", use_container_width=True)
        if submitted:
            if hmac.compare_digest(password.encode(), APP_PASSWORD.encode()):
                st.session_state.authenticated = True
                st.session_state.fails = 0
                st.rerun()
            else:
                st.session_state.fails = st.session_state.get("fails", 0) + 1
                if st.session_state.fails >= 5:
                    st.session_state.locked_until = time.time() + 60
                    st.session_state.fails = 0
                st.error("Incorrect password.")
    st.stop()


login_gate()

# Database is intentionally imported only after the login gate so a missing
# production DATABASE_URL produces a clear setup message instead of a traceback.
try:
    import database as db
except RuntimeError as exc:
    st.error("⚠️ Persistent database is not configured.")
    st.warning(str(exc))
    st.info("Open Streamlit → App settings → Secrets and add DATABASE_URL. "
            "Do not rely on the local .db file for business data.")
    st.stop()

try:
    db.init_db()
    healthy, db_label = db.check_connection()
    if not healthy:
        st.error(f"Database connection failed: {db_label}")
        st.stop()
except Exception as exc:
    st.error(f"Database setup failed: {type(exc).__name__}: {exc}")
    st.stop()
if not st.session_state.get("backup_done"):
    try:
        db.backup_local_db()  # automatic snapshot each time the app is opened (local mode)
    except Exception:
        pass
    st.session_state.backup_done = True

# ---------------- Styling ----------------
st.markdown("""
<style>
.stApp { background:#f7f8fc; }
.block-container { max-width:1400px; padding-top:1.5rem; padding-bottom:3rem; }
.hero { background:linear-gradient(115deg,#243b53,#486581); color:white; padding:24px 28px; border-radius:18px; margin-bottom:20px; }
.hero h1 { color:white !important; margin:0 0 6px; }
.card { background:white; border:1px solid #e5e7eb; border-radius:16px; padding:18px; margin-bottom:14px; }
.muted { color:#667085; font-size:13px; }
.big { font-size:28px; font-weight:800; }
</style>
""", unsafe_allow_html=True)


def money(x: float) -> str:
    return f"₹{x:,.2f}"


def safe_rerun():
    st.rerun()


def dashboard():
    st.markdown('<div class="hero"><h1>🎨 Art Class Manager</h1><div>Manage societies, students, attendance and fees from one place.</div></div>', unsafe_allow_html=True)
    societies = db.get_societies()
    with st.expander("➕ Add New Society"):
        with st.form("add_society"):
            name = st.text_input("Society name")
            fee = st.number_input("Fixed monthly fee per student (₹)", min_value=0.01, step=100.0)
            notes = st.text_area("Notes (optional)")
            if st.form_submit_button("Create society", use_container_width=True):
                try:
                    db.create_society(name, fee, notes)
                    st.success("Society created.")
                    safe_rerun()
                except Exception as e:
                    st.error(str(e))
    if not societies:
        st.info("No societies yet. Add your first society above.")
        return
    cols = st.columns(3)
    for i, s in enumerate(societies):
        stats = db.dashboard_stats(s["id"])
        with cols[i % 3]:
            st.markdown(f'<div class="card"><h3>{html.escape(s["name"])}</h3><div class="muted">{stats["students"]} active students</div><div class="big">{money(stats["pending"])}</div><div class="muted">pending fees</div></div>', unsafe_allow_html=True)
            if st.button("Open society", key=f"open_soc_{s['id']}", use_container_width=True):
                st.session_state.society_id = s["id"]
                st.session_state.page = "Overview"
                safe_rerun()


def society_page(society_id: int):
    society = db.get_society(society_id)
    if not society:
        st.session_state.pop("society_id", None)
        safe_rerun()
    # Keep the fee ledger current automatically. Existing charges/payments are never overwritten.
    try:
        db.ensure_current_month_fees(society_id)
    except Exception as e:
        st.error(f"Could not update the monthly fee ledger: {e}")
        st.stop()
    st.markdown(f"# 🏛️ {society['name']}")
    if st.button("← Back to dashboard"):
        st.session_state.pop("society_id", None)
        safe_rerun()
    stats = db.dashboard_stats(society_id)
    a,b,c,d = st.columns(4)
    a.metric("Active students", stats["students"])
    b.metric("Fees due", money(stats["due"]))
    c.metric("Collected", money(stats["paid"]))
    d.metric("Pending", money(stats["pending"]))

    page = st.sidebar.radio("Society menu", ["Overview", "Students", "Attendance", "Fees", "Reports"], key="society_menu")
    if page == "Overview":
        st.info(f"🔒 Fixed monthly fee per student: **{money(float(society['monthly_fee']))}**. This fee is set when the society is created and cannot be changed per student or later in the app.")
        with st.expander("⚙️ Edit society"):
            with st.form("edit_society"):
                name = st.text_input("Society name", value=society["name"])
                st.text_input("Fixed monthly fee per student", value=money(float(society["monthly_fee"])), disabled=True)
                notes = st.text_area("Notes", value=society.get("notes", "") or "")
                if st.form_submit_button("Save changes"):
                    try:
                        db.update_society(society_id, name, notes)
                        st.success("Saved. The fixed monthly fee was not changed.")
                        safe_rerun()
                    except Exception as e:
                        st.error(str(e))
        with st.expander("Delete society"):
            st.warning("Only an empty society can be deleted. Student history is never destructively deleted.")
            if st.button("Delete this society", type="secondary"):
                try:
                    db.delete_society(society_id)
                    st.session_state.pop("society_id", None)
                    safe_rerun()
                except Exception as e:
                    st.error(str(e))

    elif page == "Students":
        st.subheader("Student management")
        students = db.get_students(society_id, include_inactive=True)
        with st.expander("➕ Add student", expanded=True):
            with st.form("add_student"):
                name = st.text_input("Student name")
                parent = st.text_input("Parent name")
                phone = st.text_input("Phone")
                joining = st.date_input("Joining date", value=date.today())
                notes = st.text_area("Notes")
                if st.form_submit_button("Add student", use_container_width=True):
                    try:
                        db.create_student(society_id, name, parent, phone, joining, notes)
                        st.success("Student added.")
                        safe_rerun()
                    except Exception as e:
                        st.error(str(e))
        search = st.text_input("Search students")
        for s in students:
            if search and search.lower() not in s["name"].lower() and search.lower() not in (s["phone"] or "").lower():
                continue
            with st.container(border=True):
                left, mid, right = st.columns([3,2,1])
                with left:
                    st.markdown(f"**{s['name']}**")
                    st.caption(f"Parent: {s['parent_name'] or '—'} · Phone: {s['phone'] or '—'}")
                with mid:
                    summary = db.fee_summary(s["id"])
                    st.write(f"Pending: **{money(summary['pending'])}**")
                with right:
                    if s["active"]:
                        if st.button("Deactivate", key=f"deact_{s['id']}"):
                            db.set_student_active(s["id"], False); safe_rerun()
                    else:
                        st.warning("Inactive")
                        if st.button("Reactivate", key=f"react_{s['id']}"):
                            db.set_student_active(s["id"], True); safe_rerun()
                with st.expander("Edit / view"):
                    with st.form(f"edit_{s['id']}"):
                        name = st.text_input("Student name", value=s["name"], key=f"name_{s['id']}")
                        parent = st.text_input("Parent name", value=s["parent_name"] or "", key=f"parent_{s['id']}")
                        phone = st.text_input("Phone", value=s["phone"] or "", key=f"phone_{s['id']}")
                        joining = st.date_input("Joining date", value=pd.to_datetime(s["joining_date"]).date(), key=f"join_{s['id']}")
                        notes = st.text_area("Notes", value=s["notes"] or "", key=f"notes_{s['id']}")
                        if st.form_submit_button("Save student"):
                            try:
                                db.update_student(s["id"], name, parent, phone, joining, notes)
                                st.success("Saved.")
                                safe_rerun()
                            except Exception as e:
                                st.error(str(e))

                    st.markdown("**Danger zone**")
                    sure = st.checkbox("I understand this permanently deletes this student and ALL their attendance and fee records (use Deactivate to keep history)", key=f"delc_{s['id']}")
                    if st.button("Delete permanently", key=f"del_{s['id']}", disabled=not sure):
                        db.backup_local_db()
                        db.delete_student_permanently(s["id"])
                        safe_rerun()

    elif page == "Attendance":
        st.subheader("Attendance")
        dt = st.date_input("Date", value=date.today())
        rows = db.get_attendance_for_date(society_id, dt)
        if not rows:
            st.info("No active students yet.")
        else:
            values = {}
            for r in rows:
                values[r["id"]] = st.checkbox(r["name"], value=bool(r["present"]), key=f"att_{r['id']}_{dt.isoformat()}")
            if st.button("Save attendance", type="primary"):
                for sid, present in values.items():
                    db.mark_attendance(sid, dt, present)
                st.success("Attendance saved.")

    elif page == "Fees":
        st.subheader("Fees")
        show_inactive = st.checkbox("Include inactive students (e.g. someone who left but still owes)")
        active = db.get_students(society_id, include_inactive=show_inactive)
        if not active:
            st.info("Add students first.")
            return
        st.success(f"Monthly fees are automatic: every active student is charged **{money(float(society['monthly_fee']))} per month** from their joining month. Unpaid months stay pending until a payment is recorded.")
        st.caption("You do not manually create or edit individual monthly fee amounts. This prevents accidental fee differences between students in the same society.")
        student_options = {s["name"]: s["id"] for s in active}
        selected_name = st.selectbox("Student", list(student_options))
        sid = student_options[selected_name]
        with st.expander("💳 Record payment", expanded=True):
            pdate = st.date_input("Payment date", value=date.today())
            amount = st.number_input("Payment amount (₹)", min_value=0.01, step=100.0, key="payment_amount")
            method = st.selectbox("Method", ["Cash", "UPI", "Bank transfer", "Cheque", "Other"])
            reference = st.text_input("Reference / transaction no. (optional)")
            notes = st.text_area("Notes (optional)")
            st.caption("One payment can automatically cover multiple outstanding months. Partial payments are supported.")
            if st.button("Save payment", type="primary"):
                try:
                    db.record_payment(sid, pdate, amount, method, reference, notes)
                    st.success("Payment saved and allocated oldest outstanding charges first.")
                    safe_rerun()
                except Exception as e:
                    st.error(str(e))
        summary = db.fee_summary(sid)
        x,y,z = st.columns(3)
        x.metric("Total charged", money(summary["due"]))
        y.metric("Paid", money(summary["paid"]))
        z.metric("Pending", money(summary["pending"]))
        st.markdown("### Outstanding charges")
        st.dataframe(pd.DataFrame(db.unpaid_charges(sid)), use_container_width=True, hide_index=True)
        st.markdown("### Payment history")
        history = db.payment_history(sid)
        st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)
        if history:
            with st.expander("🗑️ Remove a wrongly entered payment"):
                labels = {f"#{i+1} · {h['payment_date']} · {money(float(h['amount']))} · {h['method']}": h["id"] for i, h in enumerate(history)}
                pick = st.selectbox("Payment", list(labels))
                ok = st.checkbox("Yes, remove this payment", key="delpay_ok")
                if st.button("Remove payment", disabled=not ok):
                    db.backup_local_db()
                    db.delete_payment(labels[pick])
                    safe_rerun()

    elif page == "Reports":
        st.subheader("Reports")
        st.markdown("### Pending fees (who owes, and for how many months)")
        od = pd.DataFrame(db.overdue_list(society_id))
        if od.empty:
            st.success("Nobody has pending fees.")
        else:
            od["pending"] = od["pending"].astype(float)
            st.dataframe(od, use_container_width=True, hide_index=True)
        rows = db.society_report(society_id)
        df = pd.DataFrame(rows)
        if df.empty:
            st.info("No student data yet.")
        else:
            df["attendance_%"] = df.apply(lambda r: round(r["present_days"] / r["attendance_days"] * 100, 1) if r["attendance_days"] else 0, axis=1)
            df["pending"] = df["fees_due"].astype(float) - df["fees_paid"].astype(float)
            st.dataframe(df, use_container_width=True, hide_index=True)
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Society Report")
            st.download_button("Download Excel report", output.getvalue(), file_name=f"{society['name']}_report.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def backup_xlsx() -> bytes:
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        for name, rows in db.export_all().items():
            pd.DataFrame(rows).to_excel(w, index=False, sheet_name=name)
    return out.getvalue()


with st.sidebar:
    st.markdown("## 🎨 Art Class Manager")
    if st.session_state.get("authenticated"):
        st.success("Signed in")
        st.caption(f"Database: {db.database_mode()}")
        if st.button("Sign out"):
            st.session_state.clear(); safe_rerun()
    st.divider()
    st.download_button("⬇️ Download full backup (Excel)", backup_xlsx(), file_name=f"art_class_backup_{date.today().isoformat()}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
    st.caption("Remote database: persistent across Streamlit restarts. Still download a backup regularly.")
    st.divider()
    if "society_id" not in st.session_state:
        st.caption("Dashboard")
    else:
        st.caption("Society management")

if "society_id" in st.session_state:
    society_page(st.session_state.society_id)
else:
    dashboard()
