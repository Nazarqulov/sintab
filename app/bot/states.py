from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    full_name = State()
    phone = State()
    position = State()
    depot = State()


class DepotCreation(StatesGroup):
    name = State()
