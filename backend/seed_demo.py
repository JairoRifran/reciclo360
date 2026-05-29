from datetime import date, datetime, timedelta

from werkzeug.security import generate_password_hash

try:
    from app import app, db, Plan, Solicitud, User
except ModuleNotFoundError:
    from backend.app import app, db, Plan, Solicitud, User


def upsert_user(email, **attrs):
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, password=generate_password_hash(attrs.pop("password")))
        db.session.add(user)
    else:
        password = attrs.pop("password", None)
        if password:
            user.password = generate_password_hash(password)

    for key, value in attrs.items():
        setattr(user, key, value)
    return user


with app.app_context():
    now = datetime.now
    plan_payg = Plan.query.filter_by(name="Pago por Uso").first()
    plan_starter = Plan.query.filter_by(name="Starter").first()

    admin = upsert_user(
        "admin@residuos360.test",
        password="demo1234",
        role="admin",
        empresa="Residuos 360 Admin",
        ubicacion="Montevideo",
        telefono="099000001",
        is_active=True,
    )

    institucional = upsert_user(
        "imm@residuos360.test",
        password="demo1234",
        role="institucional",
        empresa="IMM - Piloto institucional",
        ubicacion="Montevideo",
        telefono="099000010",
        institution_access_level="tecnico",
        is_active=True,
    )

    empresa = upsert_user(
        "empresa@residuos360.test",
        password="demo1234",
        role="empresa",
        empresa="Industria Delta S.A.",
        ubicacion="Montevideo",
        telefono="099000002",
        is_active=True,
        plan=plan_starter or plan_payg,
    )

    operador_1 = upsert_user(
        "operador1@residuos360.test",
        password="demo1234",
        role="gestor",
        empresa="Transportes Verde",
        ubicacion="Montevideo y Canelones",
        telefono="099000003",
        is_active=True,
        tarifa_por_kg=1.75,
        operador_habilitado=True,
        numero_habilitacion="DINACEA-TR-2026-015",
    )

    operador_2 = upsert_user(
        "operador2@residuos360.test",
        password="demo1234",
        role="gestor",
        empresa="Gestión Circular Este",
        ubicacion="Maldonado y Rocha",
        telefono="099000004",
        is_active=True,
        tarifa_por_kg=2.10,
        operador_habilitado=True,
        numero_habilitacion="DINACEA-TR-2026-028",
    )

    operador_oficial = upsert_user(
        "werba@residuos360.test",
        password="demo1234",
        role="gestor",
        empresa="WERBA SA",
        ubicacion="Montevideo",
        telefono="099000005",
        is_active=True,
        tarifa_por_kg=2.35,
        operador_habilitado=True,
        numero_habilitacion=None,
    )

    db.session.commit()

    if not Solicitud.query.filter_by(empresa=empresa.nombre_visible).first():
        db.session.add(
            Solicitud(
                empresa=empresa.nombre_visible,
                tipo="Valorizable",
                ubicacion="Montevideo",
                peso_estimado=120.0,
                precio_por_kg=operador_1.tarifa_por_kg,
                precio_estimado=120.0 * operador_1.tarifa_por_kg,
                precio_final=(120.0 * operador_1.tarifa_por_kg) * 1.10,
                fecha_retiro=date.today() + timedelta(days=2),
                estado="pendiente",
                estado_pago="cotizado",
                observaciones="Coordinar con depósito y retirar pallets mezclados.",
                owner=empresa,
                gestor_user=operador_1,
            )
        )
        db.session.add(
            Solicitud(
                empresa=empresa.nombre_visible,
                tipo="Peligroso",
                ubicacion="Canelones",
                peso_estimado=35.0,
                peso_real=32.0,
                precio_por_kg=operador_2.tarifa_por_kg,
                precio_estimado=35.0 * operador_2.tarifa_por_kg,
                precio_final=(32.0 * operador_2.tarifa_por_kg) * 1.10,
                fecha_retiro=date.today() - timedelta(days=1),
                aceptada_en=now() - timedelta(days=2),
                estado="aceptada",
                estado_pago="cotizado",
                destino_final=None,
                observaciones="Bidones con restos de solvente en área aislada.",
                owner=empresa,
                gestor_user=operador_2,
            )
        )
        db.session.add(
            Solicitud(
                empresa=empresa.nombre_visible,
                tipo="Reciclable mixto",
                ubicacion="Montevideo",
                peso_estimado=80.0,
                peso_real=78.5,
                precio_por_kg=operador_1.tarifa_por_kg,
                precio_estimado=80.0 * operador_1.tarifa_por_kg,
                precio_final=(78.5 * operador_1.tarifa_por_kg) * 1.10,
                fecha_retiro=date.today() - timedelta(days=5),
                aceptada_en=now() - timedelta(days=6),
                completada_en=now() - timedelta(days=5),
                estado="completada",
                estado_pago="ajustado",
                comprobante_peso="/static/uploads/demo_comprobante_peso.txt",
                evidencia_retiro="/static/uploads/demo_evidencia_retiro.txt",
                certificado_destino="/static/uploads/demo_certificado_destino.txt",
                destino_final="Centro de clasificación y valorización",
                observaciones="Servicio realizado con pesaje final y descarga controlada.",
                owner=empresa,
                gestor_user=operador_1,
            )
        )

    db.session.commit()
    print("Datos demo creados o actualizados.")
    print("Admin: admin@residuos360.test / demo1234")
    print("Empresa: empresa@residuos360.test / demo1234")
    print("Operador 1: operador1@residuos360.test / demo1234")
    print("Operador 2: operador2@residuos360.test / demo1234")
    print("IMM: imm@residuos360.test / demo1234")
