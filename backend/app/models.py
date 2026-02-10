from sqlalchemy import Column, Integer, String, DateTime, JSON, Enum, Date
from sqlalchemy.orm import declarative_base

Base = declarative_base()

# ORM sobre la vista (solo lectura)
class Dash(Base):
    __tablename__ = "vw_tablero_dash"
    pedido_cco = Column(String, primary_key=True)
    fecha_alta = Column(DateTime)
    ejecutiva = Column(String)
    tipo = Column(String)
    sucursal = Column(String)
    part_cco = Column(Integer)
    comentario_cco = Column(String)
    entrega_cco = Column(String)
    fecha_surtido = Column(DateTime)
    verificador = Column(String)
    fecha_verificacion = Column(DateTime)
    transportista = Column(String)
    unidad = Column(String)
    fecha_embarque = Column(DateTime)
    estado = Column(String)

class DashboardSettings(Base):
    __tablename__ = "dashboard_settings"
    id = Column(Integer, primary_key=True)
    month = Column(Date, unique=True, nullable=False)
    business_days = Column(Integer, nullable=False)
    installed_capacity_surtido = Column(Integer)
    installed_capacity_verificacion = Column(Integer)
    installed_capacity_embarques = Column(Integer)
    unit = Column(Enum('pedidos','partidas'), default='pedidos')
    holidays = Column(JSON)
    thresholds = Column(JSON)
