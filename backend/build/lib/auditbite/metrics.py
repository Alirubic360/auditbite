from __future__ import annotations


def dashboard(conn, org_id) -> dict:
    """KPIs. 'success_rate' = recovered / claimed over claims that have been sent (stated in the UI)."""
    k = conn.execute("""
      select
        (select count(*) from invoices where org_id=%(o)s and status in ('audited','needs_review','claimed','recovered')) as invoices_audited,
        (select coalesce(sum(total),0) from invoices where org_id=%(o)s and status in ('audited','needs_review','claimed','recovered')) as total_spend_audited,
        (select coalesce(sum(amount),0) from discrepancies where org_id=%(o)s and review_status='approved') as discrepancies_found,
        (select coalesce(sum(recovered_amount),0) from claims where org_id=%(o)s) as recovered_cash,
        (select coalesce(sum(total_claimed),0) from claims where org_id=%(o)s and status<>'draft') as claimed_sent,
        (select coalesce(sum(fee_amount),0) from claims where org_id=%(o)s) as fees
    """, {"o": org_id}).fetchone()
    k["success_rate"] = (k["recovered_cash"] / k["claimed_sent"]) if k["claimed_sent"] else None
    k["recovery_by_month"] = conn.execute("""select to_char(date_trunc('month', coalesce(sent_at, created_at)),'YYYY-MM') as month,
        sum(recovered_amount) as recovered from claims where org_id=%s group by 1 order by 1""", (org_id,)).fetchall()
    k["top_vendors"] = conn.execute("""select v.id, v.name, sum(d.amount) as overcharge from discrepancies d
        join invoices i on i.id=d.invoice_id join vendors v on v.id=i.vendor_id
        where d.org_id=%s and d.review_status='approved' group by v.id, v.name order by overcharge desc limit 5""", (org_id,)).fetchall()
    return k


def precision_by_month(conn, org_id) -> list[dict]:
    """Spec section 8: measure claim precision monthly = approved / (approved + rejected) human decisions."""
    return conn.execute("""select to_char(date_trunc('month', reviewed_at),'YYYY-MM') as month,
        count(*) filter (where review_status='approved') as approved, count(*) filter (where review_status='rejected') as rejected,
        round(count(*) filter (where review_status='approved')::numeric / nullif(count(*),0), 3) as precision
        from discrepancies where org_id=%s and review_status in ('approved','rejected') group by 1 order by 1""", (org_id,)).fetchall()
