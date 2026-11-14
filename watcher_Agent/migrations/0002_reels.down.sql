-- Rollback of 0002. Destroys reel records and thumbnails.
DROP TABLE IF EXISTS judge.reel_thumbnails;
DROP TABLE IF EXISTS judge.reels;
