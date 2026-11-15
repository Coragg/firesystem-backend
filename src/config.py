import os

from dotenv import load_dotenv

load_dotenv()

# NASA api
FIRMS_MAP_KEY = os.environ["FIRMS_MAP_KEY"]
FIRMS_BASE_URL = os.environ["FIRMS_BASE_URL"]

# SUPABASE
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

# jwt
SECRET_KEY = os.environ["SECRET_KEY"]
ALGORITHM = os.environ["ALGORITHM"]
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))  
# Hotspot forecast model (saved by src/notebooks/XGBoost_Hotspot_Forecast.ipynb)
HOTSPOT_MODEL_DIR = os.getenv("HOTSPOT_MODEL_DIR", "data/models/hotspots/Maule")
# Where the model's input data comes from: "api" (APPEEARS_DATA_URL endpoints) or "local" (downloaded files)
HOTSPOT_FEATURE_SOURCE = os.getenv("HOTSPOT_FEATURE_SOURCE", "api")
APPEEARS_DATA_URL = os.getenv("APPEEARS_DATA_URL", "")
APPEEARS_LOCAL_DIR = os.getenv("APPEEARS_LOCAL_DIR", "data/AppEEARS/Maule")
