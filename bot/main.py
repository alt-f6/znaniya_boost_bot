import os
import asyncio
from pathlib import Path
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from dotenv import load_dotenv

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, DateTime, select, delete, update

# ------------------ Настройка переменных окружения ------------------
env_path = Path(__file__).resolve().parent.parent / "config" / ".env"
load_dotenv(dotenv_path=env_path, override=True)

API_TOKEN = os.getenv("TELEGRAM_API_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# ------------------ Инициализация бота, диспетчера, планировщика и хранилища состояний ------------------
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# ------------------ Настройка базы данных ------------------
Base = declarative_base()

class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    description = Column(String, nullable=False)
    scheduled_time = Column(DateTime, nullable=False)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def add_task_db(user_id: int, description: str, scheduled_time: datetime):
    async with async_session() as session:
        new_task = Task(user_id=user_id, description=description, scheduled_time=scheduled_time)
        session.add(new_task)
        await session.commit()

async def get_tasks_db(user_id: int):
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.user_id == user_id))
        tasks = result.scalars().all()
        return tasks

async def delete_task_db(task_id: int):
    async with async_session() as session:
        stmt = delete(Task).where(Task.id == task_id)
        await session.execute(stmt)
        await session.commit()

async def update_task_db(task_id: int, new_description: str):
    async with async_session() as session:
        stmt = update(Task).where(Task.id == task_id).values(description=new_description)
        await session.execute(stmt)
        await session.commit()

# ------------------ Планировщик напоминаний ------------------
async def send_reminder(user_id: int, task_description: str):
    try:
        await bot.send_message(chat_id=user_id, text=f"🔔 Напоминание: {task_description}")
    except Exception as e:
        print(f"Ошибка отправки напоминания: {e}")

# ------------------ Определение состояний для добавления задачи ------------------
class AddTaskState(StatesGroup):
    waiting_for_details = State()  # ожидаем от пользователя ввод данных задачи

# ------------------ Главное меню (inline-клавиатура) ------------------
def main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить задачу", callback_data="menu_add_task")],
        [InlineKeyboardButton(text="📋 Просмотреть задачи", callback_data="menu_view_tasks")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="menu_help")]
    ])
    return keyboard

# ------------------ Обработчики команд ------------------
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer(
        "👋 Привет! Это ЗнанияBoost. Выберите действие:",
        reply_markup=main_menu_keyboard()
    )

# ------------------ Обработчики CallbackQuery для главного меню ------------------
@dp.callback_query(lambda c: c.data == "menu_add_task")
async def menu_add_task_handler(callback: CallbackQuery, state: FSMContext):
    # Переходим в состояние ожидания данных для задачи
    await state.set_state(AddTaskState.waiting_for_details)
    await callback.message.answer("✏ Введите задачу в формате:\n<описание задачи> <YYYY-MM-DD HH:MM>")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "menu_view_tasks")
async def menu_view_tasks_handler(callback: CallbackQuery):
    tasks = await get_tasks_db(callback.from_user.id)
    if not tasks:
        await callback.message.answer("📭 У вас нет задач.")
    else:
        for idx, task in enumerate(tasks, 1):
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_{task.id}")],
                [InlineKeyboardButton(text="✏ Редактировать", callback_data=f"edit_{task.id}")]
            ])
            await callback.message.answer(
                f"📌 {idx}. {task.description}\n⏳ {task.scheduled_time.strftime('%Y-%m-%d %H:%M')}",
                reply_markup=keyboard
            )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "menu_help")
async def menu_help_handler(callback: CallbackQuery):
    help_text = (
        "📖 *Справка*\n\n"
        "➕ *Добавить задачу*: нажмите кнопку и введите задачу в формате:\n"
        "   `<описание задачи> <YYYY-MM-DD HH:MM>`\n\n"
        "📋 *Просмотреть задачи*: нажмите кнопку для просмотра списка задач.\n\n"
        "При просмотре задач доступны кнопки для удаления и редактирования."
    )
    await callback.message.answer(help_text, parse_mode="Markdown")
    await callback.answer()

# ------------------ Обработчик для получения данных задачи (FSM) ------------------
@dp.message(AddTaskState.waiting_for_details)
async def process_task_details(message: types.Message, state: FSMContext):
    # Ожидаемый формат: <описание задачи> <YYYY-MM-DD HH:MM>
    parts = message.text.rsplit(maxsplit=2)
    if len(parts) < 2:
        await message.answer("⚠ Неверный формат. Попробуйте ещё раз:\n<описание задачи> <YYYY-MM-DD HH:MM>")
        return
    # Если в описании могут быть пробелы, то последний два элемента должны быть датой и временем
    datetime_str = " ".join(parts[-2:])
    description = message.text[:-len(datetime_str)].strip()
    try:
        scheduled_time = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M")
    except ValueError:
        await message.answer("⚠ Неверный формат даты и времени. Ожидается: YYYY-MM-DD HH:MM")
        return

    # Сохраняем задачу в БД
    await add_task_db(message.from_user.id, description, scheduled_time)

    # Планируем напоминание, если время в будущем
    if scheduled_time > datetime.now():
        scheduler.add_job(
            send_reminder,
            DateTrigger(run_date=scheduled_time),
            args=[message.from_user.id, description],
            misfire_grace_time=60
        )
        await message.answer(f"✅ Задача '{description}' добавлена! Напоминание будет отправлено в {datetime_str}.")
    else:
        await message.answer("⏳ Время задачи уже прошло. Задача добавлена без напоминания.")
    
    await state.clear()

# ------------------ Обработчики CallbackQuery для управления задачами ------------------
@dp.callback_query(lambda c: c.data.startswith("delete_"))
async def confirm_delete(callback: CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_{task_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])
    await callback.message.answer("Вы уверены, что хотите удалить эту задачу?", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("confirm_delete_"))
async def delete_task_callback(callback: CallbackQuery):
    task_id = int(callback.data.split("_")[2])
    await delete_task_db(task_id)
    await callback.message.edit_text("✅ Задача удалена.")
    await callback.answer("Задача удалена.")

@dp.callback_query(lambda c: c.data == "cancel")
async def cancel_callback(callback: CallbackQuery):
    await callback.message.edit_text("Действие отменено.")
    await callback.answer("Отмена.")

@dp.callback_query(lambda c: c.data.startswith("edit_"))
async def edit_task_callback(callback: CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split("_")[1])
    # Сохраняем ID задачи в состоянии для редактирования
    await state.update_data(edit_task_id=task_id)
    await callback.message.answer("✏ Введите новое описание задачи:")
    await callback.answer()

@dp.message(lambda message: True, AddTaskState.waiting_for_details)  # Обрабатываем текст в состоянии редактирования
async def process_new_description(message: types.Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get("edit_task_id")
    if task_id:
        await update_task_db(task_id, message.text)
        await message.answer("✅ Задача обновлена!")
        await state.clear()
    # Если это не редактирование, обработка происходит в другом хэндлере (для добавления задачи)

# ------------------ Основная функция ------------------
async def main():
    await init_db()
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
