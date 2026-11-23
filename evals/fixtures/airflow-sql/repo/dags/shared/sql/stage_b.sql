DROP TABLE IF EXISTS tmp.stage;
CREATE TABLE tmp.stage AS SELECT * FROM shared.calendar;
INSERT OVERWRITE TABLE shared.days_b SELECT * FROM tmp.stage;
