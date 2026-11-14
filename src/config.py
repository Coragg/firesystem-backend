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