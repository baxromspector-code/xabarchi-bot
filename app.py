"""FPS.ms uchun kirish fayli: admin panelni serverning ommaviy portida ochadi va botni ishga tushiradi."""
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()
_port = os.getenv("PORT") or os.getenv("SERVER_PORT")  # Render / FPS.ms beradigan port
if _port:
    os.environ["ADMIN_HOST"] = "0.0.0.0"
    os.environ["ADMIN_PORT"] = _port

import bot  # noqa: E402

asyncio.run(bot.main())
