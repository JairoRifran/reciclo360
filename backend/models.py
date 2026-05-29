from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Plan(db.Model):
    __tablename__ = "plans"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    price_uyu = db.Column(db.Integer, nullable=False, default=0)
    limit_requests = db.Column(db.Integer)
    commission_rate = db.Column(db.Float, nullable=False, default=0.20)
    stripe_price = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "price_uyu": self.price_uyu,
            "limit_requests": self.limit_requests,
            "commission_rate": self.commission_rate,
            "stripe_price": self.stripe_price,
        }


class User(UserMixin, db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="empresa")
    empresa = db.Column(db.String(128))
    razon_social = db.Column(db.String(160))
    rut = db.Column(db.String(32))
    giro = db.Column(db.String(160))
    condicion_tributaria = db.Column(db.String(64))
    ubicacion = db.Column(db.String(128))
    direccion_base = db.Column(db.String(256))
    domicilio_fiscal = db.Column(db.String(256))
    contacto_nombre = db.Column(db.String(128))
    contacto_facturacion = db.Column(db.String(128))
    email_facturacion = db.Column(db.String(120))
    telefono = db.Column(db.String(32))
    notas_operativas = db.Column(db.Text)
    zonas_cobertura = db.Column(db.Text)
    radio_cobertura_km = db.Column(db.Float, default=25.0)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    tarifa_por_kg = db.Column(db.Float, nullable=False, default=0.0)
    moneda_tarifa = db.Column(db.String(8), nullable=False, default="UYU")
    tarifa_base_retiro_uyu = db.Column(db.Float, nullable=False, default=1500.0)
    tarifa_km_uyu = db.Column(db.Float, nullable=False, default=45.0)
    tarifa_m3_uyu = db.Column(db.Float, nullable=False, default=220.0)
    tarifa_tratamiento_kg_uyu = db.Column(db.Float, nullable=False, default=0.0)
    costo_certificado_uyu = db.Column(db.Float, nullable=False, default=350.0)
    operador_habilitado = db.Column(db.Boolean, default=False, nullable=False)
    numero_habilitacion = db.Column(db.String(64))
    verificado_ministerio = db.Column(db.Boolean, default=False, nullable=False)
    verificacion_fuente = db.Column(db.String(256))
    verificacion_actualizada_en = db.Column(db.DateTime)
    verificacion_observacion = db.Column(db.String(256))
    ministerio_rut = db.Column(db.String(32))
    ministerio_modalidad = db.Column(db.String(64))
    ministerio_estado = db.Column(db.String(128))
    ministerio_categorias = db.Column(db.String(128))
    categorias_residuo = db.Column(db.String(256))
    institution_access_level = db.Column(db.String(32))
    plan_id = db.Column(db.Integer, db.ForeignKey("plans.id"))
    stripe_subscription_id = db.Column(db.String(64))

    plan = db.relationship("Plan", backref="users")

    def __repr__(self):
        return f"<User {self.email}>"

    @property
    def nombre_visible(self):
        return self.empresa or self.email

    @property
    def rating_values(self):
        if self.role == "empresa":
            return [
                solicitud.empresa_rating
                for solicitud in self.solicitudes
                if getattr(solicitud, "empresa_rating", None)
            ]
        if self.role == "gestor":
            return [
                solicitud.operador_rating
                for solicitud in self.gestor_solicitudes
                if getattr(solicitud, "operador_rating", None)
            ]
        return []

    @property
    def rating_promedio(self):
        values = self.rating_values
        if not values:
            return None
        return round(sum(values) / len(values), 1)

    @property
    def rating_cantidad(self):
        return len(self.rating_values)

    @property
    def institution_label(self):
        labels = {
            "observador": "Observador institucional",
            "tecnico": "Tecnico / fiscalizacion",
            "admin_institucional": "Administrador institucional",
        }
        return labels.get(self.institution_access_level or "", "Usuario institucional")


class DeclaracionCumplimiento(db.Model):
    __tablename__ = "declaracion_cumplimiento"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    organismo = db.Column(db.String(32), nullable=False)
    periodo = db.Column(db.String(16), nullable=False)
    estado = db.Column(db.String(24), nullable=False, default="faltan_datos")
    firmante_nombre = db.Column(db.String(128))
    firmante_documento = db.Column(db.String(32))
    firmante_cargo = db.Column(db.String(128))
    apoderado_nombre = db.Column(db.String(128))
    apoderado_documento = db.Column(db.String(32))
    fecha_objetivo = db.Column(db.Date)
    fecha_presentacion = db.Column(db.Date)
    contrato_transporte = db.Column(db.String(256))
    formulario_borrador = db.Column(db.String(256))
    poder_documento = db.Column(db.String(256))
    respaldo_extra = db.Column(db.String(256))
    observaciones = db.Column(db.Text)
    creado = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    actualizado = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    user = db.relationship(
        "User",
        backref=db.backref("declaraciones_cumplimiento", lazy=True, cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<DeclaracionCumplimiento {self.organismo} {self.periodo} {self.estado}>"


class ContratoMarco(db.Model):
    __tablename__ = "contrato_marco"

    id = db.Column(db.Integer, primary_key=True)
    empresa_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    gestor_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    estado = db.Column(db.String(32), nullable=False, default="pendiente_datos")
    tipos_residuo = db.Column(db.Text)
    direcciones_cubiertas = db.Column(db.Text)
    vigencia_desde = db.Column(db.Date)
    vigencia_hasta = db.Column(db.Date)
    proveedor_firma = db.Column(db.String(64), default="firma_avanzada_externa")
    contrato_borrador = db.Column(db.String(256))
    contrato_firmado = db.Column(db.String(256))
    contrato_hash = db.Column(db.String(128))
    empresa_firmado_en = db.Column(db.DateTime)
    gestor_firmado_en = db.Column(db.DateTime)
    observaciones = db.Column(db.Text)
    creado = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    actualizado = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    empresa_user = db.relationship(
        "User",
        foreign_keys=[empresa_user_id],
        backref=db.backref("contratos_empresa", lazy=True, cascade="all, delete-orphan"),
    )
    gestor_user = db.relationship(
        "User",
        foreign_keys=[gestor_user_id],
        backref=db.backref("contratos_gestor", lazy=True, cascade="all, delete-orphan"),
    )

    @property
    def vigente(self):
        if self.estado != "vigente":
            return False
        today = datetime.utcnow().date()
        if self.vigencia_desde and self.vigencia_desde > today:
            return False
        if self.vigencia_hasta and self.vigencia_hasta < today:
            return False
        return True

    def __repr__(self):
        return f"<ContratoMarco {self.id} {self.estado}>"


class Solicitud(db.Model):
    __tablename__ = "solicitud"

    id = db.Column(db.Integer, primary_key=True)
    empresa = db.Column(db.String(128), nullable=False)
    tipo = db.Column(db.String(64), nullable=False)
    categoria_residuo = db.Column(db.String(64))
    nivel_riesgo = db.Column(db.String(32))
    requiere_certificado = db.Column(db.Boolean, default=False, nullable=False)
    ubicacion = db.Column(db.String(128), nullable=False)
    direccion_retiro = db.Column(db.String(256))
    volumen_estimado_m3 = db.Column(db.Float)
    distancia_km = db.Column(db.Float)
    peso_estimado = db.Column(db.Float, nullable=False)
    peso_real = db.Column(db.Float)
    alerta_peso = db.Column(db.Boolean, default=False, nullable=False)
    desvio_peso_pct = db.Column(db.Float)
    precio_por_kg = db.Column(db.Float, nullable=False)
    precio_estimado = db.Column(db.Float, nullable=False)
    precio_final = db.Column(db.Float)
    moneda = db.Column(db.String(8), nullable=False, default="UYU")
    iva_tasa = db.Column(db.Float, nullable=False, default=0.22)
    cfe_tipo_sugerido = db.Column(db.String(32), default="eFactura")
    monto_transporte_uyu = db.Column(db.Float)
    monto_gestion_uyu = db.Column(db.Float)
    monto_certificado_uyu = db.Column(db.Float)
    monto_plataforma_uyu = db.Column(db.Float)
    subtotal_neto_uyu = db.Column(db.Float)
    iva_uyu = db.Column(db.Float)
    total_uyu = db.Column(db.Float)
    liquidacion_transportista_uyu = db.Column(db.Float)
    liquidacion_gestor_uyu = db.Column(db.Float)
    liquidacion_plataforma_uyu = db.Column(db.Float)
    subtotal_final_uyu = db.Column(db.Float)
    iva_final_uyu = db.Column(db.Float)
    total_final_uyu = db.Column(db.Float)
    liquidacion_transportista_final_uyu = db.Column(db.Float)
    liquidacion_gestor_final_uyu = db.Column(db.Float)
    liquidacion_plataforma_final_uyu = db.Column(db.Float)
    ajuste_economico_uyu = db.Column(db.Float)
    estado_economico = db.Column(db.String(24), default="cotizado", nullable=False)
    fecha_retiro = db.Column(db.Date)
    franja_horaria = db.Column(db.String(64))
    programado_en = db.Column(db.DateTime)
    aceptada_en = db.Column(db.DateTime)
    retirada_en = db.Column(db.DateTime)
    completada_en = db.Column(db.DateTime)
    ultimo_recordatorio_estado_en = db.Column(db.DateTime)
    ultimo_recordatorio_documentos_en = db.Column(db.DateTime)
    empresa_rating = db.Column(db.Integer)
    empresa_rating_comment = db.Column(db.Text)
    empresa_rated_en = db.Column(db.DateTime)
    operador_rating = db.Column(db.Integer)
    operador_rating_comment = db.Column(db.Text)
    operador_rated_en = db.Column(db.DateTime)
    contrato_marco_id = db.Column(db.Integer, db.ForeignKey("contrato_marco.id"))
    orden_servicio_estado = db.Column(db.String(32), default="borrador", nullable=False)
    orden_servicio_documento = db.Column(db.String(256))
    orden_servicio_hash = db.Column(db.String(128))
    orden_servicio_version = db.Column(db.Integer, default=1, nullable=False)
    orden_empresa_confirmada_en = db.Column(db.DateTime)
    orden_transportista_confirmada_en = db.Column(db.DateTime)
    apta_declaracion = db.Column(db.Boolean, default=False, nullable=False)
    estado = db.Column(db.String(20), default="pendiente", nullable=False)
    estado_pago = db.Column(db.String(20), default="pendiente", nullable=False)
    comprobante_peso = db.Column(db.String(256))
    evidencia_retiro = db.Column(db.String(256))
    certificado_destino = db.Column(db.String(256))
    retiro_entregado_por = db.Column(db.String(128))
    retiro_recibido_por = db.Column(db.String(128))
    retiro_observaciones = db.Column(db.Text)
    vehiculo_matricula = db.Column(db.String(32))
    vehiculo_descripcion = db.Column(db.String(128))
    proveedor_rastreo = db.Column(db.String(128))
    destino_previsto = db.Column(db.String(256))
    destino_final = db.Column(db.String(256))
    observaciones = db.Column(db.Text)
    creado = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    gestor_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    owner = db.relationship(
        "User",
        foreign_keys=[user_id],
        backref=db.backref("solicitudes", lazy=True),
    )
    gestor_user = db.relationship(
        "User",
        foreign_keys=[gestor_id],
        backref=db.backref("gestor_solicitudes", lazy=True),
    )
    contrato_marco = db.relationship(
        "ContratoMarco",
        backref=db.backref("ordenes_servicio", lazy=True),
    )

    def __repr__(self):
        return f"<Solicitud {self.id} - {self.tipo} - {self.estado}>"

    @property
    def gestor_nombre(self):
        return self.gestor_user.nombre_visible if self.gestor_user else "-"

    @property
    def fecha_label(self):
        fecha = self.fecha_retiro or self.creado.date()
        return fecha.strftime("%d/%m/%Y")

    @property
    def programacion_label(self):
        if self.franja_horaria:
            return f"{self.fecha_label} · {self.franja_horaria}"
        return self.fecha_label

    @property
    def timeline_items(self):
        return sorted(self.eventos, key=lambda event: event.creado)

    @property
    def documentacion_faltante(self):
        faltantes = []
        if not self.comprobante_peso:
            faltantes.append("Comprobante de peso")
        if not self.evidencia_retiro:
            faltantes.append("Evidencia de retiro")
        if self.requiere_certificado and not self.certificado_destino:
            faltantes.append("Certificado de destino")
        return faltantes

    @property
    def documentacion_completa(self):
        return len(self.documentacion_faltante) == 0

    @property
    def contrato_vigente(self):
        return bool(self.contrato_marco and self.contrato_marco.vigente)

    @property
    def total_estimado_display(self):
        return self.total_uyu or self.precio_final or self.precio_estimado or 0

    @property
    def subtotal_estimado_display(self):
        return self.subtotal_neto_uyu or self.precio_estimado or 0

    @property
    def total_final_display(self):
        if self.total_final_uyu is not None:
            return self.total_final_uyu
        if self.estado == "completada":
            return self.precio_final or self.total_uyu or self.precio_estimado or 0
        return None

    @property
    def subtotal_final_display(self):
        if self.subtotal_final_uyu is not None:
            return self.subtotal_final_uyu
        if self.estado == "completada":
            return self.subtotal_neto_uyu or self.precio_estimado or 0
        return None

    @property
    def iva_final_display(self):
        if self.iva_final_uyu is not None:
            return self.iva_final_uyu
        if self.estado == "completada":
            return self.iva_uyu or 0
        return None

    @property
    def total_economico_vigente(self):
        return self.total_final_display if self.total_final_display is not None else self.total_estimado_display

    @property
    def estado_documental(self):
        if self.estado in {"rechazada", "cancelada"}:
            return "no_aplicable"
        if self.documentacion_completa:
            return "completo"
        if self.comprobante_peso or self.evidencia_retiro or self.certificado_destino:
            return "incompleto"
        if self.estado in {"aceptada", "retirada", "completada"}:
            return "pendiente"
        return "pendiente"

    @property
    def unidad_peso(self):
        return "kg"


class SolicitudEvento(db.Model):
    __tablename__ = "solicitud_evento"

    id = db.Column(db.Integer, primary_key=True)
    solicitud_id = db.Column(db.Integer, db.ForeignKey("solicitud.id"), nullable=False)
    actor_role = db.Column(db.String(20))
    actor_label = db.Column(db.String(128))
    evento = db.Column(db.String(64), nullable=False)
    detalle = db.Column(db.String(256))
    creado = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    solicitud = db.relationship(
        "Solicitud",
        backref=db.backref("eventos", lazy=True, cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<SolicitudEvento {self.solicitud_id} - {self.evento}>"


class ObservacionInstitucional(db.Model):
    __tablename__ = "observacion_institucional"

    id = db.Column(db.Integer, primary_key=True)
    solicitud_id = db.Column(db.Integer, db.ForeignKey("solicitud.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    tipo = db.Column(db.String(64), nullable=False, default="observacion_documental")
    estado = db.Column(db.String(32), nullable=False, default="abierta")
    detalle = db.Column(db.Text, nullable=False)
    creado = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    solicitud = db.relationship(
        "Solicitud",
        backref=db.backref("observaciones_institucionales", lazy=True, cascade="all, delete-orphan"),
    )
    user = db.relationship(
        "User",
        backref=db.backref("observaciones_institucionales", lazy=True),
    )

    def __repr__(self):
        return f"<ObservacionInstitucional {self.solicitud_id} - {self.tipo}>"


class AccessAuditLog(db.Model):
    __tablename__ = "access_audit_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    accion = db.Column(db.String(64), nullable=False)
    entidad = db.Column(db.String(64), nullable=False)
    entidad_id = db.Column(db.String(64))
    detalle = db.Column(db.String(256))
    ip = db.Column(db.String(64))
    creado = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship(
        "User",
        backref=db.backref("access_logs", lazy=True),
    )

    def __repr__(self):
        return f"<AccessAuditLog {self.user_id} - {self.accion}>"


class CargoExtra(db.Model):
    __tablename__ = "cargos_extra"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    solicitud_id = db.Column(db.Integer, db.ForeignKey("solicitud.id"))
    monto_usd = db.Column(db.Float, nullable=False)
    monto_uyu = db.Column(db.Float)
    creado = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="cargos_extra")
    solicitud = db.relationship("Solicitud", backref="cargos_extra")

    def __repr__(self):
        monto = self.monto_uyu if self.monto_uyu is not None else self.monto_usd
        moneda = "UYU" if self.monto_uyu is not None else "USD"
        return f"<CargoExtra {self.id} - {moneda} {monto}>"
