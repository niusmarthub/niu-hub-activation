ALTER TABLE niusmart_hub_cellular_usage_history
    ADD COLUMN interface_name VARCHAR(32) NOT NULL DEFAULT 'eth0',
    ADD KEY idx_site_interface_sampled (site_id, interface_name, sampled_at);
