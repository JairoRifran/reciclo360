# Deploy de Residuos 360

## Git

```powershell
git init
git add .
git commit -m "Preparar Residuos 360 para deploy"
git branch -M main
git remote add origin TU_URL_DE_GITHUB
git push -u origin main
```

## Vercel

1. Crear/importar proyecto desde el repositorio de GitHub.
2. Framework preset: `Other`.
3. Build command: dejar vacio.
4. Install command: `pip install -r requirements.txt`.
5. Output directory: dejar vacio.
6. Agregar variables de entorno desde `.env.example`.

## Importante para produccion

- No subir `.env`, bases `.db`, logs ni documentos cargados.
- Usar una base externa con `DATABASE_URL` para que los datos persistan.
- Los uploads locales no son persistentes en Vercel; para documentos reales conviene migrarlos a almacenamiento externo.
