from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    full_name = State()
    phone = State()
    position = State()
    depot = State()


class DepotCreation(StatesGroup):
    name = State()


class BrowseSearch(StatesGroup):
    vehicle = State()
    driver = State()


class BroadcastFlow(StatesGroup):
    message = State()


class ChatFlow(StatesGroup):
    search = State()
    compose = State()


class DocFlow(StatesGroup):
    search = State()
    file = State()
    expiry = State()
