"""
extensions.py
-------------
Holds shared Flask extension instances (db, login_manager, migrate, scheduler,
csrf) so that models/, routes/, and services/ can all import them without
causing circular imports with app.py.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from apscheduler.schedulers.background import BackgroundScheduler

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()
csrf = CSRFProtect()  # Gap #9: CSRF protection
scheduler = BackgroundScheduler()

login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"
