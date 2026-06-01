import os
import json
import csv
import re
import hashlib
import textwrap
import unicodedata
import zipfile
from datetime import datetime
from difflib import SequenceMatcher
from functools import wraps
from io import BytesIO
from io import StringIO
from urllib.request import urlopen
from xml.etree import ElementTree as ET

from dotenv import load_dotenv
from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_mail import Mail, Message
from sqlalchemy import inspect, text
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from models import (
        AccessAuditLog,
        CargoExtra,
        ContratoMarco,
        DeclaracionCumplimiento,
        ObservacionInstitucional,
        Plan,
        Solicitud,
        SolicitudEvento,
        User,
        db,
    )
except ModuleNotFoundError:  # pragma: no cover
    from backend.models import (
        AccessAuditLog,
        CargoExtra,
        ContratoMarco,
        DeclaracionCumplimiento,
        ObservacionInstitucional,
        Plan,
        Solicitud,
        SolicitudEvento,
        User,
        db,
    )

try:
    import stripe
except Exception:  # pragma: no cover
    stripe = None

load_dotenv()

BASE_DIR = os.path.dirname(__file__)
template_dir = os.path.join(BASE_DIR, "../frontend/templates")
static_dir = os.path.join(BASE_DIR, "../frontend/static")
is_vercel = bool(os.getenv("VERCEL"))
if os.getenv("VERCEL") and not os.getenv("DATABASE_URL"):
    db_path = os.path.join("/tmp", "recicloco.db")
else:
    db_path = os.path.join(BASE_DIR, "instance", "recicloco.db")
os.makedirs(os.path.dirname(db_path), exist_ok=True)
uploads_dir = os.path.join("/tmp", "uploads") if is_vercel else os.path.join(static_dir, "uploads")
os.makedirs(uploads_dir, exist_ok=True)
ministerio_snapshot_path = (
    os.path.join("/tmp", "ministerio_transportistas_snapshot.json")
    if is_vercel
    else os.path.join(BASE_DIR, "instance", "ministerio_transportistas_snapshot.json")
)

MINISTERIO_TRANSPORTE_XLSX_URL = (
    "https://www.gub.uy/ministerio-ambiente/sites/ministerio-ambiente/files/2026-03/"
    "09032026%20-%20Empresas_transporte_residuos_1.xlsx"
)

DEFAULT_IVA_RATE = 0.22
DEFAULT_CFE_TYPE_B2B = "eFactura"
TIME_WINDOWS = [
    "08:00 - 10:00",
    "10:00 - 12:00",
    "12:00 - 14:00",
    "14:00 - 16:00",
    "16:00 - 18:00",
    "18:00 - 20:00",
]

DECLARATION_ENTITY_CONFIG = {
    "imm": {
        "label": "IMM Montevideo",
        "short_label": "IMM",
        "description": "Declaracion de gestion de residuos solidos no domiciliarios para habilitacion y seguimiento en Montevideo.",
        "expected_frequency": "Segun vigencia del tramite y actualizacion de la operativa declarada.",
        "official_format": "Formulario IMM con respaldo de contrato, firmante y documentacion operativa.",
        "required_documents": [
            "Contrato con transportista u operador",
            "Formulario o borrador IMM",
            "Documento del firmante o poder si corresponde",
            "Respaldo de retiros y destinos",
        ],
        "focus_states": {"retirada", "completada"},
    },
    "ministerio": {
        "label": "Ministerio de Ambiente",
        "short_label": "Ministerio",
        "description": "Declaracion jurada anual de residuos solidos industriales y asimilados para sujetos alcanzados por PGRS.",
        "expected_frequency": "Anual, por periodo calendario.",
        "official_format": "Carga anual compatible con SIA y respaldo consolidado del periodo.",
        "required_documents": [
            "Borrador de declaracion jurada",
            "Firmante y datos de presentacion",
            "Contratos y operadores utilizados",
            "Respaldo de retiros, peso y destino final",
        ],
        "focus_states": {"completada"},
    },
}

RESIDUE_CATALOG = [
    {
        "key": "sanitario_comun",
        "label": "Sanitario comun",
        "family": "sanitario",
        "risk": "medio",
        "requires_certificate": False,
        "description": "Residuos sanitarios comunes asimilables a domesticos.",
        "normative_hint": "Decreto 586/009",
    },
    {
        "key": "sanitario_infeccioso",
        "label": "Sanitario infeccioso",
        "family": "sanitario",
        "risk": "alto",
        "requires_certificate": True,
        "description": "Residuos clinicos, biologicos o con riesgo infeccioso.",
        "normative_hint": "Decreto 586/009",
    },
    {
        "key": "cortopunzante",
        "label": "Cortopunzante",
        "family": "sanitario",
        "risk": "alto",
        "requires_certificate": True,
        "description": "Agujas, bisturies y elementos punzantes o cortantes.",
        "normative_hint": "Decreto 586/009",
    },
    {
        "key": "farmaceutico",
        "label": "Farmaceutico",
        "family": "sanitario",
        "risk": "alto",
        "requires_certificate": True,
        "description": "Medicamentos vencidos, descartes farmacologicos y similares.",
        "normative_hint": "Decreto 586/009",
    },
    {
        "key": "patologico",
        "label": "Patologico",
        "family": "sanitario",
        "risk": "alto",
        "requires_certificate": True,
        "description": "Tejidos, organos u otros residuos patologicos.",
        "normative_hint": "Decreto 586/009",
    },
    {
        "key": "peligroso_industrial",
        "label": "Peligroso industrial",
        "family": "industrial",
        "risk": "alto",
        "requires_certificate": True,
        "description": "Residuos categoria I o II segun peligrosidad industrial.",
        "normative_hint": "Decreto 182/013",
    },
    {
        "key": "quimico",
        "label": "Quimico",
        "family": "industrial",
        "risk": "alto",
        "requires_certificate": True,
        "description": "Sustancias, reactivos, envases contaminados y mezclas quimicas.",
        "normative_hint": "Decreto 182/013",
    },
    {
        "key": "solventes_pinturas",
        "label": "Solventes y pinturas",
        "family": "industrial",
        "risk": "alto",
        "requires_certificate": True,
        "description": "Solventes, thinners, pinturas y residuos asociados.",
        "normative_hint": "Decreto 182/013",
    },
    {
        "key": "aceites_lubricantes",
        "label": "Aceites y lubricantes usados",
        "family": "industrial",
        "risk": "alto",
        "requires_certificate": True,
        "description": "Aceites minerales, lubricantes y filtros contaminados.",
        "normative_hint": "Decreto 182/013",
    },
    {
        "key": "baterias",
        "label": "Baterias y acumuladores",
        "family": "especial",
        "risk": "alto",
        "requires_certificate": True,
        "description": "Baterias industriales, de movilidad o almacenamiento de energia.",
        "normative_hint": "Decreto 227/025",
    },
    {
        "key": "raee",
        "label": "RAEE y componentes electricos",
        "family": "especial",
        "risk": "medio",
        "requires_certificate": True,
        "description": "Residuos de aparatos electricos y electronicos.",
        "normative_hint": "Especial",
    },
    {
        "key": "neumaticos",
        "label": "Neumaticos fuera de uso",
        "family": "especial",
        "risk": "medio",
        "requires_certificate": True,
        "description": "Neumaticos, cubiertas y camaras fuera de uso.",
        "normative_hint": "Decreto 358/015",
    },
    {
        "key": "envases",
        "label": "Envases y embalajes",
        "family": "especial",
        "risk": "medio",
        "requires_certificate": False,
        "description": "Envases posconsumo y embalajes de papel, metal, vidrio o plastico.",
        "normative_hint": "Decreto 260/007",
    },
    {
        "key": "organico",
        "label": "Organico",
        "family": "no_domiciliario",
        "risk": "medio",
        "requires_certificate": False,
        "description": "Residuos humedos, alimenticios o compostables.",
        "normative_hint": "Gestion no domiciliaria",
    },
    {
        "key": "poda_jardin",
        "label": "Poda y jardin",
        "family": "no_domiciliario",
        "risk": "bajo",
        "requires_certificate": False,
        "description": "Ramas, cesped, hojas y restos verdes.",
        "normative_hint": "Gestion no domiciliaria",
    },
    {
        "key": "papel_carton",
        "label": "Papel y carton",
        "family": "valorizable",
        "risk": "bajo",
        "requires_certificate": False,
        "description": "Papel blanco, carton y corrugado limpio.",
        "normative_hint": "Valorizable",
    },
    {
        "key": "plasticos",
        "label": "Plasticos",
        "family": "valorizable",
        "risk": "bajo",
        "requires_certificate": False,
        "description": "Plasticos recuperables segregados.",
        "normative_hint": "Valorizable",
    },
    {
        "key": "vidrio",
        "label": "Vidrio",
        "family": "valorizable",
        "risk": "bajo",
        "requires_certificate": False,
        "description": "Vidrio recuperable no contaminado.",
        "normative_hint": "Valorizable",
    },
    {
        "key": "metales",
        "label": "Metales y chatarra",
        "family": "valorizable",
        "risk": "bajo",
        "requires_certificate": False,
        "description": "Ferrosos y no ferrosos segregados.",
        "normative_hint": "Valorizable",
    },
    {
        "key": "mixto_valorizable",
        "label": "Reciclable mixto",
        "family": "valorizable",
        "risk": "bajo",
        "requires_certificate": False,
        "description": "Mezcla de fracciones reciclables no peligrosas.",
        "normative_hint": "Valorizable",
    },
    {
        "key": "roc_limpio",
        "label": "Construccion y demolicion limpio",
        "family": "construccion",
        "risk": "medio",
        "requires_certificate": True,
        "description": "Escombros, hormigon, ceramicos y otros ROCs limpios.",
        "normative_hint": "Decreto 213/025",
    },
    {
        "key": "roc_mezclado",
        "label": "Construccion y demolicion mezclado",
        "family": "construccion",
        "risk": "medio",
        "requires_certificate": True,
        "description": "ROCs con mezclas de materiales y segregacion incompleta.",
        "normative_hint": "Decreto 213/025",
    },
    {
        "key": "voluminoso",
        "label": "Voluminoso no domiciliario",
        "family": "no_domiciliario",
        "risk": "medio",
        "requires_certificate": False,
        "description": "Mobiliario, descartes grandes y residuos de gran volumen.",
        "normative_hint": "Gestion no domiciliaria",
    },
]

app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
app.secret_key = os.getenv("SECRET_KEY", "cambia_esto_por_una_clave_segura")

database_url = os.getenv("DATABASE_URL")
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url or f"sqlite:///{db_path}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config.update(
    MAIL_SERVER="smtp.gmail.com",
    MAIL_PORT=587,
    MAIL_USE_TLS=True,
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
)

db.init_app(app)
mail = Mail(app)

if stripe:
    stripe.api_key = os.getenv("STRIPE_API_KEY")


def stripe_enabled():
    return bool(stripe and os.getenv("STRIPE_API_KEY"))


def save_uploaded_file(file_storage, sid, label):
    if not file_storage or not file_storage.filename:
        return None

    _, ext = os.path.splitext(file_storage.filename)
    ext = ext.lower() or ".bin"
    filename = f"solicitud_{sid}_{label}_{int(datetime.utcnow().timestamp())}{ext}"
    target_path = os.path.join(uploads_dir, filename)
    file_storage.save(target_path)
    return url_for("static", filename=f"uploads/{filename}")


def normalize_company_name(value):
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.upper()
    normalized = re.sub(r"[^A-Z0-9]+", " ", normalized)
    normalized = re.sub(r"\bS A\b", " SA ", normalized)
    normalized = re.sub(r"\bS R L\b", " SRL ", normalized)
    normalized = re.sub(r"\bL T D A\b", " LTDA ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    removable_suffixes = {"SA", "SRL", "LTDA", "SAS"}
    tokens = [token for token in normalized.split(" ") if token]
    while tokens and tokens[-1] in removable_suffixes:
        tokens.pop()
    return " ".join(tokens)


def normalize_rut(value):
    if not value:
        return ""
    return re.sub(r"\D+", "", value)


def read_xlsx_rows(binary_content):
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    workbook = zipfile.ZipFile(BytesIO(binary_content))

    shared_strings = []
    if "xl/sharedStrings.xml" in workbook.namelist():
        shared_root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
        for si in shared_root.findall("main:si", namespace):
            parts = [node.text or "" for node in si.findall(".//main:t", namespace)]
            shared_strings.append("".join(parts))

    sheet_name = "xl/worksheets/sheet1.xml"
    root = ET.fromstring(workbook.read(sheet_name))
    rows = []

    for row in root.findall(".//main:sheetData/main:row", namespace):
        values = []
        for cell in row.findall("main:c", namespace):
            raw_value = cell.find("main:v", namespace)
            value = raw_value.text if raw_value is not None else ""
            if cell.attrib.get("t") == "s" and value:
                value = shared_strings[int(value)]
            values.append((cell.attrib.get("r", ""), value))
        rows.append(values)

    return rows


def xlsx_rows_to_records(rows):
    records = []
    current = None

    for row in rows:
        values = [value.strip() for _, value in row if value and value.strip()]
        if not values:
            continue

        joined = " | ".join(values)
        if "RAZ" in joined.upper() and "NOMBRE" in joined.upper():
            continue

        if len(values) >= 3:
            detail = " | ".join(values[2:])
            current = {
                "razon_social": values[0],
                "nombre_comercial": values[1] if len(values) > 1 else values[0],
                "detalle": detail,
            }
            current.update(parse_ministerio_detail(detail))
            records.append(current)
        elif current:
            current["detalle"] = f"{current['detalle']} | {' | '.join(values)}"
            current.update(parse_ministerio_detail(current["detalle"]))

    return records


def parse_ministerio_detail(detail):
    parts = [part.strip() for part in (detail or "").split("|") if part.strip()]
    rut = ""
    modalidad = ""
    estado = ""
    categorias = ""

    for part in parts:
        compact = re.sub(r"\s+", "", part)
        if not rut and compact.isdigit() and len(compact) >= 8:
            rut = compact
            continue

        upper = normalize_company_name(part)
        if not modalidad and ("TERCEROS" in upper or "AUTOTRANSPORTE" in upper):
            modalidad = part
            continue

        if not categorias and "CAT" in upper:
            categorias = part
            continue

        if not estado and any(
            token in upper
            for token in ("HABILIT", "RENOVACION", "TRAMITE", "BAJA", "SUSPEND")
        ):
            estado = part

    return {
        "rut": rut,
        "modalidad": modalidad,
        "estado_habilitacion": estado,
        "categorias": categorias,
    }


def write_ministerio_snapshot(records, sync_result):
    payload = {
        "synced_at": datetime.utcnow().isoformat(),
        "source_url": MINISTERIO_TRANSPORTE_XLSX_URL,
        "record_count": len(records),
        "result": sync_result,
        "records": records,
    }
    with open(ministerio_snapshot_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def read_ministerio_snapshot_summary():
    if not os.path.exists(ministerio_snapshot_path):
        return {
            "synced_at": None,
            "record_count": 0,
            "matched": 0,
            "total": 0,
            "source_url": MINISTERIO_TRANSPORTE_XLSX_URL,
        }

    with open(ministerio_snapshot_path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    result = payload.get("result", {})
    return {
        "synced_at": payload.get("synced_at"),
        "record_count": payload.get("record_count", 0),
        "matched": result.get("matched", 0),
        "total": result.get("total", 0),
        "source_url": payload.get("source_url", MINISTERIO_TRANSPORTE_XLSX_URL),
    }


def read_ministerio_snapshot_records():
    if not os.path.exists(ministerio_snapshot_path):
        return []

    with open(ministerio_snapshot_path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    records = payload.get("records", [])
    enriched = []
    for record in records:
        if "rut" not in record or "categorias" not in record:
            enriched_record = dict(record)
            enriched_record.update(parse_ministerio_detail(record.get("detalle", "")))
            enriched.append(enriched_record)
        else:
            enriched.append(record)
    return enriched


def ministerio_unregistered_suggestions(limit=12):
    records = read_ministerio_snapshot_records()
    if not records:
        return []

    existing_keys = set()
    for gestor in User.query.filter_by(role="gestor").all():
        for candidate in (gestor.empresa, gestor.nombre_visible):
            key = normalize_company_name(candidate)
            if key:
                existing_keys.add(key)

    suggestions = []
    seen = set()
    for record in records:
        keys = {
            normalize_company_name(record.get("razon_social")),
            normalize_company_name(record.get("nombre_comercial")),
        }
        keys.discard("")
        if not keys or keys & existing_keys:
            continue

        canonical_key = sorted(keys)[0]
        if canonical_key in seen:
            continue

        seen.add(canonical_key)
        suggestions.append(record)
        if len(suggestions) >= limit:
            break

    return suggestions


def build_placeholder_operator_email(name):
    slug = normalize_company_name(name).lower().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9-]+", "", slug).strip("-") or "operador"
    base_email = f"{slug}@ministerio-pendiente.local"
    if not User.query.filter_by(email=base_email).first():
        return base_email

    suffix = 2
    while True:
        candidate = f"{slug}-{suffix}@ministerio-pendiente.local"
        if not User.query.filter_by(email=candidate).first():
            return candidate
        suffix += 1


def residue_options():
    return RESIDUE_CATALOG


def residue_option_map():
    return {item["key"]: item for item in RESIDUE_CATALOG}


def get_residue_option(key):
    return residue_option_map().get(key)


def grouped_residue_catalog():
    labels = {
        "sanitario": "Sanitarios",
        "industrial": "Industriales",
        "especial": "Especiales",
        "valorizable": "Valorizables",
        "no_domiciliario": "No domiciliarios",
        "construccion": "Construccion",
    }
    grouped = []
    seen = set()
    for item in RESIDUE_CATALOG:
        family = item["family"]
        if family in seen:
            continue
        seen.add(family)
        grouped.append(
            {
                "family": family,
                "label": labels.get(family, family.capitalize()),
                "items": [entry for entry in RESIDUE_CATALOG if entry["family"] == family],
            }
        )
    return grouped


def parse_operator_categories(values):
    if isinstance(values, str):
        raw_values = [item.strip() for item in values.split(",") if item.strip()]
    else:
        raw_values = []
        for item in values or []:
            text = (item or "").strip()
            if text:
                raw_values.append(text)

    valid = set(residue_option_map().keys())
    parsed = []
    for item in raw_values:
        if item in valid and item not in parsed:
            parsed.append(item)
    return parsed


def inferred_operator_capabilities(gestor):
    manual = {
        token.strip().lower()
        for token in (gestor.categorias_residuo or "").split(",")
        if token.strip()
    }
    if manual:
        return manual

    categorias = normalize_company_name(gestor.ministerio_categorias or "")
    base_low = {
        "organico",
        "poda_jardin",
        "papel_carton",
        "plasticos",
        "vidrio",
        "metales",
        "mixto_valorizable",
        "envases",
        "voluminoso",
        "roc_limpio",
    }
    if "II" in categorias:
        return base_low | {
            "peligroso_industrial",
            "quimico",
            "solventes_pinturas",
            "aceites_lubricantes",
            "baterias",
            "raee",
            "neumaticos",
            "roc_mezclado",
        }
    if "I" in categorias:
        return base_low
    return {"organico", "papel_carton", "plasticos", "mixto_valorizable"}


def operator_can_handle_residue(gestor, residue_key):
    if not residue_key:
        return True
    return residue_key in inferred_operator_capabilities(gestor)


def gestor_tariff_config(gestor):
    return {
        "currency": gestor.moneda_tarifa or "UYU",
        "base_retiro": (
            gestor.tarifa_base_retiro_uyu
            if getattr(gestor, "tarifa_base_retiro_uyu", None) not in (None, 0)
            else 1500.0
        ),
        "tarifa_km": (
            gestor.tarifa_km_uyu
            if getattr(gestor, "tarifa_km_uyu", None) not in (None, 0)
            else 45.0
        ),
        "tarifa_m3": (
            gestor.tarifa_m3_uyu
            if getattr(gestor, "tarifa_m3_uyu", None) not in (None, 0)
            else 220.0
        ),
        "tarifa_peso": (gestor.tarifa_por_kg or 0.0),
        "tarifa_tratamiento_kg": getattr(gestor, "tarifa_tratamiento_kg_uyu", 0.0) or 0.0,
        "costo_certificado": (
            gestor.costo_certificado_uyu
            if getattr(gestor, "costo_certificado_uyu", None) not in (None, 0)
            else 350.0
        ),
    }


def quote_service_breakdown(
    gestor,
    residue,
    peso_estimado,
    volumen_estimado_m3=0.0,
    distancia_km=0.0,
    commission_rate=0.20,
):
    config = gestor_tariff_config(gestor)
    peso_estimado = max(float(peso_estimado or 0), 0)
    volumen_estimado_m3 = max(float(volumen_estimado_m3 or 0), 0)
    distancia_km = max(float(distancia_km or 0), 0)

    transporte_base = round(config["base_retiro"], 2)
    transporte_km = round(distancia_km * config["tarifa_km"], 2)
    if volumen_estimado_m3 > 0:
        transporte_carga = round(volumen_estimado_m3 * config["tarifa_m3"], 2)
    else:
        tarifa_peso = config["tarifa_peso"] if config["tarifa_peso"] > 0 else 6.0
        transporte_carga = round(peso_estimado * tarifa_peso, 2)

    transporte_total = round(transporte_base + transporte_km + transporte_carga, 2)
    gestion_total = round(peso_estimado * config["tarifa_tratamiento_kg"], 2)
    certificado_total = round(
        config["costo_certificado"] if residue.get("requires_certificate") else 0.0,
        2,
    )
    plataforma_total = round((transporte_total + gestion_total + certificado_total) * commission_rate, 2)
    subtotal_neto = round(transporte_total + gestion_total + certificado_total + plataforma_total, 2)
    iva = round(subtotal_neto * DEFAULT_IVA_RATE, 2)
    total = round(subtotal_neto + iva, 2)

    return {
        "currency": config["currency"],
        "transport_base": transporte_base,
        "transport_distance": transporte_km,
        "transport_load": transporte_carga,
        "transport_total": transporte_total,
        "management_total": gestion_total,
        "certificate_total": certificado_total,
        "platform_fee": plataforma_total,
        "subtotal_net": subtotal_neto,
        "iva_rate": DEFAULT_IVA_RATE,
        "iva_amount": iva,
        "total": total,
        "liquidation_transport": transporte_total,
        "liquidation_operator": round(gestion_total + certificado_total, 2),
        "liquidation_platform": plataforma_total,
    }


def user_fiscal_completion(user):
    required = [
        user.razon_social or user.empresa,
        user.rut,
        user.condicion_tributaria,
        user.domicilio_fiscal or user.direccion_base,
        user.contacto_facturacion or user.contacto_nombre,
        user.email_facturacion or user.email,
    ]
    complete = sum(1 for item in required if item)
    return round((complete / len(required)) * 100)


def declaration_period_label():
    return str(datetime.utcnow().year)


def get_company_declaration(user, organismo, periodo=None):
    target_period = periodo or declaration_period_label()
    declaration = DeclaracionCumplimiento.query.filter_by(
        user_id=user.id,
        organismo=organismo,
        periodo=target_period,
    ).first()
    if declaration:
        return declaration
    declaration = DeclaracionCumplimiento(
        user_id=user.id,
        organismo=organismo,
        periodo=target_period,
        firmante_nombre=user.contacto_nombre or user.contacto_facturacion or user.nombre_visible,
        firmante_cargo="Responsable operativo",
    )
    db.session.add(declaration)
    db.session.commit()
    return declaration


def declaration_state_label(value):
    labels = {
        "faltan_datos": "Faltan datos",
        "borrador_listo": "Borrador listo",
        "listo_para_presentar": "Listo para presentar",
        "presentado": "Presentado",
        "observado": "Observado",
        "vencido": "Vencido",
    }
    return labels.get(value, value.replace("_", " ").capitalize())


def summarize_declaration_checklist(user, declaration, organismo):
    config = DECLARATION_ENTITY_CONFIG[organismo]
    solicitudes = [s for s in user.solicitudes if s.estado in config["focus_states"]]
    completed = [s for s in solicitudes if s.estado == "completada"]
    verified_ops = [s for s in solicitudes if s.gestor_user]
    operator_ok = bool(verified_ops) and all(
        (s.gestor_user.verificado_ministerio or s.gestor_user.operador_habilitado) for s in verified_ops
    )
    documented = sum(1 for s in completed if s.documentacion_completa)
    checklist = [
        {
            "label": "Perfil fiscal completo",
            "ok": user_fiscal_completion(user) >= 80,
            "detail": f"Perfil actual: {user_fiscal_completion(user)}% completo.",
        },
        {
            "label": "Firmante identificado",
            "ok": bool(declaration.firmante_nombre and declaration.firmante_documento),
            "detail": "Nombre, documento y cargo del firmante o responsable.",
        },
        {
            "label": "Contrato o respaldo de operador",
            "ok": bool(declaration.contrato_transporte),
            "detail": "Sube contrato, acuerdo marco o respaldo equivalente.",
        },
        {
            "label": "Operadores compatibles y verificados",
            "ok": operator_ok,
            "detail": f"{len(verified_ops)} operador(es) usados en el periodo con verificacion revisada.",
        },
        {
            "label": "Respaldo operativo conciliado",
            "ok": len(completed) == 0 or documented == len(completed),
            "detail": f"{documented}/{len(completed)} cierres completados con legajo documental completo.",
        },
    ]
    if organismo == "imm":
        checklist.append(
            {
                "label": "Formulario IMM preparado",
                "ok": bool(declaration.formulario_borrador),
                "detail": "Adjunta el formulario, borrador o caratula lista para presentar.",
            }
        )
    else:
        checklist.append(
            {
                "label": "Borrador anual para Ministerio",
                "ok": bool(declaration.formulario_borrador),
                "detail": "Adjunta la DJ anual o planilla preparada para carga en SIA.",
            }
        )
    return checklist


def company_declaration_context(user, periodo=None):
    current_period = periodo or declaration_period_label()
    declaraciones = []
    all_requests = list(user.solicitudes)
    for organismo in ("imm", "ministerio"):
        declaration = get_company_declaration(user, organismo, current_period)
        config = DECLARATION_ENTITY_CONFIG[organismo]
        scoped_requests = [s for s in all_requests if s.estado in config["focus_states"]]
        if organismo == "ministerio":
            scoped_requests = [
                s for s in scoped_requests if (s.categoria_residuo or "") not in {"envases_embalajes", "organico"}
            ]
        total_weight = round(sum((s.peso_real or s.peso_estimado or 0) for s in scoped_requests), 2)
        total_amount = round(sum((s.total_economico_vigente or 0) for s in scoped_requests), 2)
        checklist = summarize_declaration_checklist(user, declaration, organismo)
        ready_count = sum(1 for item in checklist if item["ok"])
        pending_docs = [
            doc
            for doc in [
                ("Contrato", declaration.contrato_transporte),
                ("Formulario", declaration.formulario_borrador),
                ("Poder/Firmante", declaration.poder_documento),
                ("Respaldo extra", declaration.respaldo_extra),
            ]
            if not doc[1]
        ]
        declaraciones.append(
            {
                "organismo": organismo,
                "config": config,
                "record": declaration,
                "state_label": declaration_state_label(declaration.estado),
                "checklist": checklist,
                "checklist_ready": ready_count,
                "checklist_total": len(checklist),
                "pending_docs": pending_docs,
                "requests_count": len(scoped_requests),
                "documented_count": sum(1 for s in scoped_requests if s.documentacion_completa),
                "operators_count": len({s.gestor_id for s in scoped_requests if s.gestor_id}),
                "total_weight": total_weight,
                "total_amount": total_amount,
                "files": [
                    ("Contrato", declaration.contrato_transporte),
                    ("Formulario", declaration.formulario_borrador),
                    ("Poder/Firmante", declaration.poder_documento),
                    ("Respaldo extra", declaration.respaldo_extra),
                ],
            }
        )
    summary = {
        "ready": sum(1 for item in declaraciones if item["record"].estado == "listo_para_presentar"),
        "presented": sum(1 for item in declaraciones if item["record"].estado == "presentado"),
        "pending": sum(1 for item in declaraciones if item["record"].estado in {"faltan_datos", "observado", "vencido"}),
        "reconciled": sum(1 for item in declaraciones if item["checklist_ready"] == item["checklist_total"]),
    }
    return {
        "periodo_actual": current_period,
        "declaraciones": declaraciones,
        "declaration_summary": summary,
    }


def contract_state_label(value):
    if not value:
        value = "pendiente_datos"
    labels = {
        "pendiente_datos": "Pendiente de datos",
        "pendiente_firma_empresa": "Pendiente firma empresa",
        "pendiente_firma_transportista": "Pendiente firma transportista",
        "vigente": "Vigente",
        "por_vencer": "Por vencer",
        "vencido": "Vencido",
        "rechazado": "Rechazado",
        "rescindido": "Rescindido",
    }
    return labels.get(value, value.replace("_", " ").capitalize())


def order_state_label(value):
    if not value:
        value = "borrador"
    labels = {
        "borrador": "Borrador",
        "pendiente_de_contrato": "Pendiente de contrato",
        "pendiente_firma_empresa": "Pendiente firma empresa",
        "pendiente_aceptacion_transportista": "Pendiente aceptacion del transportista",
        "confirmada": "Confirmada",
        "programada": "Programada",
        "en_retiro": "En retiro",
        "retirado": "Retirado",
        "pendiente_documentacion": "Pendiente de documentacion",
        "cerrado": "Cerrado",
        "observado": "Observado",
        "cancelado": "Cancelado",
    }
    return labels.get(value, value.replace("_", " ").capitalize())


def economic_state_label(value):
    if not value:
        value = "cotizado"
    labels = {
        "cotizado": "Cotizacion estimada",
        "confirmado": "Confirmado por operador",
        "retirado": "Retiro realizado",
        "facturable": "Listo para facturar",
        "facturado": "Facturado",
        "cobrado": "Cobrado",
        "liquidado": "Liquidado",
        "cancelado": "Cancelado",
    }
    return labels.get(value, value.replace("_", " ").capitalize())


def contract_document_hash(*parts):
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_contract_between(empresa_user, gestor_user):
    if not empresa_user or not gestor_user:
        return None
    return (
        ContratoMarco.query.filter_by(empresa_user_id=empresa_user.id, gestor_user_id=gestor_user.id)
        .order_by(ContratoMarco.actualizado.desc())
        .first()
    )


def ensure_contract_for_pair(empresa_user, gestor_user):
    contract = get_contract_between(empresa_user, gestor_user)
    if contract:
        return contract
    contract = ContratoMarco(
        empresa_user_id=empresa_user.id,
        gestor_user_id=gestor_user.id,
        estado="pendiente_firma_empresa",
        tipos_residuo="",
        direcciones_cubiertas=empresa_user.direccion_base or "",
        vigencia_desde=datetime.utcnow().date(),
        vigencia_hasta=datetime.utcnow().date().replace(year=datetime.utcnow().year + 1),
        contrato_hash=contract_document_hash(
            empresa_user.rut,
            gestor_user.rut or gestor_user.ministerio_rut,
            empresa_user.direccion_base,
            gestor_user.numero_habilitacion,
        ),
    )
    db.session.add(contract)
    db.session.flush()
    return contract


def sync_contract_expiry_state(contract):
    if not contract:
        return None
    today = datetime.utcnow().date()
    if contract.estado == "vigente":
        if contract.vigencia_hasta and contract.vigencia_hasta < today:
            contract.estado = "vencido"
        elif contract.vigencia_hasta and (contract.vigencia_hasta - today).days <= 30:
            contract.estado = "por_vencer"
    return contract.estado


def contract_summary_context(user):
    contracts = []
    if user.role == "empresa":
        contracts = (
            ContratoMarco.query.filter_by(empresa_user_id=user.id)
            .order_by(ContratoMarco.actualizado.desc())
            .all()
        )
    elif user.role == "gestor":
        contracts = (
            ContratoMarco.query.filter_by(gestor_user_id=user.id)
            .order_by(ContratoMarco.actualizado.desc())
            .all()
        )
    for contract in contracts:
        sync_contract_expiry_state(contract)
    db.session.commit()
    summary = {
        "total": len(contracts),
        "vigentes": sum(1 for item in contracts if item.estado == "vigente"),
        "pendientes": sum(1 for item in contracts if item.estado in {"pendiente_datos", "pendiente_firma_empresa", "pendiente_firma_transportista"}),
        "vencidos": sum(1 for item in contracts if item.estado in {"por_vencer", "vencido"}),
    }
    return {"contracts": contracts, "contract_summary": summary}

def coverage_tokens(value):
    normalized = normalize_company_name(value)
    if not normalized:
        return set()
    return {token for token in normalized.split(" ") if len(token) >= 3}


def operador_cobertura_labels(gestor):
    labels = [gestor.ubicacion or ""]
    if gestor.zonas_cobertura:
        labels.extend(part.strip() for part in gestor.zonas_cobertura.split(",") if part.strip())
    return [label for label in labels if label]


def log_solicitud_event(solicitud, evento, detalle="", actor=None):
    actor_role = actor.role if actor else None
    actor_label = actor.nombre_visible if actor else "Sistema"
    db.session.add(
        SolicitudEvento(
            solicitud=solicitud,
            actor_role=actor_role,
            actor_label=actor_label,
            evento=evento,
            detalle=detalle or None,
        )
    )


def build_timeline(solicitud):
    labels = {
        "creada": "Solicitud creada",
        "asignada": "Operador asignado",
        "aceptada": "Operador acepto el retiro",
        "retirada": "Retiro realizado",
        "rechazada": "Operador rechazo el retiro",
        "completada": "Servicio completado",
        "documentacion_actualizada": "Documentacion actualizada",
        "observacion_institucional": "Observacion institucional",
        "perfil_actualizado": "Perfil actualizado",
        "activado_admin": "Operador activado",
        "categorias_actualizadas": "Categorias actualizadas",
    }
    items = []
    for event in sorted(solicitud.eventos, key=lambda item: item.creado):
        items.append(
            {
                "label": labels.get(event.evento, event.evento.replace("_", " ").capitalize()),
                "detail": event.detalle or "",
                "actor": event.actor_label or "Sistema",
                "created_at": event.creado.strftime("%d/%m/%Y %H:%M"),
            }
        )
    if items:
        return items

    items.append(
        {
            "label": "Solicitud creada",
            "detail": f"Retiro cargado para {solicitud.ubicacion}.",
            "actor": solicitud.owner.nombre_visible if solicitud.owner else "Empresa",
            "created_at": solicitud.creado.strftime("%d/%m/%Y %H:%M"),
        }
    )
    if solicitud.aceptada_en:
        items.append(
            {
                "label": "Operador acepto el retiro",
                "detail": solicitud.gestor_nombre,
                "actor": solicitud.gestor_nombre,
                "created_at": solicitud.aceptada_en.strftime("%d/%m/%Y %H:%M"),
            }
        )
    if solicitud.retirada_en:
        retiro_detail_parts = [solicitud.direccion_retiro or solicitud.ubicacion]
        if solicitud.retiro_entregado_por:
            retiro_detail_parts.append(f"Entrego: {solicitud.retiro_entregado_por}")
        if solicitud.retiro_recibido_por:
            retiro_detail_parts.append(f"Recibio: {solicitud.retiro_recibido_por}")
        items.append(
            {
                "label": "Retiro realizado",
                "detail": " · ".join(retiro_detail_parts),
                "actor": solicitud.gestor_nombre,
                "created_at": solicitud.retirada_en.strftime("%d/%m/%Y %H:%M"),
            }
        )
    if solicitud.completada_en:
        items.append(
            {
                "label": "Servicio completado",
                "detail": f"Peso real: {solicitud.peso_real or solicitud.peso_estimado:.2f} kg.",
                "actor": solicitud.gestor_nombre,
                "created_at": solicitud.completada_en.strftime("%d/%m/%Y %H:%M"),
            }
        )
    return items


def notify_solicitud_event(solicitud, event_key, detail=""):
    recipients = []
    if solicitud.owner:
        recipients.append(solicitud.owner.email)
        if solicitud.owner.email_facturacion and solicitud.owner.email_facturacion not in recipients:
            recipients.append(solicitud.owner.email_facturacion)
    if solicitud.gestor_user and event_key == "completada":
        if solicitud.gestor_user.email not in recipients:
            recipients.append(solicitud.gestor_user.email)

    if not recipients:
        return

    titles = {
        "aceptada": f"Solicitud #{solicitud.id} aceptada",
        "retirada": f"Solicitud #{solicitud.id} retirada",
        "rechazada": f"Solicitud #{solicitud.id} rechazada",
        "completada": f"Solicitud #{solicitud.id} completada",
        "documentacion": f"Documentacion actualizada en solicitud #{solicitud.id}",
    }
    next_steps = {
        "aceptada": "El operador ya confirmo el retiro. Ahora queda ejecutar el servicio y cerrar con respaldo.",
        "retirada": "El retiro ya fue realizado. Falta cargar peso final, destino y documentos de cierre.",
        "rechazada": "La solicitud necesita una nueva asignacion o una revision operativa.",
        "completada": "Revisa el peso final, el destino informado y descarga el respaldo documental.",
        "documentacion": "Ya podes entrar al centro documental para revisar los archivos cargados.",
    }
    status_map = {
        "aceptada": "Aceptada",
        "retirada": "Retirada",
        "rechazada": "Rechazada",
        "completada": "Completada",
        "documentacion": "Documentacion actualizada",
    }

    html = render_template(
        "emails/evento_solicitud.html",
        solicitud=solicitud,
        event_title=titles.get(event_key, f"Actualizacion de solicitud #{solicitud.id}"),
        event_status=status_map.get(event_key, "Actualizada"),
        event_detail=detail,
        next_step=next_steps.get(event_key, "Ingresa a la plataforma para revisar el detalle."),
    )
    for recipient in recipients:
        enviar_email(titles.get(event_key, f"Actualizacion de solicitud #{solicitud.id}"), recipient, html)


def maybe_send_operational_reminders(user):
    now = datetime.utcnow()
    touched = False
    solicitudes = []
    if user.role == "empresa":
        solicitudes = list(user.solicitudes)
    elif user.role == "gestor":
        solicitudes = list(user.gestor_solicitudes)
    else:
        return

    for solicitud in solicitudes:
        if solicitud.estado == "pendiente":
            age_hours = (now - solicitud.creado).total_seconds() / 3600
            last_sent = solicitud.ultimo_recordatorio_estado_en
            if age_hours >= 12 and (not last_sent or (now - last_sent).total_seconds() >= 43200):
                html = render_template(
                    "emails/recordatorio_operativo.html",
                    solicitud=solicitud,
                    title=f"Recordatorio de respuesta para solicitud #{solicitud.id}",
                    body="La solicitud sigue pendiente de respuesta del operador. Conviene revisarla para no frenar la operacion.",
                    next_step="Ingresá a Residuos 360 y aceptá, rechazá o reasigná el retiro.",
                )
                if solicitud.gestor_user:
                    enviar_email(f"Recordatorio de solicitud #{solicitud.id}", solicitud.gestor_user.email, html)
                if solicitud.owner:
                    enviar_email(f"Seguimiento de solicitud #{solicitud.id}", solicitud.owner.email, html)
                solicitud.ultimo_recordatorio_estado_en = now
                touched = True

        if solicitud.estado == "retirada":
            last_status = solicitud.ultimo_recordatorio_estado_en
            retiro_age = (now - (solicitud.retirada_en or solicitud.aceptada_en or solicitud.creado)).total_seconds() / 3600
            if retiro_age >= 6 and (not last_status or (now - last_status).total_seconds() >= 21600):
                html = render_template(
                    "emails/recordatorio_operativo.html",
                    solicitud=solicitud,
                    title=f"Cierre pendiente para solicitud #{solicitud.id}",
                    body="El retiro ya fue marcado como realizado, pero falta cargar peso, destino y cierre documental.",
                    next_step="Ingresa a Residuos 360 y completa el servicio para cerrar la trazabilidad.",
                )
                if solicitud.gestor_user:
                    enviar_email(f"Cierre pendiente de solicitud #{solicitud.id}", solicitud.gestor_user.email, html)
                solicitud.ultimo_recordatorio_estado_en = now
                touched = True

        if solicitud.estado in {"aceptada", "retirada", "completada"} and not solicitud.documentacion_completa:
            reference_date = solicitud.completada_en or solicitud.retirada_en or solicitud.aceptada_en or solicitud.creado
            age_hours = (now - reference_date).total_seconds() / 3600
            last_docs = solicitud.ultimo_recordatorio_documentos_en
            if age_hours >= 8 and (not last_docs or (now - last_docs).total_seconds() >= 28800):
                faltantes = ", ".join(solicitud.documentacion_faltante) or "Respaldo operativo"
                html = render_template(
                    "emails/recordatorio_operativo.html",
                    solicitud=solicitud,
                    title=f"Recordatorio documental para solicitud #{solicitud.id}",
                    body=f"Faltan archivos para cerrar bien la trazabilidad: {faltantes}.",
                    next_step="Subí los documentos faltantes desde el panel o revisá el centro documental.",
                )
                if solicitud.gestor_user:
                    enviar_email(f"Faltan documentos en solicitud #{solicitud.id}", solicitud.gestor_user.email, html)
                if solicitud.owner:
                    enviar_email(f"Seguimiento documental de solicitud #{solicitud.id}", solicitud.owner.email, html)
                solicitud.ultimo_recordatorio_documentos_en = now
                touched = True

    if touched:
        db.session.commit()


def company_document_center_context(user, estado=None):
    solicitudes = sorted(user.solicitudes, key=lambda s: s.creado, reverse=True)
    selected_estado = (estado or "").strip()
    if selected_estado:
        solicitudes = [s for s in solicitudes if s.estado == selected_estado]

    total_files = sum(
        1
        for s in solicitudes
        for doc in [s.comprobante_peso, s.evidencia_retiro, s.certificado_destino]
        if doc
    )
    return {
        "solicitudes": solicitudes,
        "selected_estado": selected_estado,
        "doc_summary": {
            "requests": len(solicitudes),
            "files": total_files,
            "complete": sum(1 for s in solicitudes if s.documentacion_completa),
            "partial": sum(1 for s in solicitudes if not s.documentacion_completa and (s.comprobante_peso or s.evidencia_retiro or s.certificado_destino)),
            "pending": sum(1 for s in solicitudes if not (s.comprobante_peso or s.evidencia_retiro or s.certificado_destino)),
        },
    }


def gestor_document_center_context(user, estado=None):
    solicitudes = sorted(user.gestor_solicitudes, key=lambda s: s.creado, reverse=True)
    selected_estado = (estado or "").strip()
    if selected_estado:
        solicitudes = [s for s in solicitudes if s.estado == selected_estado]

    total_files = sum(
        1
        for s in solicitudes
        for doc in [s.comprobante_peso, s.evidencia_retiro, s.certificado_destino]
        if doc
    )
    return {
        "solicitudes": solicitudes,
        "selected_estado": selected_estado,
        "doc_summary": {
            "requests": len(solicitudes),
            "files": total_files,
            "complete": sum(1 for s in solicitudes if s.documentacion_completa),
            "partial": sum(1 for s in solicitudes if not s.documentacion_completa and (s.comprobante_peso or s.evidencia_retiro or s.certificado_destino)),
            "pending": sum(1 for s in solicitudes if not (s.comprobante_peso or s.evidencia_retiro or s.certificado_destino)),
        },
    }


def location_tokens(value):
    normalized = normalize_company_name(value)
    if not normalized:
        return []
    return [token for token in normalized.split(" ") if len(token) >= 3]


def gestor_matches_location(gestor, ubicacion=""):
    if not ubicacion:
        return True

    normalized_location = normalize_company_name(ubicacion)
    coverage_labels = operador_cobertura_labels(gestor)
    coverage_norm = [normalize_company_name(label) for label in coverage_labels if label]
    if not coverage_norm:
        return False

    for label in coverage_norm:
        if normalized_location in label or label in normalized_location:
            return True

    requested_tokens = set(location_tokens(ubicacion))
    if not requested_tokens:
        return False
    for label in coverage_labels:
        gestor_tokens = set(location_tokens(label))
        if gestor_tokens and requested_tokens & gestor_tokens:
            return True
    return False


def gestor_matches_service(gestor, ubicacion="", distancia_km=0.0):
    if not gestor_matches_location(gestor, ubicacion):
        return False
    radius = float(gestor.radio_cobertura_km or 0)
    if radius <= 0 or not distancia_km:
        return True
    return float(distancia_km) <= radius


def fetch_ministerio_transportistas():
    with urlopen(MINISTERIO_TRANSPORTE_XLSX_URL, timeout=20) as response:
        content = response.read()
    rows = read_xlsx_rows(content)
    return xlsx_rows_to_records(rows)


def match_ministerio_record_for_gestor(gestor, ministerio_records, rut_index, name_index):
    candidate_ruts = [
        normalize_rut(gestor.rut),
        normalize_rut(gestor.ministerio_rut),
        normalize_rut(gestor.numero_habilitacion),
    ]
    for candidate_rut in candidate_ruts:
        if candidate_rut and candidate_rut in rut_index:
            return rut_index[candidate_rut], "rut", 1.0

    candidate_names = [
        normalize_company_name(gestor.empresa),
        normalize_company_name(gestor.nombre_visible),
    ]
    for candidate_name in candidate_names:
        if candidate_name and candidate_name in name_index:
            return name_index[candidate_name], "nombre", 0.97

    best_match = None
    best_score = 0.0
    for candidate_name in candidate_names:
        if not candidate_name:
            continue
        for record in ministerio_records:
            for source_name in {
                normalize_company_name(record.get("razon_social")),
                normalize_company_name(record.get("nombre_comercial")),
            }:
                if not source_name:
                    continue
                score = SequenceMatcher(None, candidate_name, source_name).ratio()
                if score > best_score:
                    best_score = score
                    best_match = record

    if best_match and best_score >= 0.88:
        return best_match, "nombre_probable", best_score

    return None, None, 0.0


def sync_transportistas_ministerio(force=False):
    gestores = User.query.filter_by(role="gestor").all()
    if not gestores:
        return {"matched": 0, "total": 0, "status": "no_gestores"}

    recently_verified = all(
        gestor.verificacion_actualizada_en
        and (datetime.utcnow() - gestor.verificacion_actualizada_en).total_seconds() < 86400
        for gestor in gestores
    )
    if recently_verified and not force:
        return {
            "matched": sum(1 for g in gestores if g.verificado_ministerio),
            "total": len(gestores),
            "status": "cached",
        }

    ministerio_records = fetch_ministerio_transportistas()
    index = {}
    for record in ministerio_records:
        for candidate in {record["razon_social"], record["nombre_comercial"]}:
            key = normalize_company_name(candidate)
            if key:
                index[key] = record

    matched = 0
    for gestor in gestores:
        candidates = [
            normalize_company_name(gestor.empresa),
            normalize_company_name(gestor.nombre_visible),
        ]
        match = next((index[key] for key in candidates if key in index), None)
        gestor.verificacion_fuente = MINISTERIO_TRANSPORTE_XLSX_URL
        gestor.verificacion_actualizada_en = datetime.utcnow()

        if match:
            gestor.verificado_ministerio = True
            gestor.operador_habilitado = True
            gestor.verificacion_observacion = (
                f"Coincidencia con {match['nombre_comercial']}"
            )
            gestor.ministerio_rut = match.get("rut") or gestor.ministerio_rut
            gestor.ministerio_modalidad = match.get("modalidad") or ""
            gestor.ministerio_estado = match.get("estado_habilitacion") or ""
            gestor.ministerio_categorias = match.get("categorias") or ""
            if not gestor.numero_habilitacion:
                gestor.numero_habilitacion = (
                    match.get("rut") or "Verificado en listado Ministerio"
                )
            matched += 1
        else:
            gestor.verificado_ministerio = False
            gestor.ministerio_modalidad = ""
            gestor.ministerio_estado = ""
            gestor.ministerio_categorias = ""
            gestor.verificacion_observacion = "No encontrado en el último sync automático"

    db.session.commit()
    result = {"matched": matched, "total": len(gestores), "status": "synced"}
    write_ministerio_snapshot(ministerio_records, result)
    return result


def sync_transportistas_ministerio(force=False):
    gestores = User.query.filter_by(role="gestor").all()
    if not gestores:
        return {"matched": 0, "total": 0, "status": "no_gestores"}

    recently_verified = all(
        gestor.verificacion_actualizada_en
        and (datetime.utcnow() - gestor.verificacion_actualizada_en).total_seconds() < 86400
        for gestor in gestores
    )
    if recently_verified and not force:
        return {
            "matched": sum(1 for g in gestores if g.verificado_ministerio),
            "total": len(gestores),
            "status": "cached",
        }

    ministerio_records = fetch_ministerio_transportistas()
    name_index = {}
    rut_index = {}
    for record in ministerio_records:
        record_rut = normalize_rut(record.get("rut"))
        if record_rut:
            rut_index[record_rut] = record
        for candidate in {record.get("razon_social"), record.get("nombre_comercial")}:
            key = normalize_company_name(candidate)
            if key:
                name_index[key] = record

    matched = 0
    for gestor in gestores:
        match = None
        match_mode = None
        match_score = 0.0

        for candidate_rut in [
            normalize_rut(gestor.rut),
            normalize_rut(gestor.ministerio_rut),
            normalize_rut(gestor.numero_habilitacion),
        ]:
            if candidate_rut and candidate_rut in rut_index:
                match = rut_index[candidate_rut]
                match_mode = "rut"
                match_score = 1.0
                break

        if not match:
            candidates = [
                normalize_company_name(gestor.empresa),
                normalize_company_name(gestor.nombre_visible),
            ]
            match = next((name_index[key] for key in candidates if key in name_index), None)
            if match:
                match_mode = "nombre"
                match_score = 0.97

        if not match:
            best_match = None
            best_score = 0.0
            candidates = [
                normalize_company_name(gestor.empresa),
                normalize_company_name(gestor.nombre_visible),
            ]
            for candidate in candidates:
                if not candidate:
                    continue
                for record in ministerio_records:
                    for source_name in {
                        normalize_company_name(record.get("razon_social")),
                        normalize_company_name(record.get("nombre_comercial")),
                    }:
                        if not source_name:
                            continue
                        score = SequenceMatcher(None, candidate, source_name).ratio()
                        if score > best_score:
                            best_score = score
                            best_match = record
            if best_match and best_score >= 0.88:
                match = best_match
                match_mode = "nombre_probable"
                match_score = best_score

        gestor.verificacion_fuente = MINISTERIO_TRANSPORTE_XLSX_URL
        gestor.verificacion_actualizada_en = datetime.utcnow()

        if match:
            gestor.verificado_ministerio = True
            gestor.operador_habilitado = True
            if match_mode == "rut":
                gestor.verificacion_observacion = f"Verificado por RUT con {match['nombre_comercial']}"
            elif match_mode == "nombre":
                gestor.verificacion_observacion = f"Coincidencia fuerte por nombre con {match['nombre_comercial']}"
            else:
                pct = int(round(match_score * 100))
                gestor.verificacion_observacion = f"Coincidencia probable por nombre con {match['nombre_comercial']} ({pct}% de similitud)"
            gestor.ministerio_rut = match.get("rut") or gestor.ministerio_rut
            gestor.ministerio_modalidad = match.get("modalidad") or ""
            gestor.ministerio_estado = match.get("estado_habilitacion") or ""
            gestor.ministerio_categorias = match.get("categorias") or ""
            if not gestor.numero_habilitacion:
                gestor.numero_habilitacion = match.get("rut") or "Verificado en listado Ministerio"
            matched += 1
        else:
            gestor.verificado_ministerio = False
            gestor.ministerio_modalidad = ""
            gestor.ministerio_estado = ""
            gestor.ministerio_categorias = ""
            if normalize_rut(gestor.rut):
                gestor.verificacion_observacion = "RUT informado sin coincidencia en el ultimo sync automatico"
            else:
                gestor.verificacion_observacion = "No encontrado en el ultimo sync automatico"

    db.session.commit()
    result = {"matched": matched, "total": len(gestores), "status": "synced"}
    write_ministerio_snapshot(ministerio_records, result)
    return result


def enviar_email(asunto, destinatario, html):
    if not app.config.get("MAIL_USERNAME"):
        return
    try:
        msg = Message(
            asunto,
            sender=("Residuos 360", app.config["MAIL_USERNAME"]),
            recipients=[destinatario],
        )
        msg.html = html
        mail.send(msg)
    except Exception as exc:  # pragma: no cover
        app.logger.warning("No se pudo enviar email a %s: %s", destinatario, exc)


def ensure_schema():
    inspector = inspect(db.engine)
    table_names = inspector.get_table_names()
    if "solicitud" not in table_names:
        return

    columns = {col["name"] for col in inspector.get_columns("solicitud")}
    if "fecha_retiro" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN fecha_retiro DATE"))
    if "franja_horaria" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN franja_horaria VARCHAR(64)"))
    if "programado_en" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN programado_en DATETIME"))
    if "aceptada_en" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN aceptada_en DATETIME"))
    if "retirada_en" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN retirada_en DATETIME"))
    if "completada_en" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN completada_en DATETIME"))
    if "destino_final" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN destino_final VARCHAR(256)"))
    if "observaciones" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN observaciones TEXT"))
    if "evidencia_retiro" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN evidencia_retiro VARCHAR(256)"))
    if "certificado_destino" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN certificado_destino VARCHAR(256)"))
    if "retiro_entregado_por" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN retiro_entregado_por VARCHAR(128)"))
    if "retiro_recibido_por" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN retiro_recibido_por VARCHAR(128)"))
    if "retiro_observaciones" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN retiro_observaciones TEXT"))
    if "vehiculo_matricula" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN vehiculo_matricula VARCHAR(32)"))
    if "vehiculo_descripcion" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN vehiculo_descripcion VARCHAR(128)"))
    if "proveedor_rastreo" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN proveedor_rastreo VARCHAR(128)"))
    if "destino_previsto" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN destino_previsto VARCHAR(256)"))
    if "categoria_residuo" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN categoria_residuo VARCHAR(64)"))
    if "nivel_riesgo" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN nivel_riesgo VARCHAR(32)"))
    if "direccion_retiro" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN direccion_retiro VARCHAR(256)"))
    if "volumen_estimado_m3" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN volumen_estimado_m3 FLOAT"))
    if "distancia_km" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN distancia_km FLOAT"))
    if "requiere_certificado" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN requiere_certificado BOOLEAN DEFAULT 0"))
    if "alerta_peso" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN alerta_peso BOOLEAN DEFAULT 0"))
    if "desvio_peso_pct" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN desvio_peso_pct FLOAT"))
    if "moneda" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN moneda VARCHAR(8) DEFAULT 'UYU'"))
    if "iva_tasa" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN iva_tasa FLOAT DEFAULT 0.22"))
    if "cfe_tipo_sugerido" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN cfe_tipo_sugerido VARCHAR(32) DEFAULT 'eFactura'"))
    if "monto_transporte_uyu" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN monto_transporte_uyu FLOAT"))
    if "monto_gestion_uyu" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN monto_gestion_uyu FLOAT"))
    if "monto_certificado_uyu" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN monto_certificado_uyu FLOAT"))
    if "monto_plataforma_uyu" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN monto_plataforma_uyu FLOAT"))
    if "subtotal_neto_uyu" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN subtotal_neto_uyu FLOAT"))
    if "iva_uyu" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN iva_uyu FLOAT"))
    if "total_uyu" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN total_uyu FLOAT"))
    if "liquidacion_transportista_uyu" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN liquidacion_transportista_uyu FLOAT"))
    if "liquidacion_gestor_uyu" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN liquidacion_gestor_uyu FLOAT"))
    if "liquidacion_plataforma_uyu" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN liquidacion_plataforma_uyu FLOAT"))
    if "subtotal_final_uyu" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN subtotal_final_uyu FLOAT"))
    if "iva_final_uyu" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN iva_final_uyu FLOAT"))
    if "total_final_uyu" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN total_final_uyu FLOAT"))
    if "liquidacion_transportista_final_uyu" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN liquidacion_transportista_final_uyu FLOAT"))
    if "liquidacion_gestor_final_uyu" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN liquidacion_gestor_final_uyu FLOAT"))
    if "liquidacion_plataforma_final_uyu" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN liquidacion_plataforma_final_uyu FLOAT"))
    if "ajuste_economico_uyu" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN ajuste_economico_uyu FLOAT"))
    if "estado_economico" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN estado_economico VARCHAR(24) DEFAULT 'cotizado'"))
    if "ultimo_recordatorio_estado_en" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN ultimo_recordatorio_estado_en DATETIME"))
    if "ultimo_recordatorio_documentos_en" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN ultimo_recordatorio_documentos_en DATETIME"))
    if "empresa_rating" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN empresa_rating INTEGER"))
    if "empresa_rating_comment" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN empresa_rating_comment TEXT"))
    if "empresa_rated_en" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN empresa_rated_en DATETIME"))
    if "operador_rating" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN operador_rating INTEGER"))
    if "operador_rating_comment" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN operador_rating_comment TEXT"))
    if "operador_rated_en" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN operador_rated_en DATETIME"))
    if "contrato_marco_id" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN contrato_marco_id INTEGER"))
    if "orden_servicio_estado" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN orden_servicio_estado VARCHAR(32) DEFAULT 'borrador'"))
    if "orden_servicio_documento" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN orden_servicio_documento VARCHAR(256)"))
    if "orden_servicio_hash" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN orden_servicio_hash VARCHAR(128)"))
    if "orden_servicio_version" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN orden_servicio_version INTEGER DEFAULT 1"))
    if "orden_empresa_confirmada_en" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN orden_empresa_confirmada_en DATETIME"))
    if "orden_transportista_confirmada_en" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN orden_transportista_confirmada_en DATETIME"))
    if "apta_declaracion" not in columns:
        db.session.execute(text("ALTER TABLE solicitud ADD COLUMN apta_declaracion BOOLEAN DEFAULT 0"))
    if "solicitud_evento" not in table_names:
        SolicitudEvento.__table__.create(db.engine, checkfirst=True)
    if "observacion_institucional" not in table_names:
        ObservacionInstitucional.__table__.create(db.engine, checkfirst=True)
    if "access_audit_log" not in table_names:
        AccessAuditLog.__table__.create(db.engine, checkfirst=True)

    user_columns = {col["name"] for col in inspector.get_columns("user")}
    if "razon_social" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN razon_social VARCHAR(160)"))
    if "rut" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN rut VARCHAR(32)"))
    if "giro" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN giro VARCHAR(160)"))
    if "condicion_tributaria" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN condicion_tributaria VARCHAR(64)"))
    if "operador_habilitado" not in user_columns:
        db.session.execute(
            text("ALTER TABLE user ADD COLUMN operador_habilitado BOOLEAN DEFAULT 0")
        )
    if "numero_habilitacion" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN numero_habilitacion VARCHAR(64)"))
    if "verificado_ministerio" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN verificado_ministerio BOOLEAN DEFAULT 0"))
    if "verificacion_fuente" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN verificacion_fuente VARCHAR(256)"))
    if "verificacion_actualizada_en" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN verificacion_actualizada_en DATETIME"))
    if "verificacion_observacion" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN verificacion_observacion VARCHAR(256)"))
    if "ministerio_rut" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN ministerio_rut VARCHAR(32)"))
    if "ministerio_modalidad" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN ministerio_modalidad VARCHAR(64)"))
    if "ministerio_estado" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN ministerio_estado VARCHAR(128)"))
    if "ministerio_categorias" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN ministerio_categorias VARCHAR(128)"))
    if "categorias_residuo" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN categorias_residuo VARCHAR(256)"))
    if "institution_access_level" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN institution_access_level VARCHAR(32)"))
    if "direccion_base" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN direccion_base VARCHAR(256)"))
    if "domicilio_fiscal" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN domicilio_fiscal VARCHAR(256)"))
    if "contacto_nombre" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN contacto_nombre VARCHAR(128)"))
    if "contacto_facturacion" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN contacto_facturacion VARCHAR(128)"))
    if "email_facturacion" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN email_facturacion VARCHAR(120)"))
    if "notas_operativas" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN notas_operativas TEXT"))
    if "zonas_cobertura" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN zonas_cobertura TEXT"))
    if "radio_cobertura_km" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN radio_cobertura_km FLOAT DEFAULT 25"))
    if "moneda_tarifa" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN moneda_tarifa VARCHAR(8) DEFAULT 'UYU'"))
    if "tarifa_base_retiro_uyu" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN tarifa_base_retiro_uyu FLOAT DEFAULT 1500"))
    if "tarifa_km_uyu" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN tarifa_km_uyu FLOAT DEFAULT 45"))
    if "tarifa_m3_uyu" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN tarifa_m3_uyu FLOAT DEFAULT 220"))
    if "tarifa_tratamiento_kg_uyu" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN tarifa_tratamiento_kg_uyu FLOAT DEFAULT 0"))
    if "costo_certificado_uyu" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN costo_certificado_uyu FLOAT DEFAULT 350"))
    if "cargos_extra" in table_names:
        cargo_columns = {col["name"] for col in inspector.get_columns("cargos_extra")}
        if "monto_uyu" not in cargo_columns:
            db.session.execute(text("ALTER TABLE cargos_extra ADD COLUMN monto_uyu FLOAT"))
    db.session.commit()


def seed_plans():
    defaults = [
        {
            "name": "Starter",
            "price_uyu": 1900,
            "limit_requests": 15,
            "commission_rate": 0.10,
        },
        {
            "name": "Pro",
            "price_uyu": 4900,
            "limit_requests": 80,
            "commission_rate": 0.06,
        },
        {
            "name": "Pago por Uso",
            "price_uyu": 0,
            "limit_requests": None,
            "commission_rate": 0.20,
        },
    ]
    for payload in defaults:
        if not Plan.query.filter_by(name=payload["name"]).first():
            db.session.add(Plan(**payload))
    db.session.commit()


_database_ready = False


def ensure_database_ready():
    global _database_ready
    if _database_ready:
        return
    db.create_all()
    ensure_schema()
    seed_plans()
    _database_ready = True


@app.context_processor
def inject_now():
    return {
        "now": datetime.utcnow,
        "contract_state_label": contract_state_label,
        "order_state_label": order_state_label,
        "economic_state_label": economic_state_label,
        "estado_documental_label": estado_documental_label,
        "notification_summary": build_notification_summary(current_user)
        if getattr(current_user, "is_authenticated", False)
        else {"count": 0, "items": []},
    }


login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Necesitas iniciar sesion para continuar."
login_manager.login_message_category = "info"


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.before_request
def prepare_database_for_request():
    if request.endpoint == "healthz":
        return None
    ensure_database_ready()
    return None


@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))


def require_roles(*roles):
    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def company_metrics(user):
    solicitudes = user.solicitudes
    return {
        "total": len(solicitudes),
        "pend": sum(1 for s in solicitudes if s.estado == "pendiente"),
        "acept": sum(1 for s in solicitudes if s.estado in {"aceptada", "retirada"}),
        "reti": sum(1 for s in solicitudes if s.estado == "retirada"),
        "comp": sum(1 for s in solicitudes if s.estado == "completada"),
        "rech": sum(1 for s in solicitudes if s.estado == "rechazada"),
    }


def build_notification_summary(user):
    if not user or not getattr(user, "is_authenticated", False):
        return {"count": 0, "items": []}

    items = []

    if user.role == "empresa":
        pendientes = [s for s in user.solicitudes if s.estado == "pendiente"]
        aceptadas = [s for s in user.solicitudes if s.estado == "aceptada"]
        retiradas = [s for s in user.solicitudes if s.estado == "retirada"]
        docs = [
            s for s in user.solicitudes
            if s.estado == "completada" and not s.documentacion_completa
        ]
        if pendientes:
            items.append({
                "icon": "fa-regular fa-hourglass-half",
                "title": f"{len(pendientes)} solicitud(es) esperando respuesta",
                "detail": "Hay retiros pendientes de confirmacion por operador.",
                "href": url_for("dashboard", estado="pendiente"),
            })
        if aceptadas:
            items.append({
                "icon": "fa-solid fa-truck-fast",
                "title": f"{len(aceptadas)} retiro(s) en curso",
                "detail": "Ya fueron aceptados y estan en ejecucion.",
                "href": url_for("dashboard", estado="aceptada"),
            })
        if retiradas:
            items.append({
                "icon": "fa-solid fa-location-dot",
                "title": f"{len(retiradas)} retiro(s) ya realizados",
                "detail": "Falta el cierre final con peso, destino y respaldo.",
                "href": url_for("dashboard", estado="retirada"),
            })
        if docs:
            items.append({
                "icon": "fa-regular fa-folder-open",
                "title": f"{len(docs)} cierre(s) con documentos faltantes",
                "detail": "Revisa respaldo o certificados pendientes.",
                "href": url_for("documentos", estado="completada"),
            })

    elif user.role == "gestor":
        pendientes = [s for s in user.gestor_solicitudes if s.estado == "pendiente"]
        aceptadas = [s for s in user.gestor_solicitudes if s.estado == "aceptada"]
        retiradas = [s for s in user.gestor_solicitudes if s.estado == "retirada"]
        por_calificar = [s for s in user.gestor_solicitudes if s.estado == "completada" and not s.empresa_rating]
        if pendientes:
            items.append({
                "icon": "fa-regular fa-bell",
                "title": f"{len(pendientes)} solicitud(es) por revisar",
                "detail": "Acepta o rechaza nuevos pedidos.",
                "href": url_for("dashboard") + "#revisar",
            })
        if aceptadas:
            items.append({
                "icon": "fa-solid fa-route",
                "title": f"{len(aceptadas)} retiro(s) por ejecutar",
                "detail": "Marca cuando el retiro ya fue realizado.",
                "href": url_for("dashboard") + "#retiros",
            })
        if retiradas:
            items.append({
                "icon": "fa-solid fa-clipboard-check",
                "title": f"{len(retiradas)} cierre(s) pendientes",
                "detail": "Falta cargar peso, destino y documentos finales.",
                "href": url_for("dashboard") + "#cierres",
            })
        if por_calificar:
            items.append({
                "icon": "fa-solid fa-star",
                "title": f"{len(por_calificar)} empresa(s) por valorar",
                "detail": "Deja una referencia operativa del servicio.",
                "href": url_for("dashboard") + "#valoraciones",
            })

    elif user.role == "admin":
        solicitudes = Solicitud.query.order_by(Solicitud.creado.desc()).all()
        nuevas_24h = sum(
            1 for s in solicitudes if (datetime.utcnow() - s.creado).total_seconds() <= 86400
        )
        pendientes_activation = User.query.filter_by(role="gestor", is_active=False).count()
        if nuevas_24h:
            items.append({
                "icon": "fa-regular fa-clock",
                "title": f"{nuevas_24h} solicitud(es) nuevas en 24h",
                "detail": "Actividad reciente para supervisar.",
                "href": url_for("dashboard") + "#requests",
            })
        if pendientes_activation:
            items.append({
                "icon": "fa-solid fa-user-plus",
                "title": f"{pendientes_activation} operador(es) pendientes de activacion",
                "detail": "Completa email, tarifas y zonas para activarlos.",
                "href": url_for("dashboard") + "#operators",
            })

    return {
        "count": len(items),
        "items": items[:5],
    }


def company_rating_summary(user):
    rated = [
        solicitud
        for solicitud in user.solicitudes
        if getattr(solicitud, "empresa_rating", None)
    ]
    average = round(
        sum(solicitud.empresa_rating for solicitud in rated) / len(rated),
        1,
    ) if rated else None
    recent = sorted(
        rated,
        key=lambda solicitud: solicitud.empresa_rated_en or solicitud.completada_en or solicitud.creado,
        reverse=True,
    )[:5]
    return {
        "average": average,
        "count": len(rated),
        "recent": recent,
    }


def user_rating_distribution(user):
    buckets = {stars: 0 for stars in range(5, 0, -1)}
    for rating in user.rating_values:
        if rating in buckets:
            buckets[rating] += 1
    total = sum(buckets.values())
    max_count = max(buckets.values()) if total else 0
    return {
        "items": [
            {
                "stars": stars,
                "count": buckets[stars],
                "pct": round((buckets[stars] / total) * 100, 1) if total else 0,
                "bar_pct": round((buckets[stars] / max_count) * 100, 1) if max_count else 0,
            }
            for stars in range(5, 0, -1)
        ],
        "total": total,
    }


def company_dashboard_context(user, estado=None, selected_id=None):
    metrics = company_metrics(user)
    rating_summary = company_rating_summary(user)
    rating_distribution = user_rating_distribution(user)
    declaration_context = company_declaration_context(user)
    contract_context = contract_summary_context(user)
    solicitudes = sorted(user.solicitudes, key=lambda s: s.creado, reverse=True)
    selected_estado = (estado or "").strip()
    if selected_estado in {"pendiente", "aceptada", "retirada", "completada", "rechazada"}:
        filtered = [s for s in solicitudes if s.estado == selected_estado]
    else:
        selected_estado = ""
        filtered = solicitudes

    featured = filtered[:6]
    selected_solicitud = None
    if selected_id:
        selected_solicitud = next((s for s in solicitudes if s.id == selected_id), None)
    if not selected_solicitud and featured:
        selected_solicitud = featured[0]

    alerts = []
    for solicitud in filtered[:8]:
        if solicitud.alerta_peso:
            alerts.append(
                {
                    "level": "warning",
                    "label": f"Solicitud #{solicitud.id} con desvio de peso",
                    "detail": f"Desvio registrado: {solicitud.desvio_peso_pct}%.",
                }
            )
        if solicitud.estado == "completada" and not solicitud.documentacion_completa:
            alerts.append(
                {
                    "level": "info",
                    "label": f"Solicitud #{solicitud.id} con respaldo incompleto",
                    "detail": ", ".join(solicitud.documentacion_faltante),
                }
            )

    return {
        "solicitudes": solicitudes,
        "filtered_solicitudes": filtered,
        "featured_solicitudes": featured,
        "selected_solicitud": selected_solicitud,
        "selected_timeline": build_timeline(selected_solicitud) if selected_solicitud else [],
        "company_fiscal_completion": user_fiscal_completion(user),
        "company_alerts": alerts,
        "declaration_summary": declaration_context["declaration_summary"],
        "contract_summary": contract_context["contract_summary"],
        "company_rating_average": rating_summary["average"],
        "company_rating_count": rating_summary["count"],
        "company_recent_ratings": rating_summary["recent"],
        "company_rating_distribution": rating_distribution["items"],
        "completed_to_rate_operator": [
            s for s in solicitudes if s.estado == "completada" and s.gestor_id and not s.operador_rating
        ][:5],
        "selected_estado": selected_estado,
        "total": metrics["total"],
        "pend": metrics["pend"],
        "acept": metrics["acept"],
        "comp": metrics["comp"],
        "rech": metrics["rech"],
    }


def company_compliance_context(user, estado=None, tipo=None):
    query = Solicitud.query.filter_by(user_id=user.id)
    if estado:
        query = query.filter_by(estado=estado)
    if tipo:
        query = query.filter_by(tipo=tipo)

    solicitudes = query.order_by(Solicitud.creado.desc()).all()
    tipos = sorted({s.tipo for s in user.solicitudes})
    completed = [s for s in user.solicitudes if s.estado == "completada"]
    documented = [
        s
        for s in completed
        if s.comprobante_peso and (s.evidencia_retiro or s.certificado_destino)
    ]
    documentation_rate = round((len(documented) / len(completed)) * 100) if completed else 0

    return {
        "solicitudes": solicitudes,
        "available_tipos": tipos,
        "selected_estado": estado or "",
        "selected_tipo": tipo or "",
        "compliance_summary": {
            "documented_completed": len(documented),
            "total_completed": len(completed),
            "documentation_rate": documentation_rate,
            "pending_documents": sum(
                1
                for s in completed
                if not (s.comprobante_peso and (s.evidencia_retiro or s.certificado_destino))
            ),
        },
    }


def company_reports_context(user, estado=None, tipo=None):
    estado_filter = (estado or "").strip()
    tipo_filter = (tipo or "").strip()

    solicitudes = sorted(user.solicitudes, key=lambda s: s.creado, reverse=True)
    filtered = solicitudes
    if estado_filter:
        filtered = [s for s in filtered if s.estado == estado_filter]
    if tipo_filter:
        filtered = [s for s in filtered if s.tipo == tipo_filter]

    total_facturado = round(sum((s.total_economico_vigente or 0) for s in filtered), 2)
    total_peso_estimado = round(sum((s.peso_estimado or 0) for s in filtered), 2)
    total_peso_real = round(sum((s.peso_real or 0) for s in filtered), 2)
    total_neto = round(sum(((s.subtotal_final_display if s.subtotal_final_display is not None else s.subtotal_estimado_display) or 0) for s in filtered), 2)
    total_iva = round(sum(((s.iva_final_display if s.iva_final_display is not None else s.iva_uyu) or 0) for s in filtered), 2)
    total_transport = round(sum(((s.liquidacion_transportista_final_uyu if s.liquidacion_transportista_final_uyu is not None else s.liquidacion_transportista_uyu) or 0) for s in filtered), 2)
    total_management = round(sum(((s.liquidacion_gestor_final_uyu if s.liquidacion_gestor_final_uyu is not None else s.liquidacion_gestor_uyu) or 0) for s in filtered), 2)
    total_platform = round(sum(((s.liquidacion_plataforma_final_uyu if s.liquidacion_plataforma_final_uyu is not None else s.liquidacion_plataforma_uyu) or 0) for s in filtered), 2)
    documented = [
        s
        for s in filtered
        if s.comprobante_peso or s.evidencia_retiro or s.certificado_destino
    ]

    by_tipo = {}
    by_operador = {}
    monthly = {}

    for solicitud in filtered:
        tipo_key = solicitud.tipo
        by_tipo.setdefault(
            tipo_key,
            {"label": tipo_key, "count": 0, "weight": 0.0, "amount": 0.0, "platform": 0.0},
        )
        by_tipo[tipo_key]["count"] += 1
        by_tipo[tipo_key]["weight"] += solicitud.peso_real or solicitud.peso_estimado or 0
        by_tipo[tipo_key]["amount"] += solicitud.total_uyu or solicitud.precio_final or solicitud.precio_estimado or 0
        by_tipo[tipo_key]["platform"] += solicitud.liquidacion_plataforma_uyu or 0

        operador_key = solicitud.gestor_nombre
        by_operador.setdefault(
            operador_key,
            {"label": operador_key, "count": 0, "amount": 0.0, "transport": 0.0, "management": 0.0},
        )
        by_operador[operador_key]["count"] += 1
        by_operador[operador_key]["amount"] += solicitud.total_uyu or solicitud.precio_final or solicitud.precio_estimado or 0
        by_operador[operador_key]["transport"] += solicitud.liquidacion_transportista_uyu or 0
        by_operador[operador_key]["management"] += solicitud.liquidacion_gestor_uyu or 0

        month_key = (solicitud.fecha_retiro or solicitud.creado.date()).strftime("%Y-%m")
        monthly.setdefault(month_key, 0)
        monthly[month_key] += 1

    return {
        "solicitudes": filtered,
        "available_tipos": sorted({s.tipo for s in solicitudes}),
        "selected_estado": estado_filter,
        "selected_tipo": tipo_filter,
        "report_summary": {
            "total_requests": len(filtered),
            "documented_requests": len(documented),
            "total_estimated_weight": total_peso_estimado,
            "total_real_weight": total_peso_real,
            "total_net": total_neto,
            "total_iva": total_iva,
            "total_amount": total_facturado,
            "total_transport": total_transport,
            "total_management": total_management,
            "total_platform": total_platform,
            "pending_requests": sum(1 for s in filtered if s.estado == "pendiente"),
            "completed_requests": sum(1 for s in filtered if s.estado == "completada"),
        },
        "report_by_tipo": sorted(by_tipo.values(), key=lambda item: (-item["count"], item["label"])),
        "report_by_operador": sorted(by_operador.values(), key=lambda item: (-item["count"], item["label"])),
        "report_monthly": [
            {"label": key, "count": monthly[key]}
            for key in sorted(monthly.keys())
        ],
    }


def gestor_stats(user):
    solicitudes = (
        Solicitud.query.filter_by(gestor_id=user.id).order_by(Solicitud.creado.desc()).all()
    )
    accepted = [s for s in solicitudes if s.estado == "aceptada"]
    picked_up = [s for s in solicitudes if s.estado == "retirada"]
    completed = [s for s in solicitudes if s.estado == "completada"]
    rating_distribution = user_rating_distribution(user)
    contract_context = contract_summary_context(user)
    return {
        "solicitudes": solicitudes,
        "pendientes": [s for s in solicitudes if s.estado == "pendiente"],
        "aceptadas": accepted,
        "retiradas": picked_up,
        "completadas": completed,
        "completed_to_rate": [s for s in completed if not s.empresa_rating][:5],
        "gestor_rating_average": user.rating_promedio,
        "gestor_rating_count": user.rating_cantidad,
        "gestor_rating_distribution": rating_distribution["items"],
        "gestor_fiscal_completion": user_fiscal_completion(user),
        "contract_summary": contract_context["contract_summary"],
        "income_summary": gestor_income_summary(solicitudes),
        "pending_contract_orders": [s for s in solicitudes if not s.contrato_vigente][:5],
        "gestor_alerts": [
            s for s in (accepted + picked_up) if s.requiere_certificado or s.nivel_riesgo == "alto"
        ][:5],
    }


def solicitud_transport_income(solicitud, final=False):
    if final and solicitud.liquidacion_transportista_final_uyu is not None:
        return solicitud.liquidacion_transportista_final_uyu or 0
    if solicitud.liquidacion_transportista_uyu is not None:
        return solicitud.liquidacion_transportista_uyu or 0
    return solicitud.monto_transporte_uyu or solicitud.precio_estimado or 0


def gestor_income_summary(solicitudes):
    today = datetime.utcnow().date()
    month_start = today.replace(day=1)
    completed = [s for s in solicitudes if s.estado == "completada"]
    pending_execution = [s for s in solicitudes if s.estado == "aceptada"]
    pending_close = [s for s in solicitudes if s.estado == "retirada"]

    completed_total = round(sum(solicitud_transport_income(s, final=True) for s in completed), 2)
    month_completed = [
        s
        for s in completed
        if ((s.completada_en.date() if s.completada_en else (s.fecha_retiro or s.creado.date())) >= month_start)
    ]
    month_total = round(sum(solicitud_transport_income(s, final=True) for s in month_completed), 2)
    pending_total = round(
        sum(solicitud_transport_income(s) for s in pending_execution + pending_close),
        2,
    )
    average_ticket = round(completed_total / len(completed), 2) if completed else 0

    by_company = {}
    for solicitud in completed:
        key = solicitud.empresa or "Empresa sin nombre"
        by_company.setdefault(key, {"name": key, "count": 0, "amount": 0})
        by_company[key]["count"] += 1
        by_company[key]["amount"] += solicitud_transport_income(solicitud, final=True)

    status_breakdown = [
        {
            "label": "Cerrados",
            "count": len(completed),
            "amount": completed_total,
            "hint": "Servicios finalizados y listos para liquidar.",
            "tone": "success",
        },
        {
            "label": "Por cerrar",
            "count": len(pending_close),
            "amount": round(sum(solicitud_transport_income(s) for s in pending_close), 2),
            "hint": "Ya retirados; falta peso, destino o respaldo.",
            "tone": "warning",
        },
        {
            "label": "Por retirar",
            "count": len(pending_execution),
            "amount": round(sum(solicitud_transport_income(s) for s in pending_execution), 2),
            "hint": "Aceptados y pendientes de ejecucion.",
            "tone": "info",
        },
    ]

    def income_sort_value(solicitud):
        if solicitud.completada_en:
            return solicitud.completada_en
        fecha = solicitud.fecha_retiro or solicitud.creado.date()
        return datetime.combine(fecha, datetime.min.time())

    recent_services = sorted(
        completed,
        key=income_sort_value,
        reverse=True,
    )[:6]

    return {
        "completed_total": completed_total,
        "month_total": month_total,
        "pending_total": pending_total,
        "average_ticket": average_ticket,
        "completed_count": len(completed),
        "month_count": len(month_completed),
        "status_breakdown": status_breakdown,
        "top_companies": sorted(
            [
                {**item, "amount": round(item["amount"], 2)}
                for item in by_company.values()
            ],
            key=lambda item: (-item["amount"], item["name"]),
        )[:5],
        "recent_services": recent_services,
    }


def admin_context():
    try:
        sync_transportistas_ministerio()
    except Exception as exc:  # pragma: no cover
        app.logger.warning("No se pudo sincronizar listado del Ministerio: %s", exc)

    solicitudes = Solicitud.query.order_by(Solicitud.creado.desc()).all()
    empresas = User.query.filter_by(role="empresa").order_by(User.empresa.asc()).all()
    gestores = User.query.filter_by(role="gestor").order_by(User.empresa.asc()).all()
    nuevas_24h = sum(
        1 for s in solicitudes if (datetime.utcnow() - s.creado).total_seconds() <= 86400
    )
    return {
        "solicitudes": solicitudes,
        "empresas": empresas,
        "gestores": gestores,
        "nuevas_24h": nuevas_24h,
        "total_empresas": len(empresas),
        "total_gestores": len(gestores),
        "planes_admin": Plan.query.order_by(Plan.price_uyu.asc()).all(),
        "planes_admin_json": [p.to_dict() for p in Plan.query.order_by(Plan.price_uyu.asc()).all()],
        "ministerio_sync": read_ministerio_snapshot_summary(),
        "ministerio_suggestions": ministerio_unregistered_suggestions(),
        "residue_catalog": residue_options(),
        "residue_groups": grouped_residue_catalog(),
    }


def estado_documental_label(value):
    labels = {
        "completo": "Completo",
        "incompleto": "Incompleto",
        "pendiente": "Pendiente",
        "observado": "Observado",
        "vencido": "Vencido",
        "no_aplicable": "No aplicable",
    }
    return labels.get(value, value.replace("_", " ").capitalize())


def institutional_allows(user, *levels):
    return bool(user and user.role == "institucional" and (user.institution_access_level or "tecnico") in levels)


def institutional_can_view_sensitive(user):
    return institutional_allows(user, "tecnico", "admin_institucional")


def institutional_can_view_prices(user):
    return institutional_allows(user, "admin_institucional")


def log_access_audit(user, accion, entidad, entidad_id=None, detalle=""):
    if not user or getattr(user, "role", None) != "institucional":
        return
    db.session.add(
        AccessAuditLog(
            user_id=user.id,
            accion=accion,
            entidad=entidad,
            entidad_id=str(entidad_id) if entidad_id is not None else None,
            detalle=detalle[:256] if detalle else None,
            ip=(request.remote_addr or "")[:64],
        )
    )
    db.session.commit()


def parse_institutional_filters(args):
    return {
        "estado": (args.get("estado") or "").strip(),
        "documental": (args.get("documental") or "").strip(),
        "tipo": (args.get("tipo") or "").strip(),
        "empresa": (args.get("empresa") or "").strip(),
        "transportista": (args.get("transportista") or "").strip(),
        "zona": (args.get("zona") or "").strip(),
        "contrato": (args.get("contrato") or "").strip(),
        "quick": (args.get("quick") or "").strip(),
    }


def institutional_filtered_solicitudes(filters=None):
    filters = filters or {}
    solicitudes = Solicitud.query.order_by(Solicitud.creado.desc()).all()

    def match_text(value, needle):
        return needle.lower() in (value or "").lower()

    filtered = []
    for solicitud in solicitudes:
        quick = filters.get("quick")
        if quick == "programados" and solicitud.estado not in {"pendiente", "aceptada"}:
            continue
        if quick == "en_curso" and solicitud.estado != "retirada":
            continue
        if quick == "cerrados" and solicitud.estado != "completada":
            continue
        if quick == "observados" and solicitud.estado_documental not in {"incompleto", "pendiente"}:
            continue
        if quick == "documentacion_completa" and solicitud.estado_documental != "completo":
            continue
        if quick == "documentacion_pendiente" and solicitud.estado_documental == "completo":
            continue
        if quick == "contrato_vigente" and not solicitud.contrato_vigente:
            continue
        if quick == "contrato_no_vigente" and solicitud.contrato_vigente:
            continue
        if filters.get("estado") and solicitud.estado != filters["estado"]:
            continue
        if filters.get("documental") and solicitud.estado_documental != filters["documental"]:
            continue
        if filters.get("tipo") and not match_text(solicitud.tipo, filters["tipo"]):
            continue
        if filters.get("empresa") and not (
            match_text(solicitud.empresa, filters["empresa"])
            or match_text(getattr(solicitud.owner, "razon_social", ""), filters["empresa"])
            or match_text(getattr(solicitud.owner, "rut", ""), filters["empresa"])
        ):
            continue
        if filters.get("transportista") and not (
            match_text(solicitud.gestor_nombre, filters["transportista"])
            or match_text(getattr(solicitud.gestor_user, "rut", ""), filters["transportista"])
        ):
            continue
        if filters.get("zona") and not (
            match_text(solicitud.ubicacion, filters["zona"])
            or match_text(solicitud.direccion_retiro, filters["zona"])
        ):
            continue
        if filters.get("contrato") == "vigente" and not solicitud.contrato_vigente:
            continue
        if filters.get("contrato") == "no_vigente" and solicitud.contrato_vigente:
            continue
        filtered.append(solicitud)
    return filtered


def institutional_company_rows(solicitudes):
    rows = {}
    for solicitud in solicitudes:
        owner = solicitud.owner
        key = owner.id if owner else f"anonymous-{solicitud.empresa}"
        if key not in rows:
            rows[key] = {
                "user": owner,
                "empresa": solicitud.empresa,
                "rut": getattr(owner, "rut", None),
                "ubicacion": getattr(owner, "ubicacion", None),
                "direccion_base": getattr(owner, "direccion_base", None),
                "transportistas": set(),
                "tipos": set(),
                "retiros": 0,
                "cerrados": 0,
                "observados": 0,
                "pendientes_documentales": 0,
                "peso_total": 0.0,
                "ultima_actividad": None,
            }
        row = rows[key]
        if solicitud.gestor_user:
            row["transportistas"].add(solicitud.gestor_user.nombre_visible)
        row["tipos"].add(solicitud.tipo)
        row["retiros"] += 1
        row["cerrados"] += 1 if solicitud.estado == "completada" else 0
        row["observados"] += 1 if solicitud.estado_documental in {"incompleto", "pendiente"} else 0
        row["pendientes_documentales"] += 1 if not solicitud.documentacion_completa else 0
        row["peso_total"] += solicitud.peso_real or solicitud.peso_estimado or 0
        candidate_date = solicitud.completada_en or solicitud.retirada_en or solicitud.aceptada_en or solicitud.creado
        if not row["ultima_actividad"] or candidate_date > row["ultima_actividad"]:
            row["ultima_actividad"] = candidate_date
    return sorted(rows.values(), key=lambda item: (item["ultima_actividad"] or datetime.min), reverse=True)


def institutional_operator_rows(solicitudes):
    rows = {}
    for solicitud in solicitudes:
        gestor = solicitud.gestor_user
        if not gestor:
            continue
        if gestor.id not in rows:
            rows[gestor.id] = {
                "user": gestor,
                "empresas": set(),
                "retiros": 0,
                "cerrados": 0,
                "observados": 0,
                "pendientes_documentales": 0,
                "matriculas": set(),
                "tipos": set(),
                "ultima_actividad": None,
            }
        row = rows[gestor.id]
        row["empresas"].add(solicitud.empresa)
        row["retiros"] += 1
        row["cerrados"] += 1 if solicitud.estado == "completada" else 0
        row["observados"] += 1 if solicitud.estado_documental in {"incompleto", "pendiente"} else 0
        row["pendientes_documentales"] += 1 if not solicitud.documentacion_completa else 0
        if solicitud.vehiculo_matricula:
            row["matriculas"].add(solicitud.vehiculo_matricula)
        row["tipos"].add(solicitud.tipo)
        candidate_date = solicitud.completada_en or solicitud.retirada_en or solicitud.aceptada_en or solicitud.creado
        if not row["ultima_actividad"] or candidate_date > row["ultima_actividad"]:
            row["ultima_actividad"] = candidate_date
    return sorted(rows.values(), key=lambda item: (item["ultima_actividad"] or datetime.min), reverse=True)


def institutional_dashboard_context(user, filters=None):
    filters = filters or {}
    solicitudes = institutional_filtered_solicitudes(filters)
    contracts = ContratoMarco.query.order_by(ContratoMarco.actualizado.desc()).all()
    companies = institutional_company_rows(solicitudes)
    operators = institutional_operator_rows(solicitudes)
    residue_counts = {}
    zone_counts = {}
    destination_counts = {}
    alerts = []

    for solicitud in solicitudes:
        residue_counts[solicitud.tipo] = residue_counts.get(solicitud.tipo, 0) + 1
        zone_counts[solicitud.ubicacion] = zone_counts.get(solicitud.ubicacion, 0) + 1
        destination_key = solicitud.destino_final or solicitud.destino_previsto or "Sin destino informado"
        destination_counts[destination_key] = destination_counts.get(destination_key, 0) + 1
        if not solicitud.contrato_vigente:
            alerts.append(("Contrato no vigente", solicitud))
        elif not solicitud.documentacion_completa and solicitud.estado in {"aceptada", "retirada", "completada"}:
            alerts.append(("Legajo incompleto", solicitud))
        elif not solicitud.gestor_user or not (solicitud.gestor_user.verificado_ministerio or solicitud.gestor_user.operador_habilitado):
            alerts.append(("Transportista con verificacion pendiente", solicitud))

    complete_docs = sum(1 for s in solicitudes if s.estado_documental == "completo")
    contracts_vigentes = sum(1 for contract in contracts if contract.estado == "vigente")
    contracts_por_vencer = sum(1 for contract in contracts if contract.estado == "por_vencer")
    contracts_vencidos = sum(1 for contract in contracts if contract.estado == "vencido")
    average_close_hours = 0.0
    close_samples = [
        (s.completada_en - s.creado).total_seconds() / 3600
        for s in solicitudes
        if s.completada_en and s.creado
    ]
    if close_samples:
        average_close_hours = round(sum(close_samples) / len(close_samples), 1)

    return {
        "filters": filters,
        "solicitudes": solicitudes,
        "empresas_rows": companies,
        "operadores_rows": operators,
        "top_residuos": sorted(residue_counts.items(), key=lambda item: (-item[1], item[0]))[:6],
        "top_zonas": sorted(zone_counts.items(), key=lambda item: (-item[1], item[0]))[:6],
        "top_destinos": sorted(destination_counts.items(), key=lambda item: (-item[1], item[0]))[:6],
        "recent_alerts": alerts[:8],
        "observaciones_abiertas": ObservacionInstitucional.query.filter_by(estado="abierta").order_by(ObservacionInstitucional.creado.desc()).limit(8).all(),
        "audit_logs": AccessAuditLog.query.order_by(AccessAuditLog.creado.desc()).limit(12).all(),
        "kpis": {
            "empresas": len(companies),
            "transportistas": len(operators),
            "programados": sum(1 for s in solicitudes if s.estado in {"pendiente", "aceptada"}),
            "en_curso": sum(1 for s in solicitudes if s.estado == "retirada"),
            "cerrados": sum(1 for s in solicitudes if s.estado == "completada"),
            "observados": sum(1 for s in solicitudes if s.estado_documental in {"incompleto", "pendiente"}),
            "documentacion_completa": complete_docs,
            "documentacion_pendiente": sum(1 for s in solicitudes if s.estado_documental != "completo"),
            "contratos_vigentes": contracts_vigentes,
            "contratos_por_vencer": contracts_por_vencer,
            "contratos_vencidos": contracts_vencidos,
            "pct_contrato_vigente": round((sum(1 for s in solicitudes if s.contrato_vigente) / len(solicitudes)) * 100, 1) if solicitudes else 0,
            "pct_documentacion_completa": round((complete_docs / len(solicitudes)) * 100, 1) if solicitudes else 0,
            "tiempo_promedio_cierre_horas": average_close_hours,
            "alertas": len(alerts),
        },
        "map_points": [
            {
                "id": s.id,
                "empresa": s.empresa,
                "zona": s.ubicacion,
                "direccion": s.direccion_retiro or s.ubicacion,
                "transportista": s.gestor_nombre,
                "estado": s.estado,
                "destino": s.destino_final or s.destino_previsto or "Pendiente",
                "matricula": s.vehiculo_matricula or "Sin matricula",
            }
            for s in solicitudes[:20]
        ],
        "can_view_sensitive": institutional_can_view_sensitive(user),
        "can_view_prices": institutional_can_view_prices(user),
    }


def institutional_detail_context(user, solicitud):
    context = {
        "solicitud": solicitud,
        "timeline": build_timeline(solicitud),
        "estado_documental_label": estado_documental_label(solicitud.estado_documental),
        "observaciones": sorted(solicitud.observaciones_institucionales, key=lambda item: item.creado, reverse=True),
        "can_view_sensitive": institutional_can_view_sensitive(user),
        "can_view_prices": institutional_can_view_prices(user),
    }
    return context


def institutional_report_rows(user, filters=None):
    solicitudes = institutional_filtered_solicitudes(filters or {})
    rows = []
    for solicitud in solicitudes:
        rows.append(
            {
                "id": solicitud.id,
                "fecha": solicitud.fecha_label,
                "empresa": solicitud.empresa,
                "rut": solicitud.owner.rut if solicitud.owner else "",
                "zona": solicitud.ubicacion,
                "direccion": solicitud.direccion_retiro or "",
                "transportista": solicitud.gestor_nombre,
                "matricula": solicitud.vehiculo_matricula or "",
                "residuo": solicitud.tipo,
                "categoria": solicitud.categoria_residuo or "",
                "peso_estimado": round(solicitud.peso_estimado or 0, 2),
                "peso_real": round(solicitud.peso_real or 0, 2) if solicitud.peso_real else "",
                "estado": solicitud.estado,
                "estado_documental": estado_documental_label(solicitud.estado_documental),
                "contrato_vigente": "Si" if solicitud.contrato_vigente else "No",
                "destino_previsto": solicitud.destino_previsto or "",
                "destino_real": solicitud.destino_final or "",
                "precio_total": round(solicitud.total_economico_vigente or 0, 2) if institutional_can_view_prices(user) else "",
            }
        )
    return rows


def gestor_options():
    return (
        User.query.filter_by(role="gestor", is_active=True)
        .order_by(User.empresa.asc())
        .all()
    )


def choose_best_gestor(ubicacion="", preferred_id=None, distancia_km=0.0):
    active_gestores = gestor_options()
    if not active_gestores:
        return None

    if preferred_id:
        selected = next((gestor for gestor in active_gestores if gestor.id == preferred_id), None)
        if selected:
            return selected

    verified = [gestor for gestor in active_gestores if gestor.verificado_ministerio]

    if ubicacion:
        local_verified = [gestor for gestor in verified if gestor_matches_service(gestor, ubicacion, distancia_km)]
        if local_verified:
            return sorted(local_verified, key=lambda gestor: gestor.tarifa_por_kg or 0)[0]

        local_any = [gestor for gestor in active_gestores if gestor_matches_service(gestor, ubicacion, distancia_km)]
        if local_any:
            return sorted(local_any, key=lambda gestor: gestor.tarifa_por_kg or 0)[0]

    if verified:
        return sorted(verified, key=lambda gestor: gestor.tarifa_por_kg or 0)[0]

    return sorted(active_gestores, key=lambda gestor: gestor.tarifa_por_kg or 0)[0]


def choose_best_gestor_for_residue(residue_key, ubicacion="", preferred_id=None, distancia_km=0.0):
    candidates = [
        gestor for gestor in gestor_options() if operator_can_handle_residue(gestor, residue_key)
    ]
    if not candidates:
        return None

    if preferred_id:
        selected = next((gestor for gestor in candidates if gestor.id == preferred_id), None)
        if selected:
            return selected

    verified = [gestor for gestor in candidates if gestor.verificado_ministerio]

    if ubicacion:
        local_verified = [gestor for gestor in verified if gestor_matches_service(gestor, ubicacion, distancia_km)]
        if local_verified:
            return sorted(local_verified, key=lambda gestor: gestor.tarifa_por_kg or 0)[0]

        local_any = [gestor for gestor in candidates if gestor_matches_service(gestor, ubicacion, distancia_km)]
        if local_any:
            return sorted(local_any, key=lambda gestor: gestor.tarifa_por_kg or 0)[0]

    if verified:
        return sorted(verified, key=lambda gestor: gestor.tarifa_por_kg or 0)[0]

    return sorted(candidates, key=lambda gestor: gestor.tarifa_por_kg or 0)[0]


@app.route("/")
def index():
    return render_template("index.html", selected_role=request.args.get("role", "empresa"))


@app.route("/presentacion/imm")
def presentacion_imm():
    return render_template("presentacion_imm.html")


@app.route("/gestores")
def gestores():
    try:
        sync_transportistas_ministerio()
    except Exception as exc:  # pragma: no cover
        app.logger.warning("No se pudo sincronizar listado del Ministerio: %s", exc)
    return render_template("gestores.html")


@app.route("/api/gestores")
def api_gestores():
    try:
        sync_transportistas_ministerio()
    except Exception as exc:  # pragma: no cover
        app.logger.warning("No se pudo sincronizar listado del Ministerio: %s", exc)

    payload = []
    for gestor in gestor_options():
        tariff = gestor_tariff_config(gestor)
        payload.append(
            {
                "id": gestor.id,
                "nombre": gestor.nombre_visible,
                "tipo": gestor.ministerio_categorias or "General",
                "ubicacion": gestor.ubicacion or "Sin definir",
                "rating": 4.7,
                "contacto": gestor.email,
                "tarifa": round(gestor.tarifa_por_kg or 0, 2),
                "moneda": tariff["currency"],
                "tarifa_base_retiro": round(tariff["base_retiro"], 2),
                "tarifa_km": round(tariff["tarifa_km"], 2),
                "tarifa_m3": round(tariff["tarifa_m3"], 2),
                "tarifa_tratamiento_kg": round(tariff["tarifa_tratamiento_kg"], 2),
                "costo_certificado": round(tariff["costo_certificado"], 2),
                "habilitado": bool(gestor.operador_habilitado),
                "numero_habilitacion": gestor.numero_habilitacion or "No informado",
                "verificado_ministerio": bool(gestor.verificado_ministerio),
                "verificacion_observacion": gestor.verificacion_observacion or "Sin sincronizar",
                "verificacion_fuente": gestor.verificacion_fuente or "",
                "ministerio_rut": gestor.ministerio_rut or "",
                "ministerio_modalidad": gestor.ministerio_modalidad or "",
                "ministerio_estado": gestor.ministerio_estado or "",
                "ministerio_categorias": gestor.ministerio_categorias or "",
                "categorias_operativas": gestor.categorias_residuo or "",
                "zonas_cobertura": gestor.zonas_cobertura or "",
                "radio_cobertura_km": round(gestor.radio_cobertura_km or 0, 1),
                "rating_average": gestor.rating_promedio or 0,
                "rating_count": gestor.rating_cantidad,
                "verificacion_actualizada_en": (
                    gestor.verificacion_actualizada_en.strftime("%d/%m/%Y %H:%M")
                    if gestor.verificacion_actualizada_en
                    else ""
                ),
            }
        )
    return jsonify(payload)


@app.route("/api/plans")
def api_plans():
    plans = Plan.query.order_by(Plan.price_uyu.asc()).all()
    return jsonify([plan.to_dict() for plan in plans])


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated and request.method == "GET":
        flash("Ya tienes una sesion activa. Si quieres crear otra cuenta, primero cierra sesion.", "info")
        return redirect(url_for("dashboard"))

    preselected_role = request.args.get("role", "empresa")
    if request.method == "POST":
        role = request.form.get("role", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        telefono = request.form.get("telefono", "").strip()
        rut = normalize_rut(request.form.get("rut", "").strip())

        empresa = request.form.get("empresa", "").strip()
        ubicacion = request.form.get("ubicacion", "").strip()
        if role == "gestor":
            empresa = request.form.get("empresa_g", empresa).strip()
            ubicacion = request.form.get("ubicacion_g", ubicacion).strip()

        tarifa_raw = request.form.get("tarifa_por_kg", "0").strip() or "0"
        numero_habilitacion = request.form.get("numero_habilitacion", "").strip()
        categorias_operador = parse_operator_categories(
            request.form.getlist("categorias_residuo")
        )

        if not (role and email and password and empresa):
            flash("Completa los campos obligatorios.", "error")
            return redirect(url_for("register", role=role or preselected_role))

        if role not in {"empresa", "gestor"}:
            flash("Rol inválido.", "error")
            return redirect(url_for("register"))

        if User.query.filter_by(email=email).first():
            flash("Ese email ya está registrado.", "error")
            return redirect(url_for("register", role=role))

        try:
            tarifa = float(tarifa_raw)
        except ValueError:
            flash("La tarifa por kg no es válida.", "error")
            return redirect(url_for("register", role=role))

        user = User(
            email=email,
            password=generate_password_hash(password),
            role=role,
            empresa=empresa,
            rut=rut or None,
            ubicacion=ubicacion,
            telefono=telefono,
            tarifa_por_kg=tarifa if role == "gestor" else 0.0,
            operador_habilitado=bool(numero_habilitacion) if role == "gestor" else False,
            numero_habilitacion=numero_habilitacion or None,
            categorias_residuo=",".join(categorias_operador) if role == "gestor" else None,
        )
        if role == "empresa":
            user.plan = Plan.query.filter_by(name="Pago por Uso").first()

        db.session.add(user)
        db.session.commit()
        if role == "gestor":
            try:
                sync_transportistas_ministerio(force=True)
            except Exception as exc:  # pragma: no cover
                app.logger.warning("No se pudo verificar automaticamente el nuevo operador %s: %s", user.email, exc)
        login_user(user)
        flash("Cuenta creada correctamente.", "success")
        return redirect(url_for("dashboard"))

    return render_template(
        "register.html",
        preselected_role=preselected_role,
        residue_catalog=residue_options(),
        residue_groups=grouped_residue_catalog(),
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if not user or not check_password_hash(user.password, password):
            flash("Credenciales inválidas.", "error")
            return redirect(url_for("login"))

        if not user.is_active:
            flash("Tu cuenta está suspendida.", "error")
            return redirect(url_for("login"))

        login_user(user)
        flash(f"Bienvenido, {user.nombre_visible}.", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Sesión cerrada.", "success")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    if current_user.is_authenticated:
        maybe_send_operational_reminders(current_user)
    if current_user.role == "institucional":
        filters = parse_institutional_filters(request.args)
        log_access_audit(current_user, "ver_dashboard", "panel_institucional", detalle="Resumen institucional")
        return render_template(
            "dashboard_institucional.html",
            **institutional_dashboard_context(current_user, filters=filters),
        )
    if current_user.role == "gestor":
        return render_template(
            "dashboard_gestor.html",
            residue_groups=grouped_residue_catalog(),
            **gestor_stats(current_user),
        )

    if current_user.role == "admin":
        return render_template("dashboard_admin.html", **admin_context())

    estado = request.args.get("estado", "").strip() or None
    sid = request.args.get("sid", "").strip()
    selected_id = int(sid) if sid.isdigit() else None
    return render_template(
        "dashboard_empresa.html",
        **company_dashboard_context(current_user, estado=estado, selected_id=selected_id),
    )


@app.route("/empresa/perfil", methods=["POST"])
@require_roles("empresa")
def empresa_perfil():
    empresa = request.form.get("empresa", "").strip()
    contacto = request.form.get("contacto_nombre", "").strip()
    telefono = request.form.get("telefono", "").strip()
    ubicacion = request.form.get("ubicacion", "").strip()
    direccion_base = request.form.get("direccion_base", "").strip()
    email = request.form.get("email", "").strip().lower()

    if not (empresa and email and ubicacion):
        flash("Completa empresa, correo y zona principal para guardar el perfil.", "error")
        return redirect(url_for("dashboard", _anchor="perfil"))

    existing = User.query.filter(User.email == email, User.id != current_user.id).first()
    if existing:
        flash("Ese correo ya esta en uso por otra cuenta.", "error")
        return redirect(url_for("dashboard", _anchor="perfil"))

    current_user.empresa = empresa
    current_user.contacto_nombre = contacto or None
    current_user.telefono = telefono or None
    current_user.ubicacion = ubicacion
    current_user.direccion_base = direccion_base or None
    current_user.email = email
    current_user.razon_social = request.form.get("razon_social", "").strip() or current_user.razon_social
    current_user.rut = request.form.get("rut", "").strip() or None
    current_user.giro = request.form.get("giro", "").strip() or None
    current_user.condicion_tributaria = request.form.get("condicion_tributaria", "").strip() or None
    current_user.domicilio_fiscal = request.form.get("domicilio_fiscal", "").strip() or None
    current_user.contacto_facturacion = request.form.get("contacto_facturacion", "").strip() or None
    current_user.email_facturacion = request.form.get("email_facturacion", "").strip().lower() or None
    current_user.notas_operativas = request.form.get("notas_operativas", "").strip() or None
    current_user.zonas_cobertura = request.form.get("zonas_cobertura", "").strip() or None
    current_user.radio_cobertura_km = float(request.form.get("radio_cobertura_km", current_user.radio_cobertura_km or 25) or 25)
    db.session.commit()
    flash("Perfil de empresa actualizado.", "success")
    return redirect(url_for("dashboard", _anchor="perfil"))


@app.route("/gestor/perfil", methods=["POST"])
@require_roles("gestor")
def gestor_perfil():
    empresa = request.form.get("empresa", "").strip()
    ubicacion = request.form.get("ubicacion", "").strip()
    email = request.form.get("email", "").strip().lower()

    if not (empresa and ubicacion and email):
        flash("Completa nombre, correo y zona principal del operador.", "error")
        return redirect(url_for("dashboard", _anchor="perfil"))

    existing = User.query.filter(User.email == email, User.id != current_user.id).first()
    if existing:
        flash("Ese correo ya esta en uso por otra cuenta.", "error")
        return redirect(url_for("dashboard", _anchor="perfil"))

    current_user.empresa = empresa
    current_user.email = email
    current_user.ubicacion = ubicacion
    current_user.telefono = request.form.get("telefono", "").strip() or None
    current_user.contacto_nombre = request.form.get("contacto_nombre", "").strip() or None
    current_user.razon_social = request.form.get("razon_social", "").strip() or current_user.empresa
    current_user.rut = request.form.get("rut", "").strip() or current_user.rut
    current_user.giro = request.form.get("giro", "").strip() or None
    current_user.condicion_tributaria = request.form.get("condicion_tributaria", "").strip() or None
    current_user.domicilio_fiscal = request.form.get("domicilio_fiscal", "").strip() or None
    current_user.contacto_facturacion = request.form.get("contacto_facturacion", "").strip() or None
    current_user.email_facturacion = request.form.get("email_facturacion", "").strip().lower() or None
    current_user.notas_operativas = request.form.get("notas_operativas", "").strip() or None
    current_user.zonas_cobertura = request.form.get("zonas_cobertura", "").strip() or None
    current_user.radio_cobertura_km = float(request.form.get("radio_cobertura_km", current_user.radio_cobertura_km or 25) or 25)
    current_user.tarifa_base_retiro_uyu = float(request.form.get("tarifa_base_retiro_uyu", current_user.tarifa_base_retiro_uyu or 1500) or 1500)
    current_user.tarifa_por_kg = float(request.form.get("tarifa_por_kg", current_user.tarifa_por_kg or 0) or 0)
    current_user.tarifa_km_uyu = float(request.form.get("tarifa_km_uyu", current_user.tarifa_km_uyu or 45) or 45)
    current_user.tarifa_m3_uyu = float(request.form.get("tarifa_m3_uyu", current_user.tarifa_m3_uyu or 220) or 220)
    current_user.tarifa_tratamiento_kg_uyu = float(request.form.get("tarifa_tratamiento_kg_uyu", current_user.tarifa_tratamiento_kg_uyu or 0) or 0)
    current_user.costo_certificado_uyu = float(request.form.get("costo_certificado_uyu", current_user.costo_certificado_uyu or 350) or 350)
    current_user.categorias_residuo = ",".join(parse_operator_categories(request.form.getlist("categorias_residuo")))
    db.session.commit()
    flash("Perfil del operador actualizado.", "success")
    return redirect(url_for("dashboard", _anchor="perfil"))


@app.route("/cumplimiento")
@require_roles("empresa")
def cumplimiento():
    maybe_send_operational_reminders(current_user)
    estado = request.args.get("estado", "").strip() or None
    tipo = request.args.get("tipo", "").strip() or None
    return render_template(
        "cumplimiento.html",
        **company_compliance_context(current_user, estado=estado, tipo=tipo),
    )


@app.route("/declaraciones")
@require_roles("empresa")
def declaraciones():
    maybe_send_operational_reminders(current_user)
    periodo = request.args.get("periodo", "").strip() or declaration_period_label()
    return render_template(
        "declaraciones_empresa.html",
        **company_declaration_context(current_user, periodo=periodo),
    )


@app.route("/declaraciones/<organismo>", methods=["POST"])
@require_roles("empresa")
def declaraciones_guardar(organismo):
    if organismo not in DECLARATION_ENTITY_CONFIG:
        abort(404)
    periodo = request.form.get("periodo", "").strip() or declaration_period_label()
    declaration = get_company_declaration(current_user, organismo, periodo)
    declaration.estado = request.form.get("estado", "faltan_datos").strip() or "faltan_datos"
    declaration.firmante_nombre = request.form.get("firmante_nombre", "").strip() or None
    declaration.firmante_documento = request.form.get("firmante_documento", "").strip() or None
    declaration.firmante_cargo = request.form.get("firmante_cargo", "").strip() or None
    declaration.apoderado_nombre = request.form.get("apoderado_nombre", "").strip() or None
    declaration.apoderado_documento = request.form.get("apoderado_documento", "").strip() or None
    declaration.observaciones = request.form.get("observaciones", "").strip() or None

    objetivo_raw = request.form.get("fecha_objetivo", "").strip()
    presentacion_raw = request.form.get("fecha_presentacion", "").strip()
    declaration.fecha_objetivo = datetime.strptime(objetivo_raw, "%Y-%m-%d").date() if objetivo_raw else None
    declaration.fecha_presentacion = (
        datetime.strptime(presentacion_raw, "%Y-%m-%d").date() if presentacion_raw else None
    )
    file_key = f"{current_user.id}_{organismo}_{periodo}"
    declaration.contrato_transporte = (
        save_uploaded_file(request.files.get("contrato_transporte"), file_key, "contrato")
        or declaration.contrato_transporte
    )
    declaration.formulario_borrador = (
        save_uploaded_file(request.files.get("formulario_borrador"), file_key, "formulario")
        or declaration.formulario_borrador
    )
    declaration.poder_documento = (
        save_uploaded_file(request.files.get("poder_documento"), file_key, "poder")
        or declaration.poder_documento
    )
    declaration.respaldo_extra = (
        save_uploaded_file(request.files.get("respaldo_extra"), file_key, "respaldo")
        or declaration.respaldo_extra
    )
    db.session.commit()
    flash(f"Expediente {DECLARATION_ENTITY_CONFIG[organismo]['short_label']} actualizado.", "success")
    return redirect(url_for("declaraciones", periodo=periodo, _anchor=organismo))


@app.route("/declaraciones/<organismo>/exportar")
@require_roles("empresa")
def declaraciones_exportar(organismo):
    if organismo not in DECLARATION_ENTITY_CONFIG:
        abort(404)
    periodo = request.args.get("periodo", "").strip() or declaration_period_label()
    context = company_declaration_context(current_user, periodo=periodo)
    entry = next((item for item in context["declaraciones"] if item["organismo"] == organismo), None)
    if not entry:
        abort(404)
    declaration = entry["record"]
    lines = [
        f"Residuos 360 - Expediente regulatorio {entry['config']['label']}",
        f"Periodo: {periodo}",
        f"Empresa: {current_user.razon_social or current_user.empresa or current_user.email}",
        f"RUT: {current_user.rut or 'Sin RUT cargado'}",
        f"Estado: {entry['state_label']}",
        f"Firmante: {declaration.firmante_nombre or 'Sin definir'}",
        f"Documento firmante: {declaration.firmante_documento or 'Sin definir'}",
        f"Cargo: {declaration.firmante_cargo or 'Sin definir'}",
        f"Fecha objetivo: {declaration.fecha_objetivo.strftime('%d/%m/%Y') if declaration.fecha_objetivo else 'Sin definir'}",
        f"Fecha presentacion: {declaration.fecha_presentacion.strftime('%d/%m/%Y') if declaration.fecha_presentacion else 'Sin informar'}",
        "",
        "Resumen conciliado",
        f"Solicitudes consideradas: {entry['requests_count']}",
        f"Operadores involucrados: {entry['operators_count']}",
        f"Peso total: {entry['total_weight']:.2f} kg",
        f"Monto total: UYU {entry['total_amount']:.2f}",
        f"Legajos documentales completos: {entry['documented_count']}",
        "",
        "Checklist",
    ]
    for item in entry["checklist"]:
        status = "OK" if item["ok"] else "PENDIENTE"
        lines.append(f"- [{status}] {item['label']}: {item['detail']}")
    lines.extend(
        [
            "",
            "Adjuntos del expediente",
            f"- Contrato: {'Cargado' if declaration.contrato_transporte else 'Falta'}",
            f"- Formulario: {'Cargado' if declaration.formulario_borrador else 'Falta'}",
            f"- Poder/Firmante: {'Cargado' if declaration.poder_documento else 'Falta'}",
            f"- Respaldo extra: {'Cargado' if declaration.respaldo_extra else 'Falta'}",
            "",
            f"Observaciones: {declaration.observaciones or 'Sin observaciones'}",
        ]
    )
    body = "\n".join(lines)
    filename = f"expediente_{organismo}_{periodo}.txt"
    return Response(
        body,
        mimetype="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/contratos")
@login_required
def contratos():
    maybe_send_operational_reminders(current_user)
    context = contract_summary_context(current_user)
    if current_user.role == "empresa":
        return render_template("contratos_empresa.html", **context)
    if current_user.role == "gestor":
        return render_template("contratos_gestor.html", **context)
    return render_template("dashboard_admin.html", **admin_context())


def build_contract_preview_context(empresa_user, gestor, args):
    contract = ensure_contract_for_pair(empresa_user, gestor)
    sync_contract_expiry_state(contract)

    residue_key = args.get("tipo", "").strip()
    residue = get_residue_option(residue_key) if residue_key else None
    direccion = args.get("direccion_retiro", "").strip()
    ubicacion = args.get("ubicacion", "").strip()
    fecha_raw = args.get("fecha", "").strip()
    fecha_retiro = None
    if fecha_raw:
        try:
            fecha_retiro = datetime.strptime(fecha_raw, "%Y-%m-%d").date()
        except ValueError:
            fecha_retiro = None

    def float_arg(name):
        raw = args.get(name, "").strip()
        if not raw:
            return 0.0
        try:
            return float(raw)
        except ValueError:
            return 0.0

    peso_estimado = float_arg("peso_estimado")
    volumen_estimado_m3 = float_arg("volumen_estimado_m3")
    distancia_km = float_arg("distancia_km")

    quote_preview = None
    if residue and peso_estimado > 0:
        quote_preview = quote_service_breakdown(
            gestor,
            residue,
            peso_estimado,
            volumen_estimado_m3=volumen_estimado_m3,
            distancia_km=distancia_km,
            commission_rate=(empresa_user.plan.commission_rate if empresa_user.plan else 0.20),
        )

    export_url = url_for(
        "contrato_preview_exportar",
        gestor_id=gestor.id,
        **args.to_dict(flat=True),
    )

    return {
        "contract": contract,
        "empresa": empresa_user,
        "gestor": gestor,
        "residue": residue,
        "direccion": direccion,
        "ubicacion": ubicacion,
        "fecha_retiro": fecha_retiro,
        "franja_horaria": args.get("franja_horaria", "").strip(),
        "peso_estimado": peso_estimado,
        "volumen_estimado_m3": volumen_estimado_m3,
        "distancia_km": distancia_km,
        "quote_preview": quote_preview,
        "export_url": export_url,
    }


def pdf_safe_text(value):
    text = str(value or "")
    text = (
        unicodedata.normalize("NFKD", text)
        .encode("latin-1", "ignore")
        .decode("latin-1")
    )
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def contract_preview_pdf_entries(context):
    contract = context["contract"]
    empresa = context["empresa"]
    gestor = context["gestor"]
    residue = context["residue"]
    quote_preview = context["quote_preview"]
    volumen_estimado_m3 = context["volumen_estimado_m3"]
    fecha_retiro = context["fecha_retiro"]

    weight_line = f"Peso estimado: {context['peso_estimado'] or 0:.2f} kg"
    if volumen_estimado_m3:
        weight_line += f" - Volumen: {volumen_estimado_m3:.2f} m3"

    entries = [
        {"text": "Documento exportado desde Residuos 360", "font": "F1", "size": 10, "gap": 12},
        {
            "text": "Contrato Marco de Recoleccion y Transporte de Residuos No Domiciliarios",
            "font": "F2",
            "size": 18,
            "gap": 16,
        },
        {
            "text": f"Estado actual del marco: {contract_state_label(contract.estado)}",
            "font": "F1",
            "size": 11,
            "gap": 16,
        },
        {"text": "Empresa generadora", "font": "F2", "size": 13, "gap": 8},
        {"text": empresa.razon_social or empresa.nombre_visible, "font": "F1", "size": 11, "gap": 6},
        {"text": f"RUT: {empresa.rut or 'No informado'}", "font": "F1", "size": 11, "gap": 6},
        {
            "text": f"Domicilio: {empresa.domicilio_fiscal or empresa.direccion_base or empresa.ubicacion or 'No informado'}",
            "font": "F1",
            "size": 11,
            "gap": 14,
        },
        {"text": "Transportista habilitado", "font": "F2", "size": 13, "gap": 8},
        {"text": gestor.razon_social or gestor.nombre_visible, "font": "F1", "size": 11, "gap": 6},
        {"text": f"RUT: {gestor.rut or gestor.ministerio_rut or 'No informado'}", "font": "F1", "size": 11, "gap": 6},
        {
            "text": f"Habilitacion: {gestor.numero_habilitacion or gestor.ministerio_estado or 'No informada'}",
            "font": "F1",
            "size": 11,
            "gap": 14,
        },
        {"text": "Vigencia prevista", "font": "F2", "size": 13, "gap": 8},
        {
            "text": (
                f"{contract.vigencia_desde.strftime('%d/%m/%Y') if contract.vigencia_desde else 'A definir'}"
                f" al {contract.vigencia_hasta.strftime('%d/%m/%Y') if contract.vigencia_hasta else 'A definir'}"
            ),
            "font": "F1",
            "size": 11,
            "gap": 6,
        },
        {
            "text": f"Proveedor de firma: {contract.proveedor_firma or 'firma_avanzada_externa'}",
            "font": "F1",
            "size": 11,
            "gap": 6,
        },
        {
            "text": f"Hash de referencia: {contract.contrato_hash or 'Pendiente'}",
            "font": "F1",
            "size": 11,
            "gap": 16,
        },
        {"text": "1. Objeto", "font": "F2", "size": 13, "gap": 8},
        {
            "text": (
                "El presente contrato marco regula la relacion entre la empresa generadora y el "
                "transportista habilitado para coordinar retiros de residuos no domiciliarios "
                "mediante la plataforma Residuos 360."
            ),
            "font": "F1",
            "size": 11,
            "gap": 12,
        },
        {"text": "2. Alcance y partes", "font": "F2", "size": 13, "gap": 8},
        {
            "text": (
                "La empresa generadora declara ser responsable de los residuos que entrega, y el "
                "transportista declara encontrarse habilitado para realizar las tareas de retiro y "
                "traslado conforme a la normativa aplicable."
            ),
            "font": "F1",
            "size": 11,
            "gap": 8,
        },
        {
            "text": (
                "Residuos 360 actua exclusivamente como plataforma de gestion documental, "
                "trazabilidad, coordinacion operativa y soporte contractual, sin asumir el rol "
                "de transportista ni de gestor de residuos."
            ),
            "font": "F1",
            "size": 11,
            "gap": 12,
        },
        {"text": "3. Tipos de residuos cubiertos", "font": "F2", "size": 13, "gap": 8},
        {
            "text": contract.tipos_residuo or (residue.label if residue else "Se definiran segun cada orden de servicio emitida en la plataforma."),
            "font": "F1",
            "size": 11,
            "gap": 12,
        },
        {"text": "4. Direcciones y cobertura", "font": "F2", "size": 13, "gap": 8},
        {
            "text": f"Direcciones cubiertas por este marco: {contract.direcciones_cubiertas or empresa.direccion_base or context['direccion'] or 'A definir'}.",
            "font": "F1",
            "size": 11,
            "gap": 8,
        },
        {
            "text": (
                f"Zona de referencia del transportista: {gestor.ubicacion or 'No informada'}"
                f"{'. Cobertura declarada: ' + gestor.zonas_cobertura if gestor.zonas_cobertura else '.'}"
            ),
            "font": "F1",
            "size": 11,
            "gap": 12,
        },
        {"text": "5. Obligaciones documentales", "font": "F2", "size": 13, "gap": 8},
        {
            "text": (
                "Cada retiro quedara respaldado por una orden de servicio, constancia de retiro "
                "y cierre documental con trazabilidad, incluyendo evidencia, peso y destino final "
                "cuando corresponda."
            ),
            "font": "F1",
            "size": 11,
            "gap": 12,
        },
        {"text": "6. Uso de Residuos 360", "font": "F2", "size": 13, "gap": 8},
        {
            "text": (
                "Las partes aceptan utilizar Residuos 360 para generar, almacenar y consultar "
                "documentos, evidencia, eventos operativos y constancias asociadas a cada retiro."
            ),
            "font": "F1",
            "size": 11,
            "gap": 12,
        },
        {"text": "7. Firma electronica avanzada", "font": "F2", "size": 13, "gap": 8},
        {
            "text": (
                "El contrato marco y las ordenes de servicio podran ser firmadas electronicamente "
                "por representantes autorizados, conservando trazabilidad de version, firmantes, "
                "fecha y soporte documental."
            ),
            "font": "F1",
            "size": 11,
            "gap": 16,
        },
        {"text": "Anexo preliminar del retiro", "font": "F2", "size": 15, "gap": 10},
        {"text": "Orden de Servicio asociada a esta solicitud", "font": "F2", "size": 13, "gap": 10},
        {"text": f"Residuo: {residue.label if residue else 'No definido aun'}", "font": "F1", "size": 11, "gap": 6},
        {"text": f"Riesgo: {residue.risk if residue else 'Pendiente'}", "font": "F1", "size": 11, "gap": 6},
        {"text": f"Origen del retiro: {context['direccion'] or 'Pendiente'}", "font": "F1", "size": 11, "gap": 6},
        {"text": f"Zona: {context['ubicacion'] or 'Pendiente'}", "font": "F1", "size": 11, "gap": 6},
        {
            "text": f"Fecha de retiro: {fecha_retiro.strftime('%d/%m/%Y') if fecha_retiro else 'Pendiente'}",
            "font": "F1",
            "size": 11,
            "gap": 6,
        },
        {"text": f"Franja horaria: {context['franja_horaria'] or 'Pendiente'}", "font": "F1", "size": 11, "gap": 6},
        {"text": weight_line, "font": "F1", "size": 11, "gap": 10},
    ]

    if quote_preview:
        entries.extend(
            [
                {"text": f"Transporte: UYU {quote_preview.transport_total or 0:.2f}", "font": "F1", "size": 11, "gap": 6},
                {"text": f"Gestion / tratamiento: UYU {quote_preview.management_total or 0:.2f}", "font": "F1", "size": 11, "gap": 6},
                {"text": f"Certificado: UYU {quote_preview.certificate_total or 0:.2f}", "font": "F1", "size": 11, "gap": 6},
                {"text": f"Fee plataforma: UYU {quote_preview.platform_fee or 0:.2f}", "font": "F1", "size": 11, "gap": 6},
                {"text": f"Total estimado: UYU {quote_preview.total or 0:.2f}", "font": "F2", "size": 12, "gap": 14},
            ]
        )

    entries.extend(
        [
            {"text": "Firma empresa generadora: ________________________________", "font": "F1", "size": 11, "gap": 22},
            {"text": "Firma transportista habilitado: ____________________________", "font": "F1", "size": 11, "gap": 16},
        ]
    )
    return entries


def render_basic_pdf(entries, title="documento"):
    page_width = 595
    page_height = 842
    left_margin = 52
    top_margin = 790
    bottom_margin = 58
    max_chars = 92
    pages = []
    current_page = []
    y = top_margin

    for entry in entries:
        wrapped_lines = textwrap.wrap(str(entry["text"]), width=max_chars) or [""]
        line_height = max(14, int(entry["size"] * 1.45))
        block_height = len(wrapped_lines) * line_height + entry.get("gap", 6)
        if current_page and y - block_height < bottom_margin:
            pages.append(current_page)
            current_page = []
            y = top_margin

        for line in wrapped_lines:
            current_page.append(
                {
                    "font": entry.get("font", "F1"),
                    "size": entry.get("size", 11),
                    "x": left_margin,
                    "y": y,
                    "text": line,
                }
            )
            y -= line_height
        y -= entry.get("gap", 6)

    if current_page:
        pages.append(current_page)

    objects = []
    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    objects.append("<< /Type /Pages /Kids [] /Count 0 >>")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    page_object_ids = []

    for page in pages:
        stream_lines = []
        for item in page:
            stream_lines.append("BT")
            stream_lines.append(f"/{item['font']} {item['size']} Tf")
            stream_lines.append(f"1 0 0 1 {item['x']} {item['y']} Tm")
            stream_lines.append(f"({pdf_safe_text(item['text'])}) Tj")
            stream_lines.append("ET")
        stream_text = "\n".join(stream_lines).encode("latin-1", "ignore")
        content_id = len(objects) + 1
        page_id = len(objects) + 2
        objects.append(f"<< /Length {len(stream_text)} >>\nstream\n{stream_text.decode('latin-1')}\nendstream")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>"
        )
        page_object_ids.append(page_id)

    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{pid} 0 R' for pid in page_object_ids)}] /Count {len(page_object_ids)} >>"

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("latin-1"))
        pdf.extend(obj.encode("latin-1"))
        pdf.extend(b"\nendobj\n")

    xref_position = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info "
            f"<< /Title ({pdf_safe_text(title)}) >> >>\nstartxref\n{xref_position}\n%%EOF"
        ).encode("latin-1")
    )
    return bytes(pdf)


@app.route("/contratos/preview/<int:gestor_id>")
@require_roles("empresa")
def contrato_preview(gestor_id):
    gestor = db.session.get(User, gestor_id)
    if not gestor or gestor.role != "gestor":
        abort(404)
    return render_template(
        "contrato_preview.html",
        **build_contract_preview_context(current_user, gestor, request.args),
    )


@app.route("/contratos/preview/<int:gestor_id>/exportar")
@require_roles("empresa")
def contrato_preview_exportar(gestor_id):
    gestor = db.session.get(User, gestor_id)
    if not gestor or gestor.role != "gestor":
        abort(404)
    context = build_contract_preview_context(current_user, gestor, request.args)
    empresa_slug = normalize_company_name(current_user.nombre_visible or "empresa").replace(" ", "-")
    gestor_slug = normalize_company_name(gestor.nombre_visible or "transportista").replace(" ", "-")
    filename = f"contrato-marco-{empresa_slug}-{gestor_slug}.pdf"
    pdf_bytes = render_basic_pdf(
        contract_preview_pdf_entries(context),
        title="Contrato Marco Residuos 360",
    )
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/contratos/marco/<int:gestor_id>/crear", methods=["POST"])
@require_roles("empresa")
def contrato_crear(gestor_id):
    gestor = db.session.get(User, gestor_id)
    if not gestor or gestor.role != "gestor":
        abort(404)
    contract = ensure_contract_for_pair(current_user, gestor)
    contract.tipos_residuo = request.form.get("tipos_residuo", "").strip() or contract.tipos_residuo
    contract.direcciones_cubiertas = (
        request.form.get("direcciones_cubiertas", "").strip()
        or current_user.direccion_base
        or contract.direcciones_cubiertas
    )
    vigencia_desde = request.form.get("vigencia_desde", "").strip()
    vigencia_hasta = request.form.get("vigencia_hasta", "").strip()
    if vigencia_desde:
        contract.vigencia_desde = datetime.strptime(vigencia_desde, "%Y-%m-%d").date()
    if vigencia_hasta:
        contract.vigencia_hasta = datetime.strptime(vigencia_hasta, "%Y-%m-%d").date()
    contract.observaciones = request.form.get("observaciones", "").strip() or contract.observaciones
    contract.contrato_borrador = (
        save_uploaded_file(request.files.get("contrato_borrador"), f"contract_{contract.id}", "draft")
        or contract.contrato_borrador
    )
    if contract.estado == "pendiente_datos":
        contract.estado = "pendiente_firma_empresa"
    db.session.commit()
    flash(f"Contrato marco preparado con {gestor.nombre_visible}.", "success")
    return redirect(url_for("contratos"))


@app.route("/contratos/marco/<int:cid>/empresa-firmar", methods=["GET", "POST"])
@require_roles("empresa")
def contrato_firmar_empresa(cid):
    contract = db.session.get(ContratoMarco, cid)
    if not contract or contract.empresa_user_id != current_user.id:
        abort(404)
    if request.method == "GET":
        flash("Para registrar la firma debes adjuntar el PDF firmado desde la ficha del contrato.", "info")
        return redirect(url_for("contratos") + f"#contrato-{cid}")
    signed = request.files.get("contrato_firmado")
    if not signed or not signed.filename:
        flash("Adjunta el PDF firmado por la empresa para registrar la firma avanzada externa.", "error")
        return redirect(url_for("contratos") + f"#contrato-{cid}")
    contract.contrato_firmado = save_uploaded_file(signed, f"contract_{contract.id}", "empresa_signed")
    contract.empresa_firmado_en = datetime.utcnow()
    contract.estado = "pendiente_firma_transportista"
    db.session.commit()
    flash("Firma de la empresa registrada.", "success")
    return redirect(url_for("contratos") + f"#contrato-{cid}")


@app.route("/contratos/marco/<int:cid>/gestor-firmar", methods=["GET", "POST"])
@require_roles("gestor")
def contrato_firmar_gestor(cid):
    contract = db.session.get(ContratoMarco, cid)
    if not contract or contract.gestor_user_id != current_user.id:
        abort(404)
    if request.method == "GET":
        flash("Para registrar la firma debes adjuntar el PDF firmado desde la ficha del contrato.", "info")
        return redirect(url_for("contratos") + f"#contrato-{cid}")
    signed = request.files.get("contrato_firmado")
    if not signed or not signed.filename:
        flash("Adjunta el PDF firmado por el transportista para dejar el contrato vigente.", "error")
        return redirect(url_for("contratos") + f"#contrato-{cid}")
    contract.contrato_firmado = save_uploaded_file(signed, f"contract_{contract.id}", "fully_signed")
    contract.gestor_firmado_en = datetime.utcnow()
    contract.estado = "vigente"
    for solicitud in contract.ordenes_servicio:
        if solicitud.estado == "pendiente" and solicitud.orden_servicio_estado == "pendiente_de_contrato":
            solicitud.orden_servicio_estado = "pendiente_aceptacion_transportista"
    db.session.commit()
    flash("Contrato marco marcado como vigente.", "success")
    return redirect(url_for("contratos") + f"#contrato-{cid}")


@app.route("/documentos")
@login_required
def documentos():
    maybe_send_operational_reminders(current_user)
    estado = request.args.get("estado", "").strip() or None
    if current_user.role == "empresa":
        return render_template(
            "document_center_empresa.html",
            **company_document_center_context(current_user, estado=estado),
        )
    if current_user.role == "gestor":
        return render_template(
            "document_center_gestor.html",
            **gestor_document_center_context(current_user, estado=estado),
        )
    return render_template("dashboard_admin.html", **admin_context())


@app.route("/reportes")
@require_roles("empresa")
def reportes():
    estado = request.args.get("estado", "").strip() or None
    tipo = request.args.get("tipo", "").strip() or None
    return render_template(
        "reportes_empresa.html",
        **company_reports_context(current_user, estado=estado, tipo=tipo),
    )


@app.route("/reportes/exportar")
@require_roles("empresa")
def reportes_exportar():
    estado = request.args.get("estado", "").strip() or None
    tipo = request.args.get("tipo", "").strip() or None
    context = company_reports_context(current_user, estado=estado, tipo=tipo)

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "ID",
            "Residuo",
            "Categoria",
            "Estado",
            "Operador",
            "Zona",
            "Direccion retiro",
            "Fecha",
            "Peso estimado",
            "Peso real",
            "Destino final",
            "Moneda",
            "Subtotal neto UYU",
            "IVA UYU",
            "Total UYU",
            "Liquidacion transporte UYU",
            "Liquidacion gestion UYU",
            "Fee plataforma UYU",
        ]
    )

    for solicitud in context["solicitudes"]:
        writer.writerow(
            [
                solicitud.id,
                solicitud.tipo,
                solicitud.categoria_residuo or "",
                solicitud.estado,
                solicitud.gestor_nombre,
                solicitud.ubicacion,
                solicitud.direccion_retiro or "",
                solicitud.fecha_label,
                f"{solicitud.peso_estimado:.2f}",
                f"{(solicitud.peso_real or 0):.2f}" if solicitud.peso_real else "",
                solicitud.destino_final or "",
                solicitud.moneda or "UYU",
                f"{(solicitud.subtotal_neto_uyu or solicitud.precio_estimado or 0):.2f}",
                f"{(solicitud.iva_uyu or 0):.2f}",
                f"{(solicitud.total_uyu or solicitud.precio_final or solicitud.precio_estimado or 0):.2f}",
                f"{(solicitud.liquidacion_transportista_uyu or 0):.2f}",
                f"{(solicitud.liquidacion_gestor_uyu or 0):.2f}",
                f"{(solicitud.liquidacion_plataforma_uyu or 0):.2f}",
            ]
        )

    filename = f"reportes_residuos360_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/solicitar", methods=["GET", "POST"])
@login_required
def solicitar():
    if current_user.role != "empresa":
        role_copy = {
            "gestor": {
                "eyebrow": "Acceso informativo",
                "title": "Solo las empresas generadoras pueden solicitar retiros",
                "description": (
                    "Tu perfil actual es de transportista u operador. En Residuos 360 las "
                    "solicitudes de retiro se crean desde cuentas de empresa generadora."
                ),
                "primary_label": "Ir a mi panel de operador",
                "primary_href": url_for("dashboard"),
                "secondary_label": "Ver retiros asignados",
                "secondary_href": url_for("dashboard", _anchor="solicitudes"),
            },
            "institucional": {
                "eyebrow": "Panel institucional",
                "title": "La IMM visualiza y analiza, pero no solicita retiros",
                "description": (
                    "El rol institucional esta pensado para trazabilidad, control documental, "
                    "reportes y observacion del piloto. La solicitud operativa la realiza la "
                    "empresa generadora."
                ),
                "primary_label": "Volver al panel IMM",
                "primary_href": url_for("dashboard"),
                "secondary_label": "Ver retiros registrados",
                "secondary_href": url_for("institucional_retiros"),
            },
            "admin": {
                "eyebrow": "Administracion de plataforma",
                "title": "Este perfil no crea solicitudes operativas",
                "description": (
                    "Las solicitudes de retiro se generan desde cuentas de empresa. Desde el "
                    "panel admin podes supervisar usuarios, operadores y actividad de la plataforma."
                ),
                "primary_label": "Ir al panel admin",
                "primary_href": url_for("dashboard"),
                "secondary_label": "Ver operadores",
                "secondary_href": url_for("gestores"),
            },
        }
        copy = role_copy.get(
            current_user.role,
            {
                "eyebrow": "Acceso restringido",
                "title": "Este perfil no puede solicitar retiros",
                "description": (
                    "La solicitud operativa de retiros esta reservada para empresas generadoras."
                ),
                "primary_label": "Ir a mi panel",
                "primary_href": url_for("dashboard"),
                "secondary_label": "Volver al inicio",
                "secondary_href": url_for("index"),
            },
        )
        return render_template("solicitar_restringido.html", **copy)

    tipos = residue_options()
    gestores_activos = gestor_options()
    recommended_gestor = choose_best_gestor(current_user.ubicacion or "")
    for gestor in gestores_activos:
        contract = ensure_contract_for_pair(current_user, gestor)
        gestor.preview_contract_id = contract.id
        sync_contract_expiry_state(contract)
        gestor.preview_contract_state = contract.estado
        gestor.preview_contract_label = contract_state_label(gestor.preview_contract_state)
        gestor.preview_contract_vigente = bool(contract.vigente)
        gestor.preview_contract_until = (
            contract.vigencia_hasta.strftime("%d/%m/%Y") if contract.vigencia_hasta else ""
        )

    if recommended_gestor:
        recommended_contract = ensure_contract_for_pair(current_user, recommended_gestor)
        recommended_gestor.preview_contract_id = recommended_contract.id
        sync_contract_expiry_state(recommended_contract)
        recommended_gestor.preview_contract_state = recommended_contract.estado
        recommended_gestor.preview_contract_label = contract_state_label(
            recommended_gestor.preview_contract_state
        )
        recommended_gestor.preview_contract_vigente = bool(recommended_contract.vigente)
        recommended_gestor.preview_contract_until = (
            recommended_contract.vigencia_hasta.strftime("%d/%m/%Y")
            if recommended_contract.vigencia_hasta
            else ""
        )

    if request.method == "POST":
        residue_key = request.form.get("tipo", "").strip()
        residue = get_residue_option(residue_key)
        if not residue:
            flash("Selecciona una categoria de residuo valida.", "error")
            return redirect(url_for("solicitar"))

        gestor_user = None
        gestor_email = request.form.get("gestor", "").strip().lower()
        gestor_id_raw = request.form.get("gestor_id", "").strip()
        gestor_user = User.query.filter_by(email=gestor_email, role="gestor").first()
        if not gestor_user and gestor_id_raw.isdigit():
            gestor_user = db.session.get(User, int(gestor_id_raw))
        if gestor_user and not operator_can_handle_residue(gestor_user, residue_key):
            flash(
                "Ese operador no esta habilitado en la plataforma para ese tipo de residuo.",
                "error",
            )
            return redirect(url_for("solicitar"))

        if not gestor_user:
            flash("No encontramos un transportista compatible con ese residuo.", "error")
            return redirect(url_for("solicitar"))

        try:
            peso_estimado = float(request.form.get("peso_estimado", "0"))
        except ValueError:
            flash("El peso estimado no es válido.", "error")
            return redirect(url_for("solicitar"))

        try:
            volumen_estimado_m3 = float(request.form.get("volumen_estimado_m3", "0") or 0)
            distancia_km = float(request.form.get("distancia_km", "0") or 0)
        except ValueError:
            flash("El volumen o la distancia informada no es valida.", "error")
            return redirect(url_for("solicitar"))

        fecha_raw = request.form.get("fecha", "")
        franja_horaria = request.form.get("franja_horaria", "").strip()
        fecha_retiro = None
        if fecha_raw:
            try:
                fecha_retiro = datetime.strptime(fecha_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("La fecha de retiro no es válida.", "error")
                return redirect(url_for("solicitar"))
        if franja_horaria and franja_horaria not in TIME_WINDOWS:
            flash("Selecciona una franja horaria valida.", "error")
            return redirect(url_for("solicitar"))

        precio_por_kg = gestor_user.tarifa_por_kg or 0.0
        commission_rate = (
            current_user.plan.commission_rate if current_user.plan else 0.20
        )
        breakdown = quote_service_breakdown(
            gestor_user,
            residue,
            peso_estimado,
            volumen_estimado_m3=volumen_estimado_m3,
            distancia_km=distancia_km,
            commission_rate=commission_rate,
        )
        contract = ensure_contract_for_pair(current_user, gestor_user)

        solicitud = Solicitud(
            empresa=current_user.nombre_visible,
            tipo=residue["label"],
            categoria_residuo=residue["key"],
            nivel_riesgo=residue["risk"],
            requiere_certificado=bool(residue["requires_certificate"]),
            ubicacion=request.form.get("ubicacion", "").strip(),
            direccion_retiro=request.form.get("direccion_retiro", "").strip() or None,
            volumen_estimado_m3=volumen_estimado_m3 or None,
            distancia_km=distancia_km or None,
            peso_estimado=peso_estimado,
            precio_por_kg=precio_por_kg,
            precio_estimado=breakdown["subtotal_net"],
            precio_final=breakdown["total"],
            moneda=breakdown["currency"],
            iva_tasa=breakdown["iva_rate"],
            cfe_tipo_sugerido=DEFAULT_CFE_TYPE_B2B,
            monto_transporte_uyu=breakdown["transport_total"],
            monto_gestion_uyu=breakdown["management_total"],
            monto_certificado_uyu=breakdown["certificate_total"],
            monto_plataforma_uyu=breakdown["platform_fee"],
            subtotal_neto_uyu=breakdown["subtotal_net"],
            iva_uyu=breakdown["iva_amount"],
            total_uyu=breakdown["total"],
            liquidacion_transportista_uyu=breakdown["liquidation_transport"],
            liquidacion_gestor_uyu=breakdown["liquidation_operator"],
            liquidacion_plataforma_uyu=breakdown["liquidation_platform"],
            contrato_marco=contract,
            orden_servicio_estado="pendiente_de_contrato" if not contract.vigente else "pendiente_aceptacion_transportista",
            orden_servicio_hash=contract_document_hash(
                current_user.id,
                gestor_user.id,
                residue["key"],
                request.form.get("direccion_retiro", "").strip(),
                fecha_raw,
                peso_estimado,
            ),
            orden_empresa_confirmada_en=datetime.utcnow(),
            apta_declaracion=bool(contract.vigente),
            fecha_retiro=fecha_retiro,
            franja_horaria=franja_horaria or None,
            estado="pendiente",
            estado_pago="cotizado",
            estado_economico="cotizado",
            observaciones=request.form.get("observaciones", "").strip() or None,
            owner=current_user,
            gestor_user=gestor_user,
        )
        db.session.add(solicitud)
        log_solicitud_event(
            solicitud,
            "creada",
            f"Retiro cargado para {solicitud.ubicacion}.",
            actor=current_user,
        )
        log_solicitud_event(
            solicitud,
            "asignada",
            f"Operador asignado: {gestor_user.nombre_visible}.",
            actor=current_user,
        )
        log_solicitud_event(
            solicitud,
            "orden_servicio_emitida",
            "Orden de servicio emitida y vinculada al marco contractual.",
            actor=current_user,
        )
        if not contract.vigente:
            log_solicitud_event(
                solicitud,
                "marco_contractual_pendiente",
                f"Falta dejar vigente el contrato marco con {gestor_user.nombre_visible}.",
                actor=current_user,
            )
        db.session.commit()

        if stripe_enabled() and breakdown["total"] > 0:
            try:  # pragma: no cover
                stripe.PaymentIntent.create(
                    amount=int(breakdown["total"] * 100),
                    currency="uyu",
                    metadata={
                        "solicitud_id": solicitud.id,
                        "empresa_id": current_user.id,
                        "gestor_id": gestor_user.id,
                        "categoria_residuo": residue["key"],
                    },
                )
                solicitud.estado_pago = "pendiente de cobro"
                db.session.commit()
            except Exception as exc:  # pragma: no cover
                app.logger.warning("Stripe no disponible para solicitud %s: %s", solicitud.id, exc)

        enviar_email(
            "Nueva solicitud de retiro",
            gestor_user.email,
            render_template("emails/solicitud_gestor.html", solicitud=solicitud),
        )
        enviar_email(
            "Solicitud registrada",
            current_user.email,
            render_template("emails/solicitud_usuario.html", solicitud=solicitud),
        )
        flash(
            (
                f"Solicitud creada y enviada a {gestor_user.nombre_visible}."
                if contract.vigente
                else f"Solicitud creada. Antes de operar debes dejar vigente el contrato marco con {gestor_user.nombre_visible}."
            ),
            "success",
        )
        return redirect(
            url_for(
                "mis_solicitudes",
                sid=solicitud.id,
                nueva=1,
                contrato="0" if not contract.vigente else "1",
            )
        )

    return render_template(
        "solicitar.html",
        tipos=tipos,
        gestores_activos=gestores_activos,
        recommended_gestor=recommended_gestor,
        time_windows=TIME_WINDOWS,
    )


@app.route("/mis-solicitudes")
@require_roles("empresa")
def mis_solicitudes():
    maybe_send_operational_reminders(current_user)
    solicitudes = (
        Solicitud.query.filter_by(user_id=current_user.id)
        .order_by(Solicitud.creado.desc())
        .all()
    )
    sid = request.args.get("sid", "").strip()
    selected_id = int(sid) if sid.isdigit() else None
    selected_solicitud = next((s for s in solicitudes if s.id == selected_id), None)
    selected_contract = selected_solicitud.contrato_marco if selected_solicitud else None
    contract_ready = request.args.get("contrato", "").strip() == "1"
    created_now = request.args.get("nueva", "").strip() == "1"
    return render_template(
        "mis_solicitudes.html",
        solicitudes=solicitudes,
        selected_solicitud=selected_solicitud,
        selected_contract=selected_contract,
        created_now=created_now,
        contract_ready=contract_ready,
    )


@app.route("/mi-plan", methods=["GET", "POST"])
@require_roles("empresa")
def mi_plan():
    plans = Plan.query.order_by(Plan.price_uyu.asc()).all()
    if request.method == "POST":
        plan_id = request.form.get("plan_id")
        plan = db.session.get(Plan, int(plan_id)) if plan_id else None
        if not plan:
            flash("Selecciona un plan válido.", "error")
            return redirect(url_for("mi_plan"))

        current_user.plan = plan
        db.session.commit()
        flash(f"Tu plan ahora es {plan.name}.", "success")
        return redirect(url_for("mi_plan"))

    return render_template("mi_plan.html", current=current_user.plan, planes=plans)


@app.route("/gestor/solicitud/<int:sid>/<accion>", methods=["POST"])
@require_roles("gestor")
def gestor_accion(sid, accion):
    solicitud = db.session.get(Solicitud, sid)
    if not solicitud or solicitud.gestor_id != current_user.id:
        abort(404)

    if accion == "aceptar" and solicitud.estado == "pendiente":
        if not solicitud.contrato_vigente:
            flash("No puedes aceptar este retiro hasta que el contrato marco con la empresa este vigente.", "error")
            return redirect(url_for("contratos"))
        vehiculo_matricula = request.form.get("vehiculo_matricula", "").strip().upper()
        vehiculo_descripcion = request.form.get("vehiculo_descripcion", "").strip()
        proveedor_rastreo = request.form.get("proveedor_rastreo", "").strip()
        solicitud.estado = "aceptada"
        solicitud.aceptada_en = datetime.utcnow()
        solicitud.programado_en = solicitud.programado_en or datetime.utcnow()
        solicitud.orden_servicio_estado = "confirmada"
        solicitud.orden_transportista_confirmada_en = datetime.utcnow()
        solicitud.apta_declaracion = True
        solicitud.estado_economico = "confirmado"
        solicitud.vehiculo_matricula = vehiculo_matricula or solicitud.vehiculo_matricula
        solicitud.vehiculo_descripcion = vehiculo_descripcion or solicitud.vehiculo_descripcion
        solicitud.proveedor_rastreo = proveedor_rastreo or solicitud.proveedor_rastreo
        log_solicitud_event(solicitud, "aceptada", "El operador confirmo la toma del retiro.", actor=current_user)
        flash(f"Solicitud #{sid} aceptada.", "success")
    elif accion == "retirar" and solicitud.estado == "aceptada":
        entregado_por = request.form.get("retiro_entregado_por", "").strip()
        recibido_por = request.form.get("retiro_recibido_por", "").strip()
        retiro_obs = request.form.get("retiro_observaciones", "").strip()
        destino_previsto = request.form.get("destino_previsto", "").strip()
        vehiculo_matricula = request.form.get("vehiculo_matricula", "").strip().upper()
        vehiculo_descripcion = request.form.get("vehiculo_descripcion", "").strip()
        evidencia_subida = request.files.get("evidencia_retiro")
        if not entregado_por:
            flash("Indica quien entrego el residuo en el punto de retiro.", "error")
            return redirect(url_for("dashboard"))
        if not recibido_por:
            flash("Indica quien recibio o conformo el retiro.", "error")
            return redirect(url_for("dashboard"))
        solicitud.estado = "retirada"
        solicitud.orden_servicio_estado = "retirado"
        solicitud.retirada_en = datetime.utcnow()
        solicitud.estado_economico = "retirado"
        solicitud.retiro_entregado_por = entregado_por
        solicitud.retiro_recibido_por = recibido_por
        solicitud.retiro_observaciones = retiro_obs or solicitud.retiro_observaciones
        solicitud.destino_previsto = destino_previsto or solicitud.destino_previsto
        solicitud.vehiculo_matricula = vehiculo_matricula or solicitud.vehiculo_matricula
        solicitud.vehiculo_descripcion = vehiculo_descripcion or solicitud.vehiculo_descripcion
        solicitud.evidencia_retiro = (
            save_uploaded_file(evidencia_subida, sid, "retiro")
            or solicitud.evidencia_retiro
        )
        log_solicitud_event(
            solicitud,
            "retirada",
            f"Retiro realizado en {solicitud.direccion_retiro or solicitud.ubicacion}. Entrego: {entregado_por}. Recibio: {recibido_por}.",
            actor=current_user,
        )
        if solicitud.evidencia_retiro:
            log_solicitud_event(
                solicitud,
                "documentacion_actualizada",
                "Evidencia de retiro cargada en sitio.",
                actor=current_user,
            )
        flash(f"Solicitud #{sid} marcada como retirada.", "success")
    elif accion == "rechazar" and solicitud.estado in {"pendiente", "aceptada"}:
        solicitud.estado = "rechazada"
        solicitud.orden_servicio_estado = "cancelado"
        solicitud.estado_economico = "cancelado"
        log_solicitud_event(solicitud, "rechazada", "El operador rechazo el retiro.", actor=current_user)
        flash(f"Solicitud #{sid} rechazada.", "success")
    else:
        flash("La acción no aplica al estado actual.", "error")
        return redirect(url_for("dashboard"))

    db.session.commit()
    notify_solicitud_event(
        solicitud,
        "aceptada" if accion == "aceptar" else "retirada" if accion == "retirar" else "rechazada",
        "Revisa el panel para ver el siguiente paso del servicio.",
    )
    return redirect(url_for("dashboard"))


@app.route("/gestor/solicitud/<int:sid>/complete", methods=["POST"])
@require_roles("gestor")
def completar_retiro(sid):
    solicitud = db.session.get(Solicitud, sid)
    if not solicitud or solicitud.gestor_id != current_user.id or solicitud.estado not in {"aceptada", "retirada"}:
        abort(403)

    try:
        peso_real = float(request.form.get("peso_real", "0"))
    except ValueError:
        flash("El peso real no es válido.", "error")
        return redirect(url_for("dashboard"))

    if peso_real <= 0:
        flash("Ingresa un peso real mayor a cero.", "error")
        return redirect(url_for("dashboard"))

    comprobante_subido = request.files.get("comprobante_peso")
    if not (comprobante_subido and comprobante_subido.filename) and not solicitud.comprobante_peso:
        flash("Para validar los kilos reales debes adjuntar un comprobante de peso.", "error")
        return redirect(url_for("dashboard"))

    certificado_subido = request.files.get("certificado_destino")
    if solicitud.requiere_certificado and not (
        (certificado_subido and certificado_subido.filename) or solicitud.certificado_destino
    ):
        flash(
            "Este retiro requiere certificado o manifiesto de destino final.",
            "error",
        )
        return redirect(url_for("dashboard"))

    residue = get_residue_option(solicitud.categoria_residuo or "")
    if not residue:
        residue = {
            "key": solicitud.categoria_residuo or "general",
            "label": solicitud.tipo,
            "risk": solicitud.nivel_riesgo or "medio",
            "requires_certificate": solicitud.requiere_certificado,
        }
    commission_rate = (
        solicitud.owner.plan.commission_rate if solicitud.owner and solicitud.owner.plan else 0.20
    )
    previous_total = solicitud.total_uyu or solicitud.precio_final or solicitud.precio_estimado or 0
    breakdown = quote_service_breakdown(
        current_user,
        residue,
        peso_real,
        volumen_estimado_m3=solicitud.volumen_estimado_m3 or 0,
        distancia_km=solicitud.distancia_km or 0,
        commission_rate=commission_rate,
    )
    diferencia = round(breakdown["total"] - previous_total, 2)
    desvio_pct = 0.0
    if solicitud.peso_estimado:
        desvio_pct = round(((peso_real - solicitud.peso_estimado) / solicitud.peso_estimado) * 100, 2)
    alerta_peso = abs(desvio_pct) >= 20

    solicitud.peso_real = peso_real
    solicitud.alerta_peso = alerta_peso
    solicitud.desvio_peso_pct = desvio_pct
    solicitud.precio_final = breakdown["total"]
    solicitud.moneda = breakdown["currency"]
    solicitud.iva_tasa = breakdown["iva_rate"]
    solicitud.cfe_tipo_sugerido = DEFAULT_CFE_TYPE_B2B
    solicitud.subtotal_final_uyu = breakdown["subtotal_net"]
    solicitud.iva_final_uyu = breakdown["iva_amount"]
    solicitud.total_final_uyu = breakdown["total"]
    solicitud.liquidacion_transportista_final_uyu = breakdown["liquidation_transport"]
    solicitud.liquidacion_gestor_final_uyu = breakdown["liquidation_operator"]
    solicitud.liquidacion_plataforma_final_uyu = breakdown["liquidation_platform"]
    solicitud.ajuste_economico_uyu = diferencia
    solicitud.estado = "completada"
    solicitud.orden_servicio_estado = "cerrado"
    solicitud.estado_pago = "ajustado" if diferencia else "cotizado"
    solicitud.estado_economico = "facturable"
    solicitud.destino_final = request.form.get("destino_final", "").strip() or None
    solicitud.observaciones = request.form.get("observaciones", "").strip() or solicitud.observaciones
    solicitud.completada_en = datetime.utcnow()
    if not solicitud.retirada_en:
        solicitud.retirada_en = solicitud.completada_en
    solicitud.comprobante_peso = (
        save_uploaded_file(comprobante_subido, sid, "peso")
        or solicitud.comprobante_peso
    )
    solicitud.evidencia_retiro = (
        save_uploaded_file(request.files.get("evidencia_retiro"), sid, "retiro")
        or solicitud.evidencia_retiro
    )
    solicitud.certificado_destino = (
        save_uploaded_file(certificado_subido, sid, "destino")
        or solicitud.certificado_destino
    )
    solicitud.apta_declaracion = bool(solicitud.contrato_vigente and solicitud.documentacion_completa)
    log_solicitud_event(
        solicitud,
        "completada",
        f"Cierre con peso real {peso_real:.2f} kg y destino {solicitud.destino_final or 'informado'}.",
        actor=current_user,
    )
    if solicitud.comprobante_peso or solicitud.evidencia_retiro or solicitud.certificado_destino:
        log_solicitud_event(
            solicitud,
            "documentacion_actualizada",
            ", ".join(
                part for part in [
                    "Comprobante de peso" if solicitud.comprobante_peso else "",
                    "Evidencia de retiro" if solicitud.evidencia_retiro else "",
                    "Certificado de destino" if solicitud.certificado_destino else "",
                ] if part
            ),
            actor=current_user,
        )

    if diferencia > 0:
        db.session.add(
            CargoExtra(
                user_id=solicitud.user_id,
                solicitud_id=solicitud.id,
                monto_usd=diferencia,
                monto_uyu=diferencia,
            )
        )

    db.session.commit()
    notify_solicitud_event(
        solicitud,
        "completada",
        "El servicio ya tiene peso final, destino y respaldo cargado.",
    )

    enviar_email(
        f"Solicitud #{sid} completada",
        solicitud.owner.email,
        render_template(
            "emails/estado_actualizado.html",
            solicitud=solicitud,
            diferencia=diferencia,
        ),
    )
    if alerta_peso:
        flash(
            f"Retiro completado con alerta: el peso real se desvio {desvio_pct}% del estimado.",
            "success",
        )
    else:
        flash("Retiro completado correctamente.", "success")
    return redirect(url_for("dashboard"))


@app.route("/solicitudes/<int:sid>/calificar-empresa", methods=["POST"])
@require_roles("gestor")
def calificar_empresa(sid):
    solicitud = db.session.get(Solicitud, sid)
    if not solicitud or solicitud.gestor_id != current_user.id or solicitud.estado != "completada":
        abort(403)

    try:
        rating = int(request.form.get("empresa_rating", "0"))
    except ValueError:
        rating = 0

    if rating < 1 or rating > 5:
        flash("La puntuacion debe estar entre 1 y 5 estrellas.", "error")
        return redirect(url_for("dashboard"))

    solicitud.empresa_rating = rating
    solicitud.empresa_rating_comment = request.form.get("empresa_rating_comment", "").strip() or None
    solicitud.empresa_rated_en = datetime.utcnow()
    log_solicitud_event(
        solicitud,
        "empresa_calificada",
        f"Empresa puntuada con {rating} estrella(s).",
        actor=current_user,
    )
    db.session.commit()
    flash("Puntuacion de la empresa guardada.", "success")
    return redirect(url_for("dashboard"))


@app.route("/solicitudes/<int:sid>/calificar-operador", methods=["POST"])
@require_roles("empresa")
def calificar_operador(sid):
    solicitud = db.session.get(Solicitud, sid)
    if not solicitud or solicitud.user_id != current_user.id or solicitud.estado != "completada":
        abort(403)

    try:
        rating = int(request.form.get("operador_rating", "0"))
    except ValueError:
        rating = 0

    if rating < 1 or rating > 5:
        flash("La puntuacion debe estar entre 1 y 5 estrellas.", "error")
        return redirect(url_for("dashboard"))

    solicitud.operador_rating = rating
    solicitud.operador_rating_comment = request.form.get("operador_rating_comment", "").strip() or None
    solicitud.operador_rated_en = datetime.utcnow()
    log_solicitud_event(
        solicitud,
        "operador_calificado",
        f"Operador puntuado con {rating} estrella(s).",
        actor=current_user,
    )
    db.session.commit()
    flash("Puntuacion del operador guardada.", "success")
    return redirect(url_for("dashboard"))


@app.route("/solicitudes")
@require_roles("admin")
def ver_solicitudes():
    return render_template("dashboard_admin.html", **admin_context())


@app.route("/admin/change-plan", methods=["POST"])
@require_roles("admin")
def admin_change_plan():
    user_id = request.form.get("user_id")
    plan_id = request.form.get("plan_id")
    user = db.session.get(User, int(user_id)) if user_id else None
    plan = db.session.get(Plan, int(plan_id)) if plan_id else None

    if not user or not plan:
        flash("No se pudo actualizar el plan.", "error")
        return redirect(url_for("dashboard"))

    user.plan = plan
    db.session.commit()
    flash(f"Plan de {user.email} actualizado a {plan.name}.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/sync-ministerio", methods=["POST"])
@require_roles("admin")
def admin_sync_ministerio():
    try:
        result = sync_transportistas_ministerio(force=True)
        flash(
            f"Sincronización completada: {result['matched']} de {result['total']} operadores coincidieron con el Ministerio.",
            "success",
        )
    except Exception as exc:  # pragma: no cover
        app.logger.warning("Error en sync manual Ministerio: %s", exc)
        flash("No se pudo sincronizar con el listado del Ministerio.", "error")
    return redirect(url_for("dashboard"))


@app.route("/admin/importar-ministerio", methods=["POST"])
@require_roles("admin")
def admin_importar_ministerio():
    nombre = request.form.get("nombre_comercial", "").strip()
    razon_social = request.form.get("razon_social", "").strip()
    referencia = nombre or razon_social
    if not referencia:
        flash("No se pudo identificar el operador sugerido.", "error")
        return redirect(url_for("dashboard"))

    normalized = normalize_company_name(referencia)
    existing = User.query.filter_by(role="gestor").all()
    for gestor in existing:
        existing_keys = {
            normalize_company_name(gestor.empresa),
            normalize_company_name(gestor.nombre_visible),
        }
        if normalized and normalized in existing_keys:
            flash("Ese operador ya existe en la plataforma.", "info")
            return redirect(url_for("dashboard"))

    operador = User(
        email=build_placeholder_operator_email(referencia),
        password=generate_password_hash("pendiente-ministerio"),
        role="gestor",
        empresa=nombre or razon_social,
        ubicacion="Pendiente de validar",
        tarifa_por_kg=0.0,
        operador_habilitado=True,
        verificado_ministerio=True,
        verificacion_fuente=MINISTERIO_TRANSPORTE_XLSX_URL,
        verificacion_actualizada_en=datetime.utcnow(),
        verificacion_observacion="Importado desde listado oficial. Pendiente de contacto y activacion.",
        numero_habilitacion=request.form.get("rut", "").strip() or "Listado Ministerio",
        ministerio_rut=request.form.get("rut", "").strip(),
        ministerio_modalidad=request.form.get("modalidad", "").strip(),
        ministerio_estado=request.form.get("estado_habilitacion", "").strip(),
        ministerio_categorias=request.form.get("categorias", "").strip(),
        is_active=False,
    )
    db.session.add(operador)
    db.session.commit()
    flash(f"Operador sugerido precargado: {operador.nombre_visible}.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/activar-gestor/<int:uid>", methods=["POST"])
@require_roles("admin")
def admin_activar_gestor(uid):
    gestor = db.session.get(User, uid)
    if not gestor or gestor.role != "gestor":
        abort(404)

    email = request.form.get("email", "").strip().lower()
    ubicacion = request.form.get("ubicacion", "").strip()
    telefono = request.form.get("telefono", "").strip()
    password = request.form.get("password", "")
    tarifa_raw = request.form.get("tarifa_por_kg", "0").strip() or "0"
    tarifa_base_raw = request.form.get("tarifa_base_retiro_uyu", "1500").strip() or "1500"
    tarifa_km_raw = request.form.get("tarifa_km_uyu", "45").strip() or "45"
    tarifa_m3_raw = request.form.get("tarifa_m3_uyu", "220").strip() or "220"
    tarifa_tratamiento_raw = request.form.get("tarifa_tratamiento_kg_uyu", "0").strip() or "0"
    costo_certificado_raw = request.form.get("costo_certificado_uyu", "350").strip() or "350"
    radio_cobertura_raw = request.form.get("radio_cobertura_km", "25").strip() or "25"
    categorias_operador = parse_operator_categories(
        request.form.getlist("categorias_residuo")
    )

    if not (email and ubicacion and password):
        flash("Completa email, ubicacion y clave temporal para activar al operador.", "error")
        return redirect(url_for("dashboard"))

    existing = User.query.filter(User.email == email, User.id != gestor.id).first()
    if existing:
        flash("Ese email ya esta en uso por otro usuario.", "error")
        return redirect(url_for("dashboard"))

    try:
        tarifa = float(tarifa_raw)
        tarifa_base = float(tarifa_base_raw)
        tarifa_km = float(tarifa_km_raw)
        tarifa_m3 = float(tarifa_m3_raw)
        tarifa_tratamiento = float(tarifa_tratamiento_raw)
        costo_certificado = float(costo_certificado_raw)
        radio_cobertura = float(radio_cobertura_raw)
    except ValueError:
        flash("La tarifa del operador no es valida.", "error")
        return redirect(url_for("dashboard"))

    gestor.email = email
    gestor.ubicacion = ubicacion
    gestor.telefono = telefono or gestor.telefono
    gestor.password = generate_password_hash(password)
    gestor.tarifa_por_kg = tarifa
    gestor.tarifa_base_retiro_uyu = tarifa_base
    gestor.tarifa_km_uyu = tarifa_km
    gestor.tarifa_m3_uyu = tarifa_m3
    gestor.tarifa_tratamiento_kg_uyu = tarifa_tratamiento
    gestor.costo_certificado_uyu = costo_certificado
    gestor.categorias_residuo = ",".join(categorias_operador)
    gestor.razon_social = request.form.get("razon_social", "").strip() or gestor.empresa
    gestor.rut = request.form.get("rut", "").strip() or gestor.ministerio_rut or None
    gestor.giro = request.form.get("giro", "").strip() or None
    gestor.condicion_tributaria = request.form.get("condicion_tributaria", "").strip() or None
    gestor.domicilio_fiscal = request.form.get("domicilio_fiscal", "").strip() or None
    gestor.contacto_facturacion = request.form.get("contacto_facturacion", "").strip() or None
    gestor.email_facturacion = request.form.get("email_facturacion", "").strip().lower() or None
    gestor.notas_operativas = request.form.get("notas_operativas", "").strip() or None
    gestor.zonas_cobertura = request.form.get("zonas_cobertura", "").strip() or None
    gestor.radio_cobertura_km = radio_cobertura
    gestor.is_active = True
    gestor.operador_habilitado = True
    if not gestor.verificacion_observacion:
        gestor.verificacion_observacion = "Operador activado desde el panel admin."

    for solicitud in gestor.gestor_solicitudes:
        log_solicitud_event(
            solicitud,
            "activado_admin",
            f"Operador activado en plataforma para {gestor.ubicacion}.",
            actor=current_user,
        )
    db.session.commit()
    flash(f"Operador activado: {gestor.nombre_visible}.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/gestor-categorias/<int:uid>", methods=["POST"])
@require_roles("admin")
def admin_gestor_categorias(uid):
    gestor = db.session.get(User, uid)
    if not gestor or gestor.role != "gestor":
        abort(404)

    categorias_operador = parse_operator_categories(
        request.form.getlist("categorias_residuo")
    )
    gestor.categorias_residuo = ",".join(categorias_operador)
    for solicitud in gestor.gestor_solicitudes:
        log_solicitud_event(
            solicitud,
            "categorias_actualizadas",
            f"Categorias operativas actualizadas para {gestor.nombre_visible}.",
            actor=current_user,
        )
    db.session.commit()
    flash(f"Categorias actualizadas para {gestor.nombre_visible}.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/usuario/<int:uid>/<action>", methods=["POST"])
@require_roles("admin")
def admin_usuario_action(uid, action):
    user = db.session.get(User, uid)
    if not user:
        abort(404)

    if action == "suspender":
        user.is_active = False
    elif action == "reactivar":
        user.is_active = True
    else:
        abort(400)

    db.session.commit()
    flash(f"Usuario {user.email} actualizado.", "success")
    return redirect(url_for("dashboard"))


@app.route("/institucional")
@require_roles("institucional")
def institucional_dashboard():
    filters = parse_institutional_filters(request.args)
    log_access_audit(current_user, "ver_dashboard", "panel_institucional", detalle="Resumen institucional")
    return render_template(
        "dashboard_institucional.html",
        **institutional_dashboard_context(current_user, filters=filters),
    )


@app.route("/institucional/retiros")
@require_roles("institucional")
def institucional_retiros():
    filters = parse_institutional_filters(request.args)
    log_access_audit(current_user, "listar", "retiros", detalle="Listado institucional de retiros")
    return render_template(
        "institucional_retiros.html",
        **institutional_dashboard_context(current_user, filters=filters),
    )


@app.route("/institucional/retiros/<int:sid>")
@require_roles("institucional")
def institucional_retiro_detalle(sid):
    solicitud = db.session.get(Solicitud, sid)
    if not solicitud:
        abort(404)
    log_access_audit(current_user, "ver_detalle", "solicitud", entidad_id=sid, detalle=f"Solicitud #{sid}")
    return render_template(
        "institucional_retiro_detalle.html",
        **institutional_detail_context(current_user, solicitud),
    )


@app.route("/institucional/retiros/<int:sid>/observaciones", methods=["POST"])
@require_roles("institucional")
def institucional_observar_retiro(sid):
    solicitud = db.session.get(Solicitud, sid)
    if not solicitud or not institutional_allows(current_user, "tecnico", "admin_institucional"):
        abort(403)

    detalle = request.form.get("detalle", "").strip()
    tipo = request.form.get("tipo", "").strip() or "observacion_documental"
    estado = request.form.get("estado", "").strip() or "abierta"
    if not detalle:
        flash("Escribe una observacion antes de guardarla.", "error")
        return redirect(url_for("institucional_retiro_detalle", sid=sid))

    observacion = ObservacionInstitucional(
        solicitud_id=solicitud.id,
        user_id=current_user.id,
        tipo=tipo,
        estado=estado,
        detalle=detalle,
    )
    db.session.add(observacion)
    log_solicitud_event(
        solicitud,
        "observacion_institucional",
        f"{current_user.institution_label}: {detalle[:120]}",
        actor=current_user,
    )
    db.session.commit()
    log_access_audit(current_user, "crear_observacion", "solicitud", entidad_id=sid, detalle=tipo)
    flash("Observacion institucional guardada.", "success")
    return redirect(url_for("institucional_retiro_detalle", sid=sid))


@app.route("/institucional/empresas")
@require_roles("institucional")
def institucional_empresas():
    filters = parse_institutional_filters(request.args)
    context = institutional_dashboard_context(current_user, filters=filters)
    log_access_audit(current_user, "listar", "empresas", detalle="Panel institucional de empresas")
    return render_template("institucional_empresas.html", **context)


@app.route("/institucional/transportistas")
@require_roles("institucional")
def institucional_transportistas():
    filters = parse_institutional_filters(request.args)
    context = institutional_dashboard_context(current_user, filters=filters)
    log_access_audit(current_user, "listar", "transportistas", detalle="Panel institucional de transportistas")
    return render_template("institucional_transportistas.html", **context)


@app.route("/institucional/documentos")
@require_roles("institucional")
def institucional_documentos():
    filters = parse_institutional_filters(request.args)
    context = institutional_dashboard_context(current_user, filters=filters)
    log_access_audit(current_user, "listar", "documentacion", detalle="Panel documental institucional")
    return render_template("institucional_documentos.html", **context)


@app.route("/institucional/reportes")
@require_roles("institucional")
def institucional_reportes():
    filters = parse_institutional_filters(request.args)
    rows = institutional_report_rows(current_user, filters=filters)
    context = institutional_dashboard_context(current_user, filters=filters)
    context["report_rows"] = rows
    log_access_audit(current_user, "ver_reportes", "reportes", detalle="Reportes institucionales")
    return render_template("institucional_reportes.html", **context)


@app.route("/institucional/reportes/exportar")
@require_roles("institucional")
def institucional_reportes_exportar():
    filters = parse_institutional_filters(request.args)
    rows = institutional_report_rows(current_user, filters=filters)
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "ID",
            "Fecha",
            "Empresa",
            "RUT",
            "Zona",
            "Direccion",
            "Transportista",
            "Matricula",
            "Residuo",
            "Categoria",
            "Peso estimado",
            "Peso real",
            "Estado",
            "Estado documental",
            "Contrato vigente",
            "Destino previsto",
            "Destino real",
            "Total UYU",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["id"],
                row["fecha"],
                row["empresa"],
                row["rut"],
                row["zona"],
                row["direccion"],
                row["transportista"],
                row["matricula"],
                row["residuo"],
                row["categoria"],
                row["peso_estimado"],
                row["peso_real"],
                row["estado"],
                row["estado_documental"],
                row["contrato_vigente"],
                row["destino_previsto"],
                row["destino_real"],
                row["precio_total"],
            ]
        )
    log_access_audit(current_user, "exportar", "reportes", detalle="CSV institucional")
    filename = f"imm_panel_retiros_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/api/check_login")
def check_login():
    return jsonify(logged_in=current_user.is_authenticated)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
