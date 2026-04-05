from aiogram.fsm.state import State, StatesGroup

class ScheduleFSM(StatesGroup):
    waiting_custom_date = State()

class EventCreation(StatesGroup):
    choosing_type = State()
    setting_deadline = State()
    setting_start = State()
    setting_end = State()
    setting_title = State()
    setting_location = State()
    setting_description = State()
    setting_color = State()
    confirming = State()

class EventManage(StatesGroup):
    selecting_event = State()
    choosing_field = State()
    entering_value = State()
    confirming_action = State()