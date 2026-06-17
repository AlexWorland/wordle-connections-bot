import sqlite3


def win_rate_by_model(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT model,
                  COUNT(*) AS games,
                  SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) AS wins,
                  CAST(SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) AS REAL)
                      / COUNT(*) AS win_rate
           FROM games
           GROUP BY model
           ORDER BY model"""
    ).fetchall()
    return [dict(row) for row in rows]
