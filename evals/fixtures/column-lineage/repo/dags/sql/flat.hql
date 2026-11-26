INSERT OVERWRITE TABLE mart.flat SELECT region, name AS x FROM stage.customers;
