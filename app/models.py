from sqlalchemy import Column, Integer, String, DateTime
from database import Base
from datetime import datetime

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    yandex_email = Column(String, index=True)
    yandex_name = Column(String, index=True)
    yandex_token = Column(String, index=True)
    widget_link = Column(String, index=True)
    registered_at = Column(DateTime, default=datetime.now)
    last_login_at = Column(DateTime, default=datetime.now)

class Widget(Base):
    __tablename__ = "widgets"
    widget_link = Column(String, primary_key=True, index=True)
    style = Column(String, index=True)  
    font = Column(String, index=True)
    color = Column(String, index=True)