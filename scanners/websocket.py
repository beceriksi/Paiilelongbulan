# scanners/websocket.py
import asyncio
import logging
import json
import websockets
from typing import Callable, Optional, Dict, Any

logger = logging.getLogger("GemBot.WebSocket")

class AsyncWebSocketClient:
    def __init__(self, uri: str, headers: Optional[Dict[str, str]] = None):
        self.uri = uri
        self.headers = headers or {}
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False

    async def connect_and_listen(self, handler: Callable[[Dict[str, Any]], asyncio.Future], subscribe_payload: Optional[Dict[str, Any]] = None):
        """Bağlantıyı kurar, isteğe bağlı abonelik mesajını gönderir ve gelen mesajları işler."""
        self._running = True
        while self._running:
            try:
                logger.info(f"WebSocket bağlantısı kuruluyor: {self.uri}")
                async with websockets.connect(self.uri, extra_headers=self.headers) as ws:
                    self.websocket = ws
                    if subscribe_payload:
                        await ws.send(json.dumps(subscribe_payload))
                        logger.info("Abonelik paketi gönderildi.")

                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(message)
                            # Handler'ı asenkron olarak arka planda çalıştır
                            asyncio.create_task(handler(data))
                        except json.JSONDecodeError:
                            logger.warning(f"Geçersiz JSON formatında mesaj alındı: {message}")
            except (websockets.ConnectionClosed, OSError) as e:
                logger.warning(f"WebSocket bağlantısı koptu: {e}. 5 saniye sonra tekrar denenecek...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"WebSocket beklenmedik hata: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def stop(self):
        self._running = False
        if self.websocket:
            await self.websocket.close()
            logger.info("WebSocket istemcisi durduruldu.")
