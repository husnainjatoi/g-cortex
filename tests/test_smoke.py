def test_db_ready(actors):
    from app import db
    assert db.query_one("SELECT COUNT(*) AS n FROM users")["n"] >= 7
