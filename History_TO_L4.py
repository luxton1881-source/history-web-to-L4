import asyncio
import json
import logging
import re
import sys
from datetime import datetime


# ФИКС ДЛЯ КОНСОЛИ WINDOWS
if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import WebAppInfo, KeyboardButton, ReplyKeyboardMarkup, ReactionTypeEmoji, InputMediaPhoto, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ContentType
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
from datetime import datetime
from zoneinfo import ZoneInfo


# --- НАСТРОЙКИ ---
TOKEN = "8501497937:AAFUml_S4OadOXonkvzDvzFz0pSwIdRjkmk"
SPREADSHEET_ID = "1PfWdhYCPCM4zbhV76qk5wcXIfQZzU7qSRwLlxkN7Jx0"
CRAMER_SPREADSHEET_ID = "1esH5e9nWVuE0ZtKc3TvKxSGGKweBoNEeetnREgoekyg"
WEB_APP_URL = "https://tozvit.spservice.org.ua"

try:
    BOT_ID = int(TOKEN.split(":")[0])
except:
    BOT_ID = 0

# НАСТРОЙКА ЛОГОВ
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(message)s', 
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ПОДКЛЮЧЕНИЕ К ГУГЛУ
try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("key.json", scope)
    client = gspread.authorize(creds)
    ss = client.open_by_key(SPREADSHEET_ID)
    try:
        ss_cramer = client.open_by_key(CRAMER_SPREADSHEET_ID)
        logger.info("✅ Google Таблицы (Основная и Крамер) подключены успешно")
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПОДКЛЮЧЕНИЯ К КРАМЕРУ: {e}")
        ss_cramer = None
except Exception as e:
    logger.error(f"❌ ОШИБКА ПОДКЛЮЧЕНИЯ: {e}")
    exit()

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

USERS_CACHE = {} 
GROUPS_CONFIG = {} 
GROUPS_MAP_REVERSE = {}
CRAMER_CACHE = {}

class Form(StatesGroup):
    waiting_for_photos = State()
    waiting_for_tkd_history = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_main_kb():
    # Очистка: оставляем только две кнопки в один ряд
    kb = [[KeyboardButton(text="📜 История ТО"), KeyboardButton(text="📝 Создать отчет")]]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def reload_users_cache_sync():
    global USERS_CACHE, GROUPS_CONFIG, GROUPS_MAP_REVERSE
    try:
        print(">>> ОБНОВЛЯЮ СПИСОК СОТРУДНИКОВ...")
        sheet = ss.worksheet("Данные")
        data = sheet.get_all_values()
        
        # 1. Считываем ГРУППЫ
        new_groups_config = {} 
        new_groups_reverse = {}
        for row in data[1:]:
            if len(row) > 6:
                reg_name = row[5].strip() 
                grp_id_str = row[6].strip()
                if reg_name and grp_id_str:
                    try:
                        grp_id = int(grp_id_str)
                        new_groups_config[reg_name] = grp_id
                        new_groups_reverse[grp_id] = reg_name
                    except: pass
        
        GROUPS_CONFIG = new_groups_config
        GROUPS_MAP_REVERSE = new_groups_reverse

        # 2. Считываем ЮЗЕРОВ
        new_users = {}
        # Считаем уникальные регионы для статистики
        unique_regions = set()

        for i, row in enumerate(data[1:], start=2):
            if len(row) < 4: continue
            
            raw_regions = row[1].strip() if len(row) > 1 else ""
            fio = row[2].strip() if len(row) > 2 else "Без имени"
            tid = str(row[3]).strip() if len(row) > 3 else ""
            role = row[4].strip() if len(row) > 4 else "Мастер" 

            if not tid or not tid.isdigit(): continue
            
            regions_list = [r.strip() for r in re.split(r'[,\s]+', raw_regions) if r.strip()]
            for r in regions_list:
                unique_regions.add(r)

            new_users[tid] = {'ФИО': fio, 'ID ТГ': tid, 'Роль': role, 'Regions': regions_list}
            
        USERS_CACHE = new_users
        print(f">>> БАЗА ГОТОВА. Найдено людей: {len(USERS_CACHE)}. Групп: {len(new_groups_config)}")
        # Возвращаем кол-во пользователей и областей
        return len(USERS_CACHE), len(unique_regions)
    except Exception as e:
        print(f"!!! ОШИБКА ПРИ ЧТЕНИИ ТАБЛИЦЫ 'ДАННЫЕ': {e}")
        return 0, 0

def reload_cramer_cache_sync():
    global CRAMER_CACHE
    if not ss_cramer:
        print("!!! НЕТ ДОСТУПА К ТАБЛИЦЕ КРАМЕР")
        return 0, 0, 0, 0
    
    print(">>> ОБНОВЛЯЮ БАЗУ КРАМЕР (ТЕХ. ДАННЫЕ)...")
    try:
        # 1. Загружаем Края Колец
        total_edges = 0 # Счётчик краев
        try:
            sheet_rings = ss_cramer.worksheet("Края колец")
            rings_data = sheet_rings.get_all_values()
            ring_map = {}
            for row in rings_data[1:]:
                if len(row) > 6:
                    ip = row[2].strip()
                    ring_id = row[6].strip()
                    if ring_id and ip:
                        if ring_id not in ring_map: ring_map[ring_id] = []
                        if ip not in ring_map[ring_id]: 
                            ring_map[ring_id].append(ip)
                            total_edges += 1 # Считаем уникальные пары
            print(f">>> Загружено колец: {len(ring_map)}")
        except Exception as e:
            print(f"!!! Ошибка чтения 'Края колец': {e}")
            ring_map = {}
            total_edges = 0

        # 2. Загружаем Крамер
        sheet_cramer = ss_cramer.worksheet("Крамер")
        cramer_data = sheet_cramer.get_all_values()
        
        new_cache = {}
        total_switches = 0 # Счётчик свичей
        
        for row in cramer_data[1:]:
            if len(row) <= 16: continue
            
            sw_name = row[0].strip()
            sw_ip = row[2].strip()
            tkd_name = row[16].strip()
            
            if not tkd_name: continue 
            
            # Формируем ключ ТКД если его нет
            if tkd_name not in new_cache:
                ring_id = tkd_name[:16] if len(tkd_name) >= 16 else tkd_name
                uplinks = ring_map.get(ring_id, [])
                new_cache[tkd_name] = {
                    "uplinks": uplinks,
                    "switches": []
                }
            
            if sw_name and sw_ip:
                new_cache[tkd_name]["switches"].append({
                    "name": sw_name,
                    "ip": sw_ip
                })
                total_switches += 1
                
        CRAMER_CACHE = new_cache
        print(f">>> БАЗА КРАМЕР ГОТОВА. ТКД: {len(CRAMER_CACHE)}")
        # Возвращаем 4 значения для отчета
        return total_switches, len(CRAMER_CACHE), len(ring_map), total_edges
    except Exception as e:
        print(f"!!! КРИТИЧЕСКАЯ ОШИБКА ОБНОВЛЕНИЯ КРАМЕРА: {e}")
        return 0, 0, 0, 0

def save_to_history_sync(data, region_name, message_id):
    print(f">>> СОХРАНЕНИЕ... ID: {message_id}, Регион: {region_name}")
    try:
        try: sheet_history = ss.worksheet(region_name)
        except: 
            print(f">>> Листа {region_name} нет, создаю...")
            try: 
                sheet_history = ss.add_worksheet(title=region_name, rows=1000, cols=10)
                sheet_history.append_row(["Дата", "ФИО", "ТКД", "Адрес", "Виды работ", "Коммент", "СН Дем", "СН Монт", "ID_MSG"])
            except: 
                print("!!! НЕ МОГУ СОЗДАТЬ ЛИСТ !!!")
                return False

        try:
            ids_column = sheet_history.col_values(9)
            if str(message_id) in ids_column:
                print(f"!!! ОТМЕНА: Сообщение {message_id} уже сохранено ранее.")
                return False 
        except Exception as e:
            print(f"Ошибка проверки дубля: {e}")

        row_data = [
            data['report_date'], 
            data.get('master_fio'), 
            data.get('tkd'),
            data.get('address'), 
            data.get('work_types'), 
            data.get('comment'),
            data.get('sn_dem'), 
            data.get('sn_mont'),
            str(message_id)
        ]
        
        # --- ИСПРАВЛЕННАЯ СТРОКА ---
        # Параметр table_range="A:I" заставляет Google Sheets искать конец 
        # таблицы ТОЛЬКО в этих столбцах, полностью игнорируя всё, что правее.
        # value_input_option="USER_ENTERED" гарантирует, что даты и числа не собьются.
        sheet_history.append_row(
            row_data, 
            table_range="A:I", 
            value_input_option="USER_ENTERED"
        )
        # ---------------------------

        print(f">>> УСПЕШНО ЗАПИСАНО! ID: {message_id}")
        return True

    except Exception as e:
        print(f"!!! КРИТИЧЕСКАЯ ОШИБКА СОХРАНЕНИЯ: {e}")
        return False

def get_history_by_tkd_sync(tkd_query, region_list):
    results = []
    for reg in region_list:
        try:
            sheet = ss.worksheet(reg)
            for row in sheet.get_all_values()[1:]:
                if len(row) < 3: continue
                if str(row[2]).strip().lower().startswith(tkd_query.lower()):
                    row.append(reg) 
                    results.append(row)
        except: pass
    return results

def get_report_from_buffer_sync(report_id):
    """Ищет отчет в листе Buffer по уникальному ID"""
    print(f">>> 🔍 Ищу в буфере ID: {report_id}")
    try:
        # Пытаемся открыть лист Buffer
        try:
            sheet = ss.worksheet("Buffer")
        except:
            print("!!! Лист Buffer не найден!")
            return None
        
        # Ищем ячейку, в которой лежит наш ID
        try:
            cell = sheet.find(report_id)
        except:
            cell = None

        if not cell:
            print("!!! ID не найден в таблице.")
            return None
            
        # Данные лежат в той же строке, но в 3-й колонке (JSON_DATA)
        json_data = sheet.cell(cell.row, 3).value
        
        if not json_data:
            return None

        return json.loads(json_data)
        
    except Exception as e:
        print(f"!!! Ошибка чтения из буфера: {e}")
        return None

# --- ХЕНДЛЕРЫ ---

# 1. ПРОВЕРКА ГРУППОВЫХ ЧАТОВ (В самом верху) - УДАЛЕНА, так как создает спам. 
# Бот теперь молчит в группах на текстовые сообщения благодаря фильтрам ниже.

@dp.message(Command("update", "update_cramer"), F.chat.type == "private")
async def cmd_update(message: types.Message, command: CommandObject):
    # Проверяем: это команда /update_cramer ИЛИ у команды /update есть аргумент "cramer"
    is_cramer_update = (command.command == "update_cramer") or (command.args and "cramer" in command.args.lower())
    
    if is_cramer_update:
        await message.answer("🔄 Обновляю базу КРАМЕР (это может занять время)...")
        # Распаковываем 4 значения
        sw_count, tkd_count, rings_count, edges_count = await asyncio.to_thread(reload_cramer_cache_sync)
        await message.answer(
            f"✅ Крамер обновлен:\n"
            f"🔌 Свичей: {sw_count}\n"
            f"🏢 ТКД: {tkd_count}\n"
            f"⭕️ Колец: {rings_count}\n"
            f"🔗 Краев колец: {edges_count}"
        )
    else:
        await message.answer("🔄 Обновляю список ЛЮДЕЙ...")
        # Распаковываем 2 значения
        users_count, areas_count = await asyncio.to_thread(reload_users_cache_sync)
        await message.answer(
            f"✅ Данные обновлены:\n"
            f"👥 Пользователей: {users_count}\n"
            f"🌍 Областей: {areas_count}", 
            reply_markup=get_main_kb()
        )

# НОВАЯ КОМАНДА: /info
@dp.message(Command("info"), F.chat.type == "private")
async def cmd_info(message: types.Message):
    text = (
        "ℹ️ <b>Справка по командам:</b>\n\n"
        "/start - Запустить бота\n"
        "/update - Обновить данные сотрудников\n"
        "/update_cramer - Обновить технические данные\n"
        "/user_info - Список пользователей\n"
        "/info - Эта справка"
    )
    await message.answer(text, parse_mode="HTML")

# ИСПРАВЛЕННАЯ КОМАНДА: /user_info
@dp.message(Command("user_info"), F.chat.type == "private")
async def cmd_user_info(message: types.Message):
    if not USERS_CACHE:
        await asyncio.to_thread(reload_users_cache_sync)
    
    if not USERS_CACHE:
        await message.answer("❌ Список пользователей пуст.")
        return

    # 1. Собираем все уникальные регионы, которые есть у пользователей
    all_regions = set()
    for u in USERS_CACHE.values():
        for r in u.get('Regions', []):
            all_regions.add(r)
    
    # Сортируем регионы по алфавиту
    sorted_regions = sorted(list(all_regions))

    lines = ["<b>Список пользователей:</b>\n"]

    # 2. Идем по КАЖДОМУ региону и ищем пользователей, которые к нему относятся
    for reg in sorted_regions:
        lines.append(f"\n🌍 <b>{reg}</b>")
        
        # Находим всех юзеров, у которых в списке Regions есть этот регион
        users_in_reg = [u for u in USERS_CACHE.values() if reg in u.get('Regions', [])]
        
        # Сортируем их по ФИО для красоты
        users_in_reg.sort(key=lambda x: x.get('ФИО', ''))

        for u in users_in_reg:
            lines.append(f"👤 {u['ФИО']} ({u['Роль']})")

    # Разбиваем на части, если слишком длинное сообщение
    full_text = "\n".join(lines)
    if len(full_text) > 4000:
        for x in range(0, len(full_text), 4000):
            await message.answer(full_text[x:x+4000], parse_mode="HTML")
    else:
        await message.answer(full_text, parse_mode="HTML")

@dp.message(Command("start"), F.chat.type == "private")
async def start_handler(message: types.Message, state: FSMContext, command: CommandObject = None):
    # 1. ПРОВЕРКА: ЕСТЬ ЛИ ДАННЫЕ В ССЫЛКЕ (Гибридный метод для ПК/iOS)
    if command and command.args and command.args.startswith("REPORT_ID_"):
        report_id = command.args.replace("REPORT_ID_", "")
        print(f">>> 📥 Получен ID отчета из /start ссылки: {report_id}")
        
        msg_wait = await message.answer("⏳ Получаю данные отчета...")
        
        # Ищем данные в Гугл Таблице по ID
        report_data = await asyncio.to_thread(get_report_from_buffer_sync, report_id)
        await msg_wait.delete()
        
        if not report_data:
            await message.answer("❌ Ошибка: Отчет не найден или устарел (проверьте лист Buffer).")
            return

        # --- ЛОГИКА ФОРМИРОВАНИЯ ТЕКСТА ---
        user = USERS_CACHE.get(str(message.from_user.id))
        if not user:
            await asyncio.to_thread(reload_users_cache_sync)
            user = USERS_CACHE.get(str(message.from_user.id))
        
        master_name = user['ФИО'] if user else "Неизвестный"
        
        # Достаем поля из JSON
        tkd = report_data.get('tkd', 'Unknown')
        addr = report_data.get('address', '').replace("🏠 ", "").replace("🔄 Поиск адреса...", "")
        work_types = report_data.get('work_types', '')
        comment = report_data.get('comment', '')
        
        def format_sn_list(sn_string):
            if not sn_string: return None
            items = [s.strip() for s in sn_string.split(',') if s.strip()]
            if not items: return None
            return ", ".join([f"<code>{item}</code>" for item in items])

        # Формируем красивый текст
        text = (f"                <b>ОТЧЕТ</b>  - 👤 {master_name}\n\n"
                f"🌐 <code>{tkd}</code>\n"
                f"{f'🏠 {addr}' + chr(10) if addr else ''}"
                f"\n"
                f"🛠 {work_types}")
        
        if comment: text += f"\n💬 {comment}"
        
        sn_dem_formatted = format_sn_list(report_data.get('sn_dem'))
        sn_mont_formatted = format_sn_list(report_data.get('sn_mont'))

        if sn_dem_formatted or sn_mont_formatted: text += "\n"
        if sn_dem_formatted: text += f"\n❌ <b>СН Дем</b>  -  {sn_dem_formatted}"
        if sn_mont_formatted: text += f"\n✅ <b>СН Уст</b>  -  {sn_mont_formatted}"

        # Сохраняем в память (FSM)
        await state.update_data(report_text=text, raw_data=report_data, photos=[], target_region=report_data.get('target_region'))
        
        kb = [[KeyboardButton(text="✅ Отправить в группу")]]
        await message.answer(text, parse_mode="HTML")
        await message.answer("📸 Данные приняты! Прикрепи фото и жми Отправить.", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))
        
        # Переводим в режим ожидания фото
        await state.set_state(Form.waiting_for_photos)
        return

    # 2. ОБЫЧНЫЙ СТАРТ (ЕСЛИ ДАННЫХ НЕТ)
    user = USERS_CACHE.get(str(message.from_user.id))
    
    if not user:
        await asyncio.to_thread(reload_users_cache_sync)
        user = USERS_CACHE.get(str(message.from_user.id))
    
    if user:
        role_text = user.get('Роль', 'Не указана')
        await message.answer(f"Привет, {user['ФИО']} ({role_text})!", reply_markup=get_main_kb())
    else:
        kb = [[KeyboardButton(text="/update")]]
        await message.answer(f"⛔ Тебя нет в базе. Твой ID: {message.from_user.id}\nПопроси добавить тебя в таблицу и нажми /update", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

# --- WEB APP ---

@dp.message(F.text == "📝 Создать отчет", F.chat.type == "private")
async def create_report_click(message: types.Message):
    user = USERS_CACHE.get(str(message.from_user.id))
    if not user:
        await message.answer("⛔ Нет доступа.")
        return

    # --- ПРОВЕРКА РОЛИ ДИСПЕТЧЕРА ---
    if user.get('Роль') == 'Диспетчер':
        await message.answer("⛔ Диспетчерам запрещено создавать отчеты.")
        return
    # --------------------------------

    regions = user.get('Regions', [])
    if not regions:
        await message.answer("⚠️ Нет привязанных регионов.")
        return

    if len(regions) == 1:
        reg = regions[0]
        final_url = f"{WEB_APP_URL}?uid={user['ID ТГ']}&region={reg}&v=2"
        kb = [[KeyboardButton(text=f"🚀 Заполнить: {reg}", web_app=WebAppInfo(url=final_url))], [KeyboardButton(text="🔙 Назад")]]
        await message.answer(f"Отчет для ({reg}):", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))
    else:
        kb_rows = [[InlineKeyboardButton(text=reg, callback_data=f"select_reg:{reg}")] for reg in regions]
        await message.answer("🌍 Выбери регион:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))

@dp.callback_query(F.data.startswith("select_reg:"))
async def region_selected_callback(callback: CallbackQuery):
    reg = callback.data.split(":")[1]
    final_url = f"{WEB_APP_URL}?uid={callback.from_user.id}&region={reg}&v=2"
    kb = [[KeyboardButton(text=f"🚀 Заполнить: {reg}", web_app=WebAppInfo(url=final_url))], [KeyboardButton(text="🔙 Назад")]]
    await callback.message.delete()
    await callback.message.answer(f"Регион: <b>{reg}</b>", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True), parse_mode="HTML")
    await callback.answer()

@dp.message(F.text == "🔙 Назад", F.chat.type == "private")
async def back_handler(message: types.Message):
    await message.answer("Меню", reply_markup=get_main_kb())

# --- УНИВЕРСАЛЬНЫЙ ОБРАБОТЧИК ДАННЫХ ИЗ WEB APP ---
@dp.message(F.content_type == ContentType.WEB_APP_DATA, F.chat.type == "private")
async def web_app_data_handler(message: types.Message, state: FSMContext):
    
    print(f"\n>>> 📨 ПРИШЛИ ДАННЫЕ ИЗ WEB APP! ID: {message.from_user.id}")
    raw_data = message.web_app_data.data
    print(f">>> RAW_DATA: {raw_data}")

    report_data = None

    # 1. Определяем тип данных (ID из буфера или прямой JSON)
    if raw_data.startswith("REPORT_ID_"):
        # Метод для iOS/PC: получили ID, ищем данные в буфере
        report_id = raw_data.replace("REPORT_ID_", "")
        print(f">>> 📥 Получен ID отчета из буфера: {report_id}")
        msg_wait = await message.answer("⏳ Получаю данные из буфера...")
        
        report_data = await asyncio.to_thread(get_report_from_buffer_sync, report_id)
        await msg_wait.delete()
        
        if not report_data:
            await message.answer("❌ Ошибка: Отчет по ID не найден в буфере.")
            return
    else:
        # Метод для Android: получили готовый JSON
        try:
            report_data = json.loads(raw_data)
            print(">>> JSON успешно разобран.")
        except Exception as e:
            print(f"!!! Ошибка обработки JSON: {e}")
            await message.answer("❌ Ошибка обработки данных.")
            return

    # 2. ОБЩАЯ ЛОГИКА ПОСЛЕ ПОЛУЧЕНИЯ ДАННЫХ
    try:
        if 'report_date' not in report_data:
            report_data['report_date'] = datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%d.%m.%Y %H:%M")

        user = USERS_CACHE.get(str(message.from_user.id))
        if not user:
            await asyncio.to_thread(reload_users_cache_sync)
            user = USERS_CACHE.get(str(message.from_user.id))
        
        master_name = user['ФИО'] if user else "Неизвестный"
        
        tkd = report_data.get('tkd', 'Unknown')
        addr = report_data.get('address', '').replace("🏠 ", "").replace("🔄 Поиск адреса...", "")
        work_types = report_data.get('work_types', '')
        comment = report_data.get('comment', '')
        target_region = report_data.get('target_region')

        def format_sn_list(sn_string):
            if not sn_string: return None
            items = [s.strip() for s in sn_string.split(',') if s.strip()]
            if not items: return None
            return ", ".join([f"<code>{item}</code>" for item in items])

        text = (f"                <b>ОТЧЕТ</b>  - 👤 {master_name}\n\n"
                f"🌐 <code>{tkd}</code>\n"
                f"{f'🏠 {addr}' + chr(10) if addr else ''}"
                f"\n"
                f"🛠 {work_types}")
        
        if comment: text += f"\n💬 {comment}"
        
        sn_dem_formatted = format_sn_list(report_data.get('sn_dem'))
        sn_mont_formatted = format_sn_list(report_data.get('sn_mont'))

        if sn_dem_formatted or sn_mont_formatted: text += "\n"
        if sn_dem_formatted: text += f"\n❌ <b>СН Дем</b>  -  {sn_dem_formatted}"
        if sn_mont_formatted: text += f"\n✅ <b>СН Уст</b>  -  {sn_mont_formatted}"

        await state.update_data(report_text=text, raw_data=report_data, photos=[], target_region=target_region)
        
        kb = [[KeyboardButton(text="✅ Отправить в группу")]]
        await message.answer(text, parse_mode="HTML")
        await message.answer("📸 Данные приняты! Прикрепи фото и жми Отправить.", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))
        
        await state.set_state(Form.waiting_for_photos)

    except Exception as e:
        print(f"!!! КРИТИЧЕСКАЯ ОШИБКА в финальной обработке: {e}")
        await message.answer("❌ Произошла критическая ошибка при формировании отчета.")

@dp.message(Form.waiting_for_photos, F.photo, F.chat.type == "private")
async def process_photos(message: types.Message, state: FSMContext):
    d = await state.get_data()
    ph = d.get("photos", [])
    ph.append(message.photo[-1].file_id)
    await state.update_data(photos=ph)

@dp.message(Form.waiting_for_photos, F.text, F.chat.type == "private")
async def send_final_report(message: types.Message, state: FSMContext):
    if message.text != "✅ Отправить в группу": return

    state_data = await state.get_data()
    region_name = state_data.get('target_region')
    target_chat_id = GROUPS_CONFIG.get(region_name)
    
    if not target_chat_id:
        await message.answer(f"⚠️ Ошибка! Не найдена группа для '{region_name}'")
        await state.clear()
        return

    text = state_data.get("report_text")
    photos = state_data.get("photos", [])
    msg_wait = await message.answer("⏳ Отправляю в группу...")

    try:
        sent_msg = None
        if not photos:
            sent_msg = await bot.send_message(target_chat_id, text, parse_mode="HTML")
        else:
            media = [InputMediaPhoto(media=p, caption=text if i==0 else None, parse_mode="HTML") for i, p in enumerate(photos)]
            msgs = await bot.send_media_group(target_chat_id, media[:10])
            sent_msg = msgs[0] # Берем первое сообщение для ссылки
        
        await msg_wait.delete()

        # === ФОРМИРОВАНИЕ ССЫЛКИ НА СООБЩЕНИЕ ===
        chat_id_str = str(target_chat_id)
        # Убираем -100 или - для формирования ссылки вида t.me/c/ID/MSG_ID
        clean_chat_id = chat_id_str.replace("-100", "").replace("-", "")
        msg_link = f"https://t.me/c/{clean_chat_id}/{sent_msg.message_id}"

        # Отправляем кликабельное подтверждение
        await message.answer(f"<a href='{msg_link}'>✅ Отправлено в: <b>{region_name}</b></a>", parse_mode="HTML", reply_markup=get_main_kb())

    except Exception as e:
        await message.answer(f"⚠️ Ошибка отправки: {e}", reply_markup=get_main_kb())
    
    await state.clear()

# --- ПОИСК ---

@dp.message(F.text == "📜 История ТО", F.chat.type == "private")
async def history_btn_click(message: types.Message, state: FSMContext):
    user = USERS_CACHE.get(str(message.from_user.id))
    if not user: return
    
    scope = ", ".join(user['Regions'])
    await message.answer(f"🔎 Введите ТКД ({scope}):", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(Form.waiting_for_tkd_history)

@dp.message(Form.waiting_for_tkd_history, F.chat.type == "private")
async def process_history_search(message: types.Message, state: FSMContext):
    user = USERS_CACHE.get(str(message.from_user.id))
    if not user: return
    
    query = message.text.strip()
    wait_msg = await message.answer("⏳ Ищу...")
    rows = await asyncio.to_thread(get_history_by_tkd_sync, query, user['Regions'])
    await wait_msg.delete()

    if not rows:
        await message.answer(f"❌ Пусто: {query}", reply_markup=get_main_kb())
    else:
        for row in rows:
            # row[0]=Дата, row[1]=ФИО, row[2]=ТКД, row[4]=Работы, row[5]=Коммент, row[6]=СН Дем, row[7]=СН Монт
            
            # --- ИСПРАВЛЕННАЯ ФУНКЦИЯ КОПИРОВАНИЯ ---
            def make_copy(raw_sn):
                if not raw_sn: return ""
                # 1. Заменяем запятые на пробелы (чтобы разделить, если в базе записано "SN1, SN2")
                clean_str = raw_sn.replace(',', ' ')
                # 2. Разбиваем строку по пробелам на список (удаляет лишние пустоты)
                parts = clean_str.split()
                # 3. Оборачиваем КАЖДЫЙ кусочек отдельно в <code>
                code_parts = [f"<code>{p}</code>" for p in parts]
                # 4. Соединяем их пробелом
                return " ".join(code_parts)

            sn_dem_str = make_copy(row[6])
            sn_mont_str = make_copy(row[7])

            # Собираем сообщение по новой структуре
            txt = (f"📅 {row[0]} | 👤 {row[1]}\n"
                   f"🌐 <code>{row[2]}</code>\n"
                   f"🛠 {row[4]}")
            
            if row[5]: 
                txt += f"\n💬 {row[5]}"
            
            if sn_dem_str:
                txt += f"\n❌ {sn_dem_str}"
            if sn_mont_str:
                txt += f"\n✅ {sn_mont_str}"

            # Отправляем с включенным HTML
            await message.answer(txt, parse_mode="HTML")
            
        await message.answer("✅ Готово.", reply_markup=get_main_kb())
    await state.clear()

# --- РЕАКЦИИ ---

@dp.message_reaction()
async def reaction_handler(reaction: types.MessageReactionUpdated):
    print(f">>> РЕАКЦИЯ! Чат: {reaction.chat.id}, Юзер: {reaction.user.id}")
    
    region_name = GROUPS_MAP_REVERSE.get(reaction.chat.id)
    if not region_name: 
        print(f">>> Игнор: Чат {reaction.chat.id} не в конфиге.")
        return

    new_emojis = [r.emoji for r in reaction.new_reaction if hasattr(r, "emoji")]
    
    if not any(e in ["👍", "✅", "💯", "👌"] for e in new_emojis): 
        print(f">>> Игнор: Смайл {new_emojis} не подходит.")
        return

    user = USERS_CACHE.get(str(reaction.user.id))
    if not user: 
        await asyncio.to_thread(reload_users_cache_sync)
        user = USERS_CACHE.get(str(reaction.user.id))
    
    user_role = user.get('Роль', 'Нет') if user else 'Нет'
    print(f">>> Кто лайкнул: {user.get('ФИО', 'Unknown')} | Роль: {user_role}")

    if not user or user_role not in ['Диспетчер', 'Админ', 'Администратор']: 
        print(">>> ОТКАЗ: Нет прав.")
        return

    # 1. СОХРАНЕНИЕ ОТЧЕТА (Без изменений)
    if any(e in ["👍", "✅"] for e in new_emojis):
        try:
            msg = await bot.forward_message(reaction.user.id, reaction.chat.id, reaction.message_id)
            if not msg.forward_from or msg.forward_from.id != BOT_ID:
                print(">>> Игнор: Лайкнули сообщение не от бота.")
                await bot.delete_message(reaction.user.id, msg.message_id)
                return
            
            text = msg.caption or msg.text or ""
            await bot.delete_message(reaction.user.id, msg.message_id)
            
            def get_val(marker):
                for l in text.split('\n'):
                    if marker in l: return l.split(marker)[1].split(' - ',1)[-1].strip()
                return ""
                # Умная конвертация времени
            msg_time = msg.forward_date or msg.date
            local_time = msg_time.astimezone(ZoneInfo("Europe/Kyiv"))

            data = {
                'report_date': local_time.strftime("%d.%m.%Y %H:%M"),
                'master_fio': get_val('👤'),
                'tkd': get_val('🌐'),
                'address': get_val('🏠'),
                'work_types': get_val('🛠'),
                'comment': get_val('💬'),
                'sn_dem': get_val('❌').replace('`','').replace(',',''), 
                'sn_mont': get_val('✅').replace('`','').replace(',','')
            }
            
            print(f">>> Сохраняю отчет ID {reaction.message_id}...")
            saved = await asyncio.to_thread(save_to_history_sync, data, region_name, reaction.message_id)
            
            if saved:
                await bot.set_message_reaction(reaction.chat.id, reaction.message_id, reaction=[ReactionTypeEmoji(emoji="⚡")])
                print(">>> УСПЕХ: Молния поставлена.")
            else:
                await bot.set_message_reaction(reaction.chat.id, reaction.message_id, reaction=[ReactionTypeEmoji(emoji="👀")])
                print(">>> ДУБЛЬ: Глаза поставлены.")
                
        except Exception as e:
            print(f"!!! ОШИБКА В РЕАКЦИИ: {e}")

    # 2. TECH INFO (С ВОССТАНОВЛЕНИЕМ КОПИРОВАНИЯ)
    if any(e in ["💯", "👌"] for e in new_emojis):
        print(f">>> TECH INFO REQUEST! Чат: {reaction.chat.id}")
        try:
            temp_msg = await bot.forward_message(reaction.user.id, reaction.chat.id, reaction.message_id)
            content_text = temp_msg.caption or temp_msg.text or ""
            await bot.delete_message(reaction.user.id, temp_msg.message_id)

            # --- Ищем MDU для логики поиска ---
            match_candidate = re.search(r"🌐\s+(MDU_[A-Za-z]{3}\d{5}_\d{4,5}(?:_\d)?)", content_text)
            
            if not match_candidate:
                print(">>> Игнор: Не найден шаблон '🌐 MDU_...'")
                return
            
            found_name = match_candidate.group(1).strip()
            print(f">>> Найден объект: {found_name}")

            target_tkd = None
            target_switch_name_to_find = None 
            is_switch_mode = False

            if re.search(r"_\d$", found_name):
                is_switch_mode = True
                target_switch_name_to_find = found_name
                target_tkd = found_name.rsplit('_', 1)[0]
                print(f">>> Режим СВИЧ. Ищем: {target_switch_name_to_find} в ТКД {target_tkd}")
            else:
                is_switch_mode = False
                target_tkd = found_name
                print(f">>> Режим ТКД. Ищем ТКД: {target_tkd}")

            # ЗАПРОС В БАЗУ
            tkd_data = CRAMER_CACHE.get(target_tkd)
            if not tkd_data:
                print(f">>> Нет данных в Крамере по ТКД: {target_tkd}")
                return 
            
            switches_to_show = []
            
            if is_switch_mode:
                for sw in tkd_data['switches']:
                    if sw['name'] == target_switch_name_to_find:
                        switches_to_show.append(sw)
                        break 
            else:
                switches_to_show = tkd_data['switches']

            if not switches_to_show and not tkd_data['uplinks']:
                 print(">>> Пустые данные")
                 return

            # ФОРМИРОВАНИЕ БЛОКА ССЫЛОК
            html_block = "\n\n"
            def make_links(ip):
                return (f"<a href='http://msu2.kyivstar.ua/partner.portal/src/util_info.php?getinfo&ip={ip}'>Get info</a> | "
                        f"<a href='http://msu2.kyivstar.ua/partner.portal/src/lldp.php?ip={ip}'>LLDP</a> | "
                        f"<a href='http://msu2.kyivstar.ua/partner.portal/src/util_info.php?gethistory&ip={ip}'>History</a>")

            for sw in switches_to_show:
                suffix = sw['name'].split('_')[-1]
                label = f"🌐_{suffix}" if suffix.isdigit() else "🌐"
                html_block += (f"<b>{label}:</b> <code>{sw['ip']}</code> 🔗 {make_links(sw['ip'])}\n")
            
            for i, ds_ip in enumerate(tkd_data['uplinks'], 1):
                html_block += (f"<b>ds{i}:</b> <code>{ds_ip}</code> 🔗 {make_links(ds_ip)}\n")

            html_block = html_block.strip()

            # ПРОВЕРКА НА ДУБЛИРОВАНИЕ
            if "Get info" in content_text:
                print(">>> Инфо уже добавлено")
                return

            # === ВОССТАНОВЛЕНИЕ ФОРМАТИРОВАНИЯ (ТКД и СН) ===
            # 1. Заголовок
            content_text = re.sub(r"(^\s*)(ОТЧЕТ)(\s+-\s+👤)", r"\1<b>\2</b>\3", content_text, flags=re.MULTILINE)
            
            # 2. Делаем имя ТКД/Свича (MDU_...) снова копируемым
            content_text = re.sub(r"(🌐\s+)(MDU_[A-Za-z]{3}\d{5}_\d{4,5}(?:_\d)?)", r"\1<code>\2</code>", content_text)

            # 3. Восстанавливаем СН (Жирный заголовок + Код значения)
            content_text = re.sub(r"(❌\s*СН Дем\s*-\s*)(.*)", r"❌ <b>СН Дем</b> - <code>\2</code>", content_text)
            content_text = re.sub(r"(✅\s*СН Уст\s*-\s*)(.*)", r"✅ <b>СН Уст</b> - <code>\2</code>", content_text)
            # ===============================================

            new_text = content_text + "\n\n" + html_block
            
            if temp_msg.caption:
                await bot.edit_message_caption(
                    chat_id=reaction.chat.id, 
                    message_id=reaction.message_id, 
                    caption=new_text, 
                    parse_mode="HTML"
                )
            else:
                await bot.edit_message_text(
                    text=new_text,
                    chat_id=reaction.chat.id, 
                    message_id=reaction.message_id, 
                    parse_mode="HTML"
                )
            
            print(">>> Сообщение успешно обновлено тех. данными")
            
        except Exception as e:
            print(f"!!! ОШИБКА В TECH INFO: {e}")

# Обработчик неизвестных команд (в самом конце)
@dp.message()
async def unknown_command_handler(message: types.Message):
    # Игнорируем сообщения в группах, чтобы не спамить
    if message.chat.type in ['group', 'supergroup']:
        return
    # Если сообщения нет (стикер) ИЛИ это текст без слэша — игнорируем
    if not message.text or not message.text.startswith('/'):
        return
        
    await message.answer("Я не понимаю эту команду. Используйте /info для справки.")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    # === ОЧИЩЕННОЕ МЕНЮ (ТОЛЬКО 2 КОМАНДЫ) ===
    commands = [
        BotCommand(command="start", description="🚀 Старт"),
        BotCommand(command="update", description="Обновить данные")
    ]
    await bot.set_my_commands(commands)
    
    print("🚀 БОТ ЗАПУЩЕН! ЖДУ...")
    reload_users_cache_sync()
    reload_cramer_cache_sync()
    
    await dp.start_polling(bot, allowed_updates=["message", "message_reaction", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())