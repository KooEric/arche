-- A SELECT * over a table whose columns are unknown: only the columns before it have known places.
INSERT INTO mart.star_slots (slot_id, slot_rest, slot_tail)
SELECT s.id, s.*, s.kind FROM raw.slot_src s;

CREATE TABLE mart.star_union AS
SELECT s.*, s.kind FROM raw.slot_src s
UNION ALL
SELECT o.region FROM raw.slot_other o;
