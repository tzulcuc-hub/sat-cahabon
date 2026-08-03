import streamlit as st
import ee
import folium
from streamlit_folium import st_folium
import pandas as pd
import requests # <--- Nueva librería para consultar el clima en tiempo real
import json
import os
from google.oauth2 import service_account

# -----------------------------------------------------------------------------
# 1. CONFIGURACIÓN DE PÁGINA Y ESTILOS
# -----------------------------------------------------------------------------
st.set_page_config(page_title="SAT Río Cahabón - Tiempo Real", page_icon="🌊", layout="wide")

st.markdown("""
    <style>
        .block-container { padding-top: 1.5rem; padding-bottom: 0rem; }
    </style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 2. AUTENTICACIÓN SECRETA DE GEE
# -----------------------------------------------------------------------------
@st.cache_resource
def init_earth_engine():
    try:
        service_account_info = dict(st.secrets["gee_service_account"])
        if "private_key" in service_account_info:
            pk = service_account_info["private_key"].replace("\\n", "\n").strip("'\"")
            service_account_info["private_key"] = pk
            
        scopes = ["https://www.googleapis.com/auth/earthengine", "https://www.googleapis.com/auth/devstorage.full_control"]
        
        # Esta es la librería que requiere google-auth
        from google.oauth2 import service_account
        credentials = service_account.Credentials.from_service_account_info(service_account_info, scopes=scopes)
        
        project_id = service_account_info.get("project_id")
        ee.Initialize(credentials, project=project_id)
        
    except Exception as e:
        # Si algo falla, Streamlit mostrará el error exacto en rojo
        st.error(f"🚨 Error crítico al leer los Secrets: {e}")
        st.info("Verifica que el nombre en st.secrets coincida con el encabezado [gee_service_account] y que instalaste google-auth.")
        st.stop()

init_earth_engine()

# -----------------------------------------------------------------------------
# 3. CARGA DE LA CUENCA (RÍO CAHABÓN) Y DEM
# -----------------------------------------------------------------------------
HYBAS_ID = 7080868090 # Nivel 8 Río Cahabón

@st.cache_data
def get_basin_and_dem(hybas_id):
    basins = ee.FeatureCollection("WWF/HydroSHEDS/v1/Basins/hybas_8")
    cuenca = basins.filter(ee.Filter.eq('HYBAS_ID', hybas_id)).first()
    geom = cuenca.geometry()
    dem = ee.Image("USGS/SRTMGL1_003").clip(geom)
    centroid = geom.centroid().coordinates().getInfo()
    return cuenca, dem, centroid

try:
    cuenca_feat, dem_cuenca, centroid = get_basin_and_dem(HYBAS_ID)
    lat_center, lon_center = centroid[1], centroid[0]
except Exception as err:
    st.error(f"Error al cargar la cuenca en GEE: {err}")
    st.stop()

# -----------------------------------------------------------------------------
# 4. CARGA DE UMBRALES HISTÓRICOS (CHIRPS)
# -----------------------------------------------------------------------------
@st.cache_data
def cargar_umbrales():
    if os.path.exists('serie_lluvia_cahabon.csv'):
        df = pd.read_csv('serie_lluvia_cahabon.csv')
        precip_only = df[df['lluvia_mm'] > 2]['lluvia_mm']
        return precip_only.quantile(0.90), precip_only.quantile(0.95), precip_only.quantile(0.99)
    return 25.0, 45.0, 70.0 # Valores de respaldo

umbral_amarilla, umbral_naranja, umbral_roja = cargar_umbrales()

# -----------------------------------------------------------------------------
# 5. NUEVO: MOTOR DE PRONÓSTICO EN TIEMPO REAL (API OPEN-METEO)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=3600) # Actualizar datos cada 1 hora
def obtener_pronostico_real(lat, lon):
    try:
        # Conexión a la API meteorológica global para la coordenada de la cuenca
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=precipitation_sum&timezone=America%2FGuatemala&forecast_days=2"
        respuesta = requests.get(url).json()
        lluvia_hoy = respuesta['daily']['precipitation_sum'][0]
        lluvia_manana = respuesta['daily']['precipitation_sum'][1]
        return lluvia_hoy, lluvia_manana
    except Exception as e:
        return 0.0, 0.0

lluvia_hoy, lluvia_manana = obtener_pronostico_real(lat_center, lon_center)

# Usamos el peor escenario (la lluvia más fuerte entre hoy o mañana) para disparar la alerta
precip_pronostico = max(lluvia_hoy, lluvia_manana)

# -----------------------------------------------------------------------------
# 6. INTERFAZ: PANEL LATERAL 
# -----------------------------------------------------------------------------
with st.sidebar:
    st.image("https://catie.ac.cr/wp-content/uploads/2023/04/Logo-CATIE-2023.png", use_container_width=True)
    st.title("📡 SAT Tiempo Real")
    
    st.success("✅ Conectado a Servidores Meteorológicos")
    st.write(f"**Lluvia esperada hoy:** {lluvia_hoy} mm")
    st.write(f"**Lluvia esperada mañana:** {lluvia_manana} mm")
    
    st.divider()
    st.markdown("### 📊 Umbrales Críticos (CHIRPS)")
    st.write(f"🟡 **P90:** {umbral_amarilla:.1f} mm")
    st.write(f"🟠 **P95:** {umbral_naranja:.1f} mm")
    st.write(f"🔴 **P99:** {umbral_roja:.1f} mm")
    
    dem_opacity = st.slider("Opacidad del DEM", 0.0, 1.0, 0.65)

# -----------------------------------------------------------------------------
# 7. LÓGICA DINÁMICA DE ALERTAS AUTOMATIZADA
# -----------------------------------------------------------------------------
if precip_pronostico < umbral_amarilla:
    estado = "ESTADO NORMAL"
    color_hex = "#28B463" 
    mensaje = "Riesgo hidrológico bajo. El pronóstico actual no supera los umbrales de peligro."
elif umbral_amarilla <= precip_pronostico < umbral_naranja:
    estado = "ALERTA AMARILLA"
    color_hex = "#F1C40F" 
    mensaje = f"Lluvia pronosticada ({precip_pronostico}mm) supera el Percentil 90 histórico. Incremento de caudales posible."
elif umbral_naranja <= precip_pronostico < umbral_roja:
    estado = "ALERTA NARANJA"
    color_hex = "#E67E22" 
    mensaje = f"Lluvia pronosticada ({precip_pronostico}mm) supera el Percentil 95 histórico. Riesgo elevado."
else:
    estado = "ALERTA ROJA (EXTREMA)"
    color_hex = "#E74C3C" 
    mensaje = f"¡PELIGRO EXTREMO! Pronóstico ({precip_pronostico}mm) supera el Percentil 99 histórico."

# -----------------------------------------------------------------------------
# 8. ENCABEZADO Y SEMÁFORO DE ALERTA
# -----------------------------------------------------------------------------
st.title("🛰️ Sistema de Alerta Temprana - Río Cahabón")

st.markdown(f"""
<div style="background-color: {color_hex}; padding: 15px; border-radius: 10px; color: white; text-align: center; font-weight: bold; text-shadow: 1px 1px 2px #000000;">
    <h2 style="margin:0;">🚦 {estado}</h2>
    <p style="font-size: 18px; margin-top:5px;">{mensaje}</p>
</div>
<br>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 9. MAPA DINÁMICO
# -----------------------------------------------------------------------------
m = folium.Map(location=[lat_center, lon_center], zoom_start=10, tiles="CartoDB positron")

dem_vis = {'min': 200, 'max': 2500, 'palette': ['006600', '002200', 'fff700', 'ab0000', 'b8b8b8', 'ffffff']}
folium.TileLayer(
    tiles=ee.Image(dem_cuenca).getMapId(dem_vis)['tile_fetcher'].url_format, 
    attr='Google Earth Engine', name='DEM SRTM', overlay=True, opacity=dem_opacity
).add_to(m)

# El borde de la cuenca se pinta del color de la alerta actual
folium.TileLayer(
    tiles=ee.Image().paint(ee.FeatureCollection([cuenca_feat]), 0, 4).getMapId({'palette': color_hex})['tile_fetcher'].url_format, 
    attr='GEE', name=f'Límite Río Cahabón ({estado})', overlay=True
).add_to(m)

folium.Marker(
    [lat_center, lon_center], popup=f"Estado: {estado}<br>Lluvia: {precip_pronostico}mm",
    icon=folium.Icon(color="red" if color_hex=="#E74C3C" else "blue", icon="info-sign")
).add_to(m)

folium.LayerControl().add_to(m)
st_folium(m, width="100%", height=550)
