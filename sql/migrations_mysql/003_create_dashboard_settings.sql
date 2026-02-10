-- MySQL migration: create dashboard_settings
-- Target DB: moving_productiva (ejecutar con USE moving_productiva; antes del script)

CREATE TABLE IF NOT EXISTS dashboard_settings (
  id INT NOT NULL AUTO_INCREMENT,
  month DATE NOT NULL,
  business_days INT NOT NULL,
  installed_capacity_surtido INT NULL,
  installed_capacity_verificacion INT NULL,
  installed_capacity_embarques INT NULL,
  unit ENUM('pedidos','partidas') NOT NULL DEFAULT 'pedidos',
  holidays JSON NULL,
  thresholds JSON NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_dashboard_settings_month (month)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
