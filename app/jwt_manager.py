import jwt
from pydantic import BaseModel
from datetime import datetime, timedelta
import os
from datetime import datetime, timezone


SECRET_KEY = os.getenv('secret')
ALGORITHM = "HS256"


class Token(BaseModel):
	access_token: str
	token_type: str


def create_token(data):
    expire = datetime.now() + timedelta(days=1)
    data.update({"exp": expire})
    encoded_jwt = jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str):
	try:
		jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
		return True
	except jwt.PyJWTError:
		return False

def token_payload(token: str):
	try:
		payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
		return payload
	except jwt.PyJWTError:
		return False
	
def token_expiration(token: str):
	payload = token_payload(token)
	if payload is False: return False
	return payload['exp'] > int(datetime.now(tz=timezone.utc).timestamp())