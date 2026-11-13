import datetime as dt

from tracker import notify
from tests.test_pipeline import add_user, mark_succeeded_before, nov_meeting, run_once


def setup_posting(conn, fetcher, agency="sbe", key="2026-11-05", title=None):
    mark_succeeded_before(conn, agency)
    p = nov_meeting(documents=[], external_key=key)
    if title:
        p.title = title
    return run_once(conn, fetcher, [p], agency=agency).new[0]


def set_prefs(conn, uid, **prefs):
    sets = ", ".join(f"{k} = %({k})s" for k in prefs)
    conn.execute(f"update user_prefs set {sets} where user_id = %(uid)s", {**prefs, "uid": uid})


def test_new_users_get_profile_and_prefs(conn):
    uid = add_user(conn, "b@example.org")
    assert conn.execute("select email_mode from user_prefs where user_id = %s",
                        (uid,)).fetchone()[0] == "none"


def test_immediate_alerts_respect_prefs_and_are_not_repeated(conn, fetcher, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda to, subj, body: sent.append((to, subj, body)) or True)
    sbe = setup_posting(conn, fetcher)
    setup_posting(conn, fetcher, agency="calpers", key="c1", title="CalPERS board")
    wants_all = add_user(conn, "all@example.org")
    set_prefs(conn, wants_all, email_mode="immediate")
    sbe_only = add_user(conn, "sbe@example.org")
    set_prefs(conn, sbe_only, email_mode="immediate", agency_ids=["sbe"])
    hits_only = add_user(conn, "hits@example.org")
    set_prefs(conn, hits_only, email_mode="immediate", only_keyword_hits=True)
    add_user(conn, "none@example.org")

    assert notify.send_user_alerts(conn, "immediate") == 2
    recipients = {to[0]: body for to, _, body in sent}
    assert set(recipients) == {"all@example.org", "sbe@example.org"}
    assert "CalPERS board" in recipients["all@example.org"]
    assert "CalPERS board" not in recipients["sbe@example.org"]

    sent.clear()
    assert notify.send_user_alerts(conn, "immediate") == 0

    conn.execute("insert into keywords (term) values ('Acme')")
    conn.execute("update postings set content_hash = 'x', last_changed_at = now() where id = %s", (sbe,))
    conn.execute("insert into posting_events (posting_id, kind, content_hash) values (%s, 'updated', 'x')", (sbe,))
    notify.send_user_alerts(conn, "immediate")
    assert {to[0] for to, _, _ in sent} == {"all@example.org", "sbe@example.org", "hits@example.org"}
    assert "Acme" in [b for t, _, b in sent if t == ["hits@example.org"]][0]


def test_unsent_email_is_retried(conn, fetcher, monkeypatch):
    monkeypatch.setattr(notify, "send_email", lambda *a: False)
    setup_posting(conn, fetcher)
    uid = add_user(conn)
    set_prefs(conn, uid, email_mode="daily")
    assert notify.send_user_alerts(conn, "daily") == 0
    assert conn.execute("select count(*) from notifications_sent").fetchone()[0] == 0


def test_send_email_without_config_does_nothing(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert notify.send_email(["x@example.org"], "s", "b") is False


def test_digest_respects_programs_and_lists_calendar_hearings(conn, fetcher, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda to, subj, body: sent.append((to, subj, body)) or True)
    setup_posting(conn, fetcher, title="SBE November meeting")
    mark_succeeded_before(conn, "abgt_sub3")
    soon = dt.date.today() + dt.timedelta(days=2)
    hearing = run_once(conn, fetcher, [nov_meeting(external_key="h", documents=[], meeting_date=soon,
                                                   title="Sub 3 hearing: K-12")],
                       agency="abgt_sub3").new[0]
    conn.execute("insert into calendar_hearings (posting_id) values (%s)", (hearing,))
    conn.execute("update agencies set last_note = 'agenda had no items' where id = 'abgt_sub3'")
    budget = add_user(conn, "budget@example.org")
    set_prefs(conn, budget, email_mode="daily", programs=["budget"])
    education = add_user(conn, "edu@example.org")
    set_prefs(conn, education, email_mode="daily", programs=["education"])

    assert notify.send_user_alerts(conn, "daily") == 2
    bodies = {to[0]: body for to, _, body in sent}
    assert "Sub 3 hearing: K-12" in bodies["budget@example.org"]
    assert "SBE November meeting" not in bodies["budget@example.org"]
    assert "Hearings on the team calendar" in bodies["budget@example.org"]
    assert "agenda had no items" in bodies["budget@example.org"]
    assert "Sub 3 hearing" not in bodies["edu@example.org"]
    assert "SBE November meeting" in bodies["edu@example.org"]

    # Nothing new the next day, but the hearing is still coming up: the digest still goes
    # to the budget user, and not to the education user.
    sent.clear()
    assert notify.send_user_alerts(conn, "daily") == 1
    (to, subject, body), = sent
    assert to == ["budget@example.org"] and "1 hearing(s) on the calendar" in subject
    assert "No new or updated postings" in body
