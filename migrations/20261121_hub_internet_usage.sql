-- NiuSmart migration proposal; execute on the NiuSmart server after review.
-- Store cumulative hub counters with a unique reading ID for idempotent ingestion.
CREATE TABLE IF NOT EXISTS hub_internet_usage (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  site_id BIGINT UNSIGNED NOT NULL,
  hub_identifier VARCHAR(100) NOT NULL,
  reading_id CHAR(64) NOT NULL,
  observed_at DATETIME NOT NULL,
  interface_name VARCHAR(16) NOT NULL DEFAULT 'eth0',
  rx_bytes BIGINT UNSIGNED NOT NULL,
  tx_bytes BIGINT UNSIGNED NOT NULL,
  delta_rx_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
  delta_tx_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
  counter_reset TINYINT(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uq_hub_reading (hub_identifier, reading_id),
  KEY idx_site_observed (site_id, observed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
