import os
import asyncio
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, DateTime, select
from dotenv import load_dotenv

# Загрузка переменных окружения из файла .env, который находится в папке config
env_path = Path(__file__).resolve().parent.parent / "config" / ".env"
if not env_path.exists():
    raise Exception(f".env file not found at {env_path}")
load_dotenv(dotenv_path=env_path, override=True)

# Получаем строку подключения к базе данных из переменной окружения
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise Exception("DATABASE_URL is not loaded!")

# Настройка SQLAlchemy
Base = declarative_base()

class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    description = Column(String, nullable=False)
    scheduled_time = Column(DateTime, nullable=False)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Инициализация FastAPI приложения
app = FastAPI(title="Tasks Viewer")

@app.get("/", response_class=HTMLResponse)
async def read_tasks():
    """
    Отображает все сохранённые задачи в виде HTML-страницы.
    """
    async with async_session() as session:
        result = await session.execute(select(Task))
        tasks = result.scalars().all()
    html_content = "<html><head><title>Saved Tasks</title></head><body>"
    html_content += "<h1>Сохранённые задачи</h1>"
    if not tasks:
        html_content += "<p>Задачи не найдены.</p>"
    else:
        html_content += "<ul>"
        for task in tasks:
            html_content += f"<li>{task.description} — {task.scheduled_time.strftime('%Y-%m-%d %H:%M')} (User ID: {task.user_id})</li>"
        html_content += "</ul>"
    html_content += "</body></html>"
    return html_content

# Функция для инициализации базы данных (создание таблиц, если их нет)
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

if __name__ == "__main__":
    import uvicorn
    asyncio.run(init_db())
    uvicorn.run(app, host="0.0.0.0", port=8000)
