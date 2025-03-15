from math import remainder

from aiogram.fsm.storage.memory import MemoryStorage
from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
import asyncio
import motor.motor_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command

import logging
from datetime import datetime, timedelta
from db import tasks_collection

API_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


logging.basicConfig(level=logging.INFO)


class TaskState(StatesGroup):
    waiting_for_date = State()
    waiting_for_time = State()
    waiting_for_text = State()

class TaskCompleteState(StatesGroup):
    waiting_for_task_text = State()

class DeleteTaskState(StatesGroup):
    waiting_for_task_text = State()

class DeleteDateState(StatesGroup):
    waiting_for_date = State()


@dp.message(Command("add"))
async def start_add_task(message: Message, state: FSMContext):
    await message.answer("Введите дату в формате ДД.ММ.ГГГГ:")
    await state.set_state(TaskState.waiting_for_date)


@dp.message(TaskState.waiting_for_date)
async def process_date(message: Message, state: FSMContext):
    try:
        date = datetime.strptime(message.text, "%d.%m.%Y").date()
        await state.update_data(date=date)
        await message.answer("Теперь введите время в формате ЧЧ:ММ:")
        await state.set_state(TaskState.waiting_for_time)
    except ValueError:
        await message.answer("Неверный формат даты. Попробуйте снова (ДД.ММ.ГГГГ):")


@dp.message(TaskState.waiting_for_time)
async def process_time(message: Message, state: FSMContext):
    try:
        time = datetime.strptime(message.text, "%H:%M").time()
        await state.update_data(time=time)
        await message.answer("Введите текст задачи:")
        await state.set_state(TaskState.waiting_for_text)
    except ValueError:
        await message.answer("Неверный формат времени. Попробуйте снова (ЧЧ:ММ):")

@dp.message(TaskState.waiting_for_text)
async def process_task_text(message: Message, state: FSMContext):
    user_data = await state.get_data()

    deadline = datetime.combine(user_data["date"], user_data["time"])

    task = {
        "user_id": message.from_user.id,
        "task": message.text,
        "deadline": deadline.isoformat(),
        "completed": False
    }

    await tasks_collection.insert_one(task)
    await message.answer(f"✅ Задача добавлена: {message.text} (до {deadline.strftime('%d.%m.%Y %H:%M')})")

    await state.clear()

@dp.message(Command("tasks"))
async def list_tasks(message: Message):
    tasks = await tasks_collection.find({"user_id": message.from_user.id}).to_list(None)

    if not tasks:
        await message.answer("У вас нет задач.")
        return

    response = ""
    now = datetime.utcnow()

    for task in tasks:
        deadline = datetime.fromisoformat(task["deadline"])
        time_left = deadline - now
        status = "✅" if task["completed"] else "⏳"

        total_seconds = int(time_left.total_seconds())
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds =divmod(remainder, 60)

        time_left_str = f"{days} д. {hours} ч. {minutes} м. {seconds} с."

        if total_seconds < 0:
            status = "❌"
            response += f"{status} {task['task']} (ПРОСРОЧЕНО)\n"
        else:
            response += f"{status} {task['task']} (до {deadline.strftime('%d.%m.%Y %H:%M')}, осталось {time_left_str})\n"

    await message.answer(response)

@dp.message(Command("done"))
async def start_task_completion(message: Message, state: FSMContext):
    await message.answer("Введите текст задачи, которую хотите отметить как выполненную:")
    await state.set_state(TaskCompleteState.waiting_for_task_text)

@dp.message(TaskCompleteState.waiting_for_task_text)
async def complete_task(message: Message, state: FSMContext):
    task_text = message.text.strip()

    task = await tasks_collection.find_one({"user_id": message.from_user.id, "task": task_text})

    if not task:
        await message.answer("Задача не найдена. Попробуйте снова.")
        return

    await tasks_collection.update_one({"_id": task["_id"]}, {"$set": {"completed": True}})
    await message.answer(f"✅ Задача '{task_text}' выполнена!")

    await state.clear()

@dp.message(Command("delete"))
async def start_delete_task(message: Message, state: FSMContext):
    await message.answer("Введите текст задачи, которую хотите удалить:")
    await state.set_state(DeleteTaskState.waiting_for_task_text)

@dp.message(DeleteTaskState.waiting_for_task_text)
async def delete_task(message: Message, state: FSMContext):
    task_text = message.text.strip()

    result = await tasks_collection.delete_one({"user_id": message.from_user.id, "task": task_text})

    if result.deleted_count:
        await message.answer(f"🗑 Задача '{task_text}' удалена!")
    else:
        await message.answer("Задача не найдена.")

    await state.clear()


@dp.message(Command("delete_date"))
async def start_delete_tasks_by_date(message: Message, state: FSMContext):
    await message.answer("Введите дату в формате ДД.ММ.ГГГГ, для удаления всех задач за этот день:")
    await state.set_state(DeleteDateState.waiting_for_date)


@dp.message(DeleteDateState.waiting_for_date)
async def delete_tasks_by_date(message: Message, state: FSMContext):
    try:
        target_date = datetime.strptime(message.text, "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Неверный формат даты. Используйте ДД.ММ.ГГГГ")
        return

    result = await tasks_collection.delete_many({
        "user_id": message.from_user.id,
        "deadline": {"$gte": target_date.isoformat(), "$lt": (target_date + timedelta(days=1)).isoformat()}
    })

    await message.answer(f"🗑 Удалено задач: {result.deleted_count}")

    await state.clear()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(dp.start_polling(bot))
