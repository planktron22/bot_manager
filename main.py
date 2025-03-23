import os
from aiogram import Bot, Dispatcher
from aiogram.types import Message
import asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging
import pytz
from datetime import datetime, timedelta
from db import tasks_collection

API_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()
MOSCOW_TZ = pytz.timezone("Europe/Moscow")

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


class EditTask(StatesGroup):
    waiting_for_old_task = State()
    waiting_for_new_task = State()


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

    user_date = user_data["date"]
    user_time = user_data["time"]
    user_datetime = datetime.combine(user_date, user_time)
    local_deadline = MOSCOW_TZ.localize(user_datetime)

    task = {
        "user_id": message.from_user.id,
        "task": message.text,
        "deadline": local_deadline.isoformat(),
        "completed": False
    }

    await tasks_collection.insert_one(task)
    await message.answer(f"✅ Задача добавлена: {message.text} (до {local_deadline.strftime('%d.%m.%Y %H:%M')})")

    await state.clear()


@dp.message(Command("tasks"))
async def list_tasks(message: Message):
    tasks = await tasks_collection.find({"user_id": message.from_user.id}).to_list(None)

    if not tasks:
        await message.answer("У вас нет задач.")
        return

    response = ""
    now = datetime.now(MOSCOW_TZ)

    for task in tasks:
        deadline = datetime.fromisoformat(task["deadline"]).astimezone(MOSCOW_TZ)
        time_left = deadline - now
        status = "✅" if task["completed"] else "⏳"

        total_seconds = int(time_left.total_seconds())
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

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


@dp.message(Command("edit"))
async def start_edit_task(message: Message, state: FSMContext):
    await message.answer("Введите текст задачи, которую хотите изменить:")
    await state.set_state(EditTask.waiting_for_old_task)


@dp.message(EditTask.waiting_for_old_task)
async def get_old_task(message: Message, state: FSMContext):
    task = await tasks_collection.find_one({"user_id": message.from_user.id, "task": message.text})
    if not task:
        await message.answer("Задача не найдена. Попробуйте снова.")
        return

    await state.update_data(old_task=message.text)
    await message.answer("Введите новый текст для этой задачи:")
    await state.set_state(EditTask.waiting_for_new_task)


@dp.message(EditTask.waiting_for_new_task)
async def update_task(message: Message, state: FSMContext):
    data = await state.get_data()
    old_task = data.get("old_task")

    await tasks_collection.update_one(
        {"user_id": message.from_user.id, "task": old_task},
        {"$set": {"task": message.text}}
    )

    await message.answer(f"✅ Задача обновлена: '{old_task}' → '{message.text}'")
    await state.clear()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(dp.start_polling(bot))