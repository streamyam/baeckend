from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends, HTTPException, status, Header
from typing import Annotated
from websockets.exceptions import ConnectionClosed
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import select
from pydantic import BaseModel
import logging_loki
import time
import logging
import aiohttp
import json
import uuid
import string
import random
import traceback
import aiohttp
import os
import sys
import asyncio
from datetime import datetime

from database import Base, engine
from jwt_manager import create_token, verify_token, token_expiration, token_payload
import models

#FastAPI
app = FastAPI(docs_url=None, redoc_url=None, openapi_url="")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
#SQLAlchemy
Base.metadata.create_all(bind=engine)

#Logging
handler = logging_loki.LokiHandler(
    url=str(os.getenv("LOKI_URL")),
    tags={"application": "streamyam"},
    version="1"
)
logger = logging.getLogger("loki-logger")
logger.setLevel(logging.DEBUG)
logger.addHandler(handler)

def global_exception_handler(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    error_details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    logging.error(f"Global Exception: {error_details}")

sys.excepthook = global_exception_handler

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    body = await request.body()
    body_str = body.decode("utf-8") if body else None
    query_params = dict(request.query_params)
    response = await call_next(request)
    
    process_time = time.time() - start_time

    log_data = {
        "method": request.method.replace("http", "https"),
        "url": str(request.url).replace("streamyam", "streamyam.ru"),
        "query_params": query_params,
        "body": body_str,
        "status_code": response.status_code,
        "process_time": process_time,
    }

    if response.status_code >= 400:
        logger.error(f"🚨 HTTP ERROR: {log_data}")
    else:
        logger.info(f"✅ REQUEST: {log_data}")

    return response

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    error_details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    
    logger.error(f"💥 UNHANDLED EXCEPTION: {error_details}")
    
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )

widget_dict = {
    "disc": "widget.html",
    "square": "widget1.html",
    "simple": "widget2.html",
    "transparent": "widget3.html",
    "fullscreen": "widget4.html",
}

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.count: int = 0

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.count += 1
        logger.info(f"WebSocket connected. Total connections: {self.count}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            self.count -= 1
            logger.info(f"WebSocket disconnected. Total connections: {self.count}")
        else:
             logger.warning("Attempted to disconnect a websocket not in the active list.")

    async def send_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

class WidgetSettings(BaseModel):
    accentColor: str
    selectedDesign: str

async def get_current_user(authorization: Annotated[str | None, Header()] = None) -> str:
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Отсутствует заголовок Authorization",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Некорректный формат заголовка Authorization (ожидается 'Bearer token')",
            headers={"WWW-Authenticate": "Bearer"},
        )

    jwt_token = parts[1]

    if not verify_token(jwt_token) or not token_expiration(jwt_token):
         raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный или просроченный токен",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return token_payload(jwt_token)['email']

@app.get("/api/dashboard")
async def dashboard(request: Request, current_user: Annotated[str, Depends(get_current_user)]):
    session = None
    try:
        with Session(engine) as session:
            user = session.query(models.User).filter(models.User.yandex_email == current_user).first()
            if not user:
                 logger.error(f"Пользователь не найден для email: {current_user}")
                 raise HTTPException(status_code=404, detail="Пользователь не найден")

            widget = session.query(models.Widget).filter(models.Widget.widget_link == user.widget_link).first()
            if not widget:
                 logger.error(f"Виджет не найден для пользователя {current_user}, link: {user.widget_link}")
                 raise HTTPException(status_code=500, detail="Виджет пользователя не найден")

            return {
                "username": user.yandex_name,
                "link": user.widget_link,
                "selectedDesign": widget.style,
                "accentColor": widget.color
            }
    except HTTPException as e:
         logger.error(f"HTTP ошибка в /api/dashboard для {current_user}: {e.detail}")
         raise e
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"Ошибка в /api/dashboard для {current_user}: {e}\n{error_details}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")
    finally:
        if session and session.is_active:
            session.close()

@app.get("/widget/{url_key}", response_class=HTMLResponse)
async def widget(request: Request, url_key: str):
    with Session(engine) as session:
        widget_data = session.query(models.Widget).filter(models.Widget.widget_link == url_key).first()
        if widget_data:
            style = widget_data.style
            font = widget_data.font
            color = widget_data.color
            token = session.query(models.User).filter(models.User.widget_link == url_key).first().yandex_token
            session.close()
            return templates.TemplateResponse(widget_dict[style], {"request": request, "font": font, "color": color, "token": token})
        else:
            session.close()
            return "Bad widget URL"

@app.get("/api/tokencatcher_sm", response_class=HTMLResponse)
async def index():
    with open("static/tokencatcher_sm.html", "r", encoding="utf-8") as f:
        return f.read()

@app.put("/api/dashboard/save", response_class=JSONResponse)
async def save(settings: WidgetSettings, current_user: Annotated[str, Depends(get_current_user)]):
    try:
        with Session(engine) as session:
            user = session.query(models.User).filter(models.User.yandex_email == current_user).first()
            widget = session.query(models.Widget).filter(models.Widget.widget_link ==  user.widget_link).first()
            if not widget:
                 raise HTTPException(status_code=500, detail="Виджет пользователя не найден")

            widget.color = settings.accentColor
            widget.style = settings.selectedDesign
            session.commit()
        content = {"status": 200, "message": "Ok"}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in save: {e}")
        content = {"status": 500, "message": "Internal Server Error"}
    finally:
        if 'session' in locals() and session.is_active:
            session.close()

    return JSONResponse(content=content)

@app.get("/api/get_yam_token_sm", response_class=HTMLResponse)
async def get_token(access_token: str):
    jwt_token = None
    link_id = None

    async with aiohttp.ClientSession() as web_session:
        req = await web_session.get("https://login.yandex.ru/info?format=json", headers={"Authorization": f"OAuth {access_token}"})
        if req.status != 200:
             raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Невалидный токен Яндекс OAuth")
        req_data = await req.json()

        if not req_data.get('default_email'):
            logger.warning(f"Не удалось получить Email из аккаунта Яндекса (нету почты). Создаю аккаунт по id")
            email = req_data.get('id')
        else:
            email = req_data.get('default_email')

        data = {
            "email": email
        }

        if not req_data["display_name"]:
            name = "StreamYam User"
        else:
            name = req_data["display_name"]

        try:
            with Session(engine) as session:
                user = session.scalars(select(models.User).where(models.User.yandex_email == email)).first()
                if not user:
                    link_id = str(uuid.uuid4())[:8]
                    i = 0
                    while i < 10:
                        link = session.scalars(select(models.Widget).where(models.Widget.widget_link == link_id)).first()
                        if link:
                            link_id = str(uuid.uuid4())[:8]
                            i += 1
                        else:
                            break
                    if i == 10:
                        logger.error("Cannot generate unique link")
                        raise HTTPException(status_code=500, detail="Не удалось сгенерировать уникальную ссылку виджета")
                    
                    jwt_token = create_token(data)
                    db_user = models.User(yandex_email=email, yandex_name=name, yandex_token=access_token, widget_link=link_id, registered_at=datetime.now(), last_login_at=datetime.now())
                    db_widgets = models.Widget(widget_link=link_id, style="disc", font="montserrat", color="#FFBC0D")
                    session.add(db_user)
                    session.add(db_widgets)
                    session.commit()
                    user = db_user
                else:
                    jwt_token = create_token(data)
                    user.yandex_token = access_token
                    user.yandex_name = name
                    user.last_login_at = datetime.now()
                    session.commit()
        except Exception as e:
            logger.error(e)
            
    if not jwt_token or not user:
        if jwt_token is None:
            logger.error("JWT token dont generated")
        if user is None:
            logger.error("User not found or not generated")
        raise HTTPException(status_code=500, detail="Не удалось создать или обновить токен")
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Аутентификация...</title>
    </head>
    <body>
        <p>Пожалуйста подождите, происходит перенаправление на панель управления...</p>
        <script>
            try {{
                const token = '{jwt_token}';
                if (token) {{
                    localStorage.setItem('jwt_token', token);
                    window.location.href = '/dashboard';
                }} else {{
                    document.body.innerHTML = '<p>Ошибка: Не удалось получить токен аутентификации</p>';
                }}
            }} catch (error) {{
                console.error('Ошибка при сохранении токена:', error);
                document.body.innerHTML = '<p>Произошла ошибка при обработке аутентификации</p>';
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

manager = ConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    token = None
    last_track_data = {
        "track_name": "Все подробности в тг @streamyam",
        "artists": "StreamYam - закрывается",
        "cover_url": "https://streamyam.ru/img/icon.svg",
    }

    try:
        token = await websocket.receive_text()
        logger.info(f"WebSocket connected for token: {token}")

        try:
            await manager.send_message(json.dumps(last_track_data), websocket)

        except (WebSocketDisconnect, ConnectionClosed) as disconnect_exc:
             logger.warning(f"Client disconnected during or shortly after initial fetch processing for token {token}. Details: {disconnect_exc}")
             raise

        except Exception as initial_fetch_exc:
            logger.error(f"Error during initial fetch logic for token {token}: {initial_fetch_exc}", exc_info=True)
            try:
                await manager.send_message(json.dumps({"error": "initial fetch failed"}), websocket)
            except (WebSocketDisconnect, ConnectionClosed):
                 logger.warning(f"Client disconnected before initial fetch error could be sent. Token: {token}")

        while True:
            await asyncio.sleep(5)

            try:
                await manager.send_message(json.dumps(last_track_data), websocket)

            except (WebSocketDisconnect, ConnectionClosed):
                logger.info(f"WebSocket closed during update loop for token: {token}")
                raise
            except Exception as loop_fetch_exc:
                logger.error(f"Error fetching/sending update for token {token}: {loop_fetch_exc}", exc_info=True)
                try:
                    if last_track_data:
                         await manager.send_message(json.dumps(last_track_data), websocket)
                         await manager.send_message(json.dumps({"error": "update fetch failed"}), websocket)
                except (WebSocketDisconnect, ConnectionClosed):
                     logger.info(f"WebSocket already closed when trying to send fetch error for token: {token}")

    except (WebSocketDisconnect, ConnectionClosed) as ws_closed:
        details = str(ws_closed)
        if isinstance(ws_closed, WebSocketDisconnect):
            if not ws_closed.reason or not ws_closed.reason.strip():
                details = f"{ws_closed.code}: (No textual reason provided)"
        logger.info(f"WebSocket disconnected for token: {token}. Reason: {details}")
    except Exception as e:
        logger.error(f"Unexpected error in WebSocket handler for token {token}: {e}", exc_info=True)
        try:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        except Exception:
            pass
    finally:
        manager.disconnect(websocket)
        logger.info(f"WebSocket connection cleanup completed for token: {token}")


async def fetch_track_info(token: str, deviceid: str):
    try:
        device_info = {"app_name": "Chrome", "type": 1}
        ws_proto = {
            "Ynison-Device-Id": deviceid,
            "Ynison-Device-Info": json.dumps(device_info),
        }
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                url="wss://ynison.music.yandex.ru/redirector.YnisonRedirectService/GetRedirectToYnison",
                headers={
                    "Sec-WebSocket-Protocol": f"Bearer, v2, {json.dumps(ws_proto)}",
                    "Origin": "http://music.yandex.ru",
                    "Authorization": f"OAuth {token}",
                },
            ) as ws:
                recv = await ws.receive()
                if recv is None:
                    raise Exception("No response received from initial WS connection")
                data = json.loads(recv.data)

            new_ws_proto = ws_proto.copy()
            await ws.close()
            if "redirect_ticket" not in data:
                logger.warning(f"Redirect ticket data: {data}\nUser token: {token}")
                return "bad token"
            new_ws_proto["Ynison-Redirect-Ticket"] = data["redirect_ticket"]

            to_send = {
                "update_full_state": {
                    "player_state": {
                        "player_queue": {
                            "current_playable_index": -1,
                            "entity_id": "",
                            "entity_type": "VARIOUS",
                            "playable_list": [],
                            "options": {
                                "repeat_mode": "NONE"
                            },
                            "entity_context": "BASED_ON_ENTITY_BY_DEFAULT",
                            "version": {
                                "device_id": ws_proto["Ynison-Device-Id"],
                                "version": 3487536190692794400,
                                "timestamp_ms": 0
                            },
                            "from_optional": None,
                            "initial_entity_optional": None,
                            "adding_options_optional": None,
                            "queue": None
                        },
                        "status": {
                            "duration_ms": 0,
                            "paused": True,
                            "playback_speed": 1,
                            "progress_ms": 0,
                            "version": {
                                "device_id": ws_proto["Ynison-Device-Id"],
                                "version": 761129841314803700,
                                "timestamp_ms": 0
                            }
                        }
                    },
                    "device": {
                        "capabilities": {
                            "can_be_player": True,
                            "can_be_remote_controller": False,
                            "volume_granularity": 16
                        },
                        "info": {
                            "device_id": ws_proto["Ynison-Device-Id"],
                            "type": "WEB",
                            "title": "StreamYam",
                            "app_name": "Chrome"
                        },
                        "volume_info": {
                            "volume": 0,
                            "version": None
                        },
                        "is_shadow": True
                    },
                    "is_currently_active": False
                },
                "rid": "ac281c26-a047-4419-ad00-e4fbfda1cba3",
                "player_action_timestamp_ms": 0,
                "activity_interception_type": "DO_NOT_INTERCEPT_BY_DEFAULT"
            }

            async with session.ws_connect(
                url=f"wss://{data['host']}/ynison_state.YnisonStateService/PutYnisonState",
                headers={
                    "Sec-WebSocket-Protocol": f"Bearer, v2, {json.dumps(new_ws_proto)}",
                    "Origin": "http://music.yandex.ru",
                    "Authorization": f"OAuth {token}",
                },
                method="GET"
            ) as ws:
                await ws.send_str(json.dumps(to_send))

                async for message in ws:
                    ynison = json.loads(message.data)

                    player_info = {
                        "update_playing_status": {
                            "playing_status": {
                                "progress_ms": ynison['player_state']['status']['progress_ms'],
                                "duration_ms": ynison['player_state']['status']['duration_ms'],
                                "paused": ynison["player_state"]["status"]["paused"],
                                "playback_speed": 1,
                                "version": {
                                    "device_id": ws_proto["Ynison-Device-Id"],
                                    "version": "0",
                                    "timestamp_ms": ynison["player_state"]["status"]["version"]["timestamp_ms"]
                                }
                            }
                        },
                        "rid": str(uuid.uuid4())
                    }
                    await ws.send_str(json.dumps(player_info))

                    recv = await ws.receive()

                    recv_pars = message.json()
                    title = "Музыка сейчас не играет"
                    artists = "Подождите 5 секунд для обновления"
                    cover = "/img/placeholder.svg"
                    data_json = None
                    try:
                        index = recv_pars["player_state"]["player_queue"]["current_playable_index"]
                        if index == -1:
                            pass
                        else:
                            track = recv_pars["player_state"]["player_queue"]["playable_list"][index]
                            if "album_id_optional" in track:
                                data_resp = await session.get(f"https://api.music.yandex.net/tracks?trackIds={track['playable_id']}")
                                data_json = await data_resp.json()
                                artists_list = []
                                for artist in data_json["result"][0]["artists"]:
                                    artists_list.append(artist["name"])
                                artists = ", ".join(artists_list)
                                title = track['title']
                                if "cover_url_optional" in track: #Ynison
                                    cover = "https://"+str(track['cover_url_optional']).replace('%%', '1000x1000')
                                elif 'coverUri' in data_json["result"][0]: #From API by ID
                                    cover = "https://"+str(data_json["result"][0]['coverUri']).replace('%%', '1000x1000')
                                else: #No Image
                                    cover = "/img/placeholder.svg"
                            else:
                                artists = "Исполнитель не найден"
                                title = track['title']
                                if "cover_url_optional" in track: #Ynison
                                    cover = "https://"+str(track['cover_url_optional']).replace('%%', '1000x1000')
                                else: #No Image
                                    cover = "/img/placeholder.svg"

                    except Exception as inner_e:
                        error_details = traceback.format_exc()
                        logger.warning(f"Inner exception processing track data: API recv: {data_json}\n Socket recv: {recv_pars}\n Exception: {inner_e}\n\n")

                    result = {
                        "track_name": title,
                        "artists": artists,
                        "cover_url": cover,
                    }
                    await ws.close()
                    return result

                logger.warning(f"WS2 loop did not yield any messages for token {token[:5]}...")
                raise Exception("WS2 loop did not yield messages")

    except Exception as e:
        error_details = traceback.format_exc()
        logger.warning(f"Exception in fetch_track_info: {error_details}\nUser token: {token}")
        raise e