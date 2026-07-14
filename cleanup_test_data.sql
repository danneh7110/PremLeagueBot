-- Removes all leftover test data from the debugging session:
-- fake matches (900000000+), the matchday 99 test gameweek, the matchday 0
-- test snapshot, and resets managers back to a clean slate. Safe to run
-- since the real season hasn't started yet (Aug 21, 2026) - none of this
-- is real data.
--
-- Run with: sqlite3 database.db < cleanup_test_data.sql

DELETE FROM predictions WHERE match_id >= 900000000;
DELETE FROM matches WHERE match_id >= 900000000;
DELETE FROM mvp_announcements WHERE matchday IN (0, 99);
DELETE FROM gameweek_snapshots WHERE matchday IN (0, 99);
UPDATE managers SET points = 0, prediction_points = 0;

-- Verify it's clean:
SELECT 'managers:' AS '';
SELECT display_name, team, points, prediction_points FROM managers;
SELECT 'remaining fake matches (should be empty):' AS '';
SELECT match_id FROM matches WHERE match_id >= 900000000;
SELECT 'remaining test announcements (should be empty):' AS '';
SELECT * FROM mvp_announcements WHERE matchday IN (0, 99);
