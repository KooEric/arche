CREATE TABLE IF NOT EXISTS mart.revenue (customer_key BIGINT, net_amount DOUBLE, region STRING,
  size_class STRING, rank_in_region INT) PARTITIONED BY (dt STRING);
CREATE TABLE IF NOT EXISTS mart.flat (a STRING, y STRING);
