# create_admin.py

from werkzeug.security import generate_password_hash

try:
    from app import app, db, User
except ModuleNotFoundError:
    from backend.app import app, db, User

# Ajusta estos valores:
email    = 'admin@gmail.com'
password = '951147'
empresa  = 'Administrador'
ubic     = 'Oficina Central'
telefono = '091640200'

with app.app_context():
    # Aquí ya estamos "dentro" de Flask
    if User.query.filter_by(email=email).first():
        print("Ese admin ya existe.")
    else:
        admin = User(
          email=email,
          password=generate_password_hash(password),
          role='admin',
          empresa=empresa,
          ubicacion=ubic,
          telefono=telefono
        )
        db.session.add(admin)
        db.session.commit()
        print("Admin creado correctamente.")
